"""Components 6+7: inbound authentication, web-fallback end-to-end, two-phase flow,
and the eval-isolation invariant."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from sentinel.actions.api import make_action_router
from sentinel.actions.journal import ActionJournal
from sentinel.actions.report import build_report
from sentinel.actions.run import propose_from_result
from sentinel.actions.verify import sign, verify_slack


def _result(root="emailservice", fault="resource saturation", supported=True):
    return {
        "run_id": "demo1", "symptom": "email latency up",
        "root_cause_service": root, "ranked_services": [root, "recommendationservice"],
        "synthesis": {"root_cause_service": root, "ranked_services": [root], "fault_type": fault,
                      "justification": "cpu rose"},
        "graph_source": "static",
        "verdicts": [{"candidate_service": root, "supported": supported,
                      "root_cause_service": root if supported else None, "signature": "resource",
                      "observed_signatures": {"resource": True, "latency": True, "error": False},
                      "evidence": ["cpu 0.27->18.68 at onset"]}],
    }


# -- inbound authentication (the trust boundary) ---------------------------------------
def test_slack_signature_valid_and_forged():
    secret, ts, body = "shh", str(int(time.time())), b"payload=%7B%7D"
    good = sign(secret, ts, body)
    assert verify_slack(secret, ts, good, body) is True
    assert verify_slack(secret, ts, "v0=deadbeef", body) is False          # forged
    assert verify_slack(secret, ts, good, body + b"x") is False            # tampered body
    assert verify_slack(None, ts, good, body) is False                     # fail closed, no secret
    old = str(int(time.time()) - 10_000)
    assert verify_slack(secret, old, sign(secret, old, body), body) is False  # stale timestamp


def test_slack_route_rejects_forged(tmp_path, monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shh")
    j = ActionJournal(tmp_path / "j.jsonl")
    from fastapi import FastAPI
    app = FastAPI(); app.include_router(make_action_router(j))
    c = TestClient(app)
    r = c.post("/slack/interact", content=b"payload=%7B%7D",
               headers={"X-Slack-Request-Timestamp": str(int(time.time())),
                        "X-Slack-Signature": "v0=forged"})
    assert r.json()["ok"] is False   # rejected, nothing journaled


# -- web fallback end to end (same journal, same gate) ---------------------------------
def test_web_fallback_approve_executes(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTION_WEB_SECRET", "web")
    monkeypatch.setenv("SLACK_DRY_RUN", "1")
    j = ActionJournal(tmp_path / "j.jsonl")
    out = propose_from_result(_result(), j, surface="web")
    primary = out["primary"]
    assert primary is not None

    from fastapi import FastAPI
    app = FastAPI(); app.include_router(make_action_router(j))
    c = TestClient(app)
    hdr = {"X-Action-Secret": "web"}
    assert c.get("/actions/pending", headers=hdr).json()["pending"]        # listed
    assert c.get("/actions/pending").json()["ok"] is False                 # unauth rejected
    assert c.post(f"/actions/{primary}/approve", headers=hdr).json()["ok"] is True
    # background thread executes; give it a moment
    for _ in range(50):
        if j.state_of(primary) and j.state_of(primary).status in ("done", "failed"):
            break
        time.sleep(0.05)
    assert j.state_of(primary).status == "done"
    rep = build_report(j)
    assert rep["safe"] is True and rep["executed"] == 1 and rep["unapproved_executions"] == []


def test_notify_only_result_proposes_no_mutate(tmp_path):
    j = ActionJournal(tmp_path / "j.jsonl")
    out = propose_from_result(_result(supported=False), j, surface="web")   # not confident
    assert out["primary"] is None
    assert out["confident"] is False


# -- eval isolation: the scored paths must not import the action half ------------------
def test_eval_paths_do_not_import_actions():
    import importlib
    import pkgutil

    import sentinel.oss as oss_pkg
    import sentinel.agent as agent_pkg
    offenders = []
    for pkg in (oss_pkg, agent_pkg):
        for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            src = importlib.import_module(m.name).__file__
            if src and "sentinel.actions" in Path(src).read_text():
                offenders.append(m.name)
    assert offenders == [], f"eval path imports sentinel.actions: {offenders}"
