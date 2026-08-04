"""The whole dashboard backend, end to end with fakes: start a live run over the
API, watch the SSE stream reach awaiting_approval, approve, confirm execution and
recovery, then replay the finished run and confirm the journaled stream matches."""
from __future__ import annotations

import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.api.livelab.router import make_livelab_router
from tests.unit.test_livelab_machine import make_deps


def wait_until(pred, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")


def sse_events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("event: "):
            current = line.removeprefix("event: ")
        elif line.startswith("data: ") and current is not None:
            events.append((current, json.loads(line.removeprefix("data: "))))
            current = None
    return events


def test_live_run_then_replay(tmp_path) -> None:
    deps, world = make_deps(tmp_path)
    app = FastAPI()
    app.include_router(make_livelab_router(out_root=tmp_path,
                                           deps_factory=lambda run_dir: deps))
    client = TestClient(app)

    # -- live run ------------------------------------------------------------
    run_id = client.post("/live/runs", json={"target": "shipping", "preset": "quick"}).json()["run_id"]
    wait_until(lambda: client.get(f"/live/runs/{run_id}").json()["phase"] == "awaiting_approval")

    snap = client.get(f"/live/runs/{run_id}").json()
    assert snap["report"]["root_cause_service"] == "shipping"
    assert snap["action"]["status"] == "posted"
    assert snap["action"]["action"]["kind"] == "restart"

    assert client.post(f"/live/runs/{run_id}/approve", json={"approver": "sid"}).status_code == 200
    wait_until(lambda: client.get(f"/live/runs/{run_id}").json()["phase"] == "done")

    with client.stream("GET", f"/live/runs/{run_id}/stream") as resp:
        events = sse_events("".join(resp.iter_text()))

    kinds = [e for e, _ in events]
    phases = [d["phase"] for e, d in events if e == "phase"]
    assert phases == ["preflight", "booting", "baseline", "injecting", "soak",
                      "investigating", "report", "awaiting_approval", "executing",
                      "recovering", "done"]
    assert "agent" in kinds and "report" in kinds and "recovery" in kinds
    action_states = [d["status"] for e, d in events if e == "action"]
    assert action_states == ["posted", "approved", "execute_result"]
    assert [e for e, _ in events][-1] == "done"

    # the injected fault was applied and swept; the executor really "restarted"
    assert world.injected == [("shipping", 3)]
    assert "shipping" in world.cleared

    # the action journal audit invariant holds: no unapproved executions
    from sentinel.actions.journal import ActionJournal
    states = ActionJournal(tmp_path / run_id / "actions.jsonl").fold()
    assert all(s.status in ("done",) for s in states.values())
    assert all(s.approver == "sid" for s in states.values())

    # -- replay of that exact run -------------------------------------------
    replay_id = client.post(f"/live/replays/{run_id}").json()["run_id"]
    wait_until(lambda: client.get("/live/status").json()["run"] is None)

    with client.stream("GET", f"/live/runs/{replay_id}/stream") as resp:
        replay_events = sse_events("".join(resp.iter_text()))

    live_payloads = [(e, d) for e, d in events if e != "done"]
    replay_payloads = [(e, d) for e, d in replay_events if e != "done"]
    assert replay_payloads == live_payloads
    assert client.get(f"/live/runs/{replay_id}").json()["mode"] == "replay"
