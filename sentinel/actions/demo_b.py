"""Option B closed-loop demo on the live OTel Demo.

phase 1 (this script): inject a real fault (flagd flag) -> build a brief proposing
remove_impairment -> journal proposed -> post the Slack card -> print the approve-listener cmd.

phase 2 (you): run the printed socketmode --executor live command, click Approve in Slack.
The LiveExecutor clears the flag (real state change), polls the health metric, and posts the
before/after recovery to the thread.

  python -m sentinel.actions.demo_b --inject        # inject + post the brief
  python -m sentinel.actions.demo_b --status        # show current lag + flag state
  python -m sentinel.actions.demo_b --reset         # clear flags manually (cleanup)

Self-contained: uses the demo's local Prometheus + flagd, no New Relic, no model cost.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path

import httpx

from sentinel.actions.journal import ActionJournal
from sentinel.actions.models import SuggestedAction
from sentinel.actions.slack import post_brief
from sentinel.actions.brief import Brief

_PROM = os.environ.get("SENTINEL_DEMO_PROM", "http://localhost:9090")
_FLAGD = os.environ.get("SENTINEL_OTEL_FLAGD_UI_BASE_URL", "http://localhost:8081/feature")
_JOURNAL = "runs/actions/server.jsonl"

# the demo incident: a kafka queue backlog whose recovery is visible in local Prometheus
_FLAG = "kafkaQueueProblems"
_HEALTH_QUERY = "sum(kafka_consumer_records_lag_avg)"
_TARGET = "kafka"


def _lag() -> float | None:
    try:
        r = httpx.get(f"{_PROM}/api/v1/query", params={"query": _HEALTH_QUERY}, timeout=10).json()
        res = r.get("data", {}).get("result", [])
        return sum(float(m["value"][1]) for m in res) if res else 0.0
    except Exception:
        return None


def _set_flag(variant: str) -> None:
    from labs.otel.flagd import FlagdClient
    FlagdClient(_FLAGD).set_flag_variant(_FLAG, variant)


def inject() -> dict:
    _set_flag("on")
    action = SuggestedAction(
        id=str(uuid.uuid4()), kind="remove_impairment", effect="mutate", target_service=_TARGET,
        params={}, description="Clear the Kafka queue impairment so consumers drain the backlog.",
        risk="low", reversible=True,
        citations=["kafka_consumer_records_lag_avg rising: fraud-detection consumer stalled at onset"],
        preview=f"reset flagd flag {_FLAG} -> off",
    ).with_hash()
    brief = Brief(
        run_id="otel-kafka-live", symptom="Order processing delayed: Kafka consumer lag climbing.",
        root_cause_service=_TARGET, fault_type="queue backlog", graph_source="live",
        ranked=[{"service": "kafka", "evidence": ["consumer records lag rising since onset"],
                 "observed_signatures": {"latency": True}}],
        primary_action=action, alternatives=[], confident=True,
    )
    j = ActionJournal(_JOURNAL)
    ttl = float(os.environ.get("ACTION_APPROVAL_TTL_S", "3600"))
    j.proposed(action, expires_at=time.time() + ttl)
    res = post_brief(brief)
    if res.get("ok"):
        j.posted(action.id, "slack", res.get("ts", ""))
    return {"action_id": action.id, "flag": _FLAG, "posted": res, "lag_now": _lag()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Option B live remediation demo (OTel Demo).")
    ap.add_argument("--inject", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args(argv)

    if args.reset:
        from labs.otel.flagd import FlagdClient
        FlagdClient(_FLAGD).reset_all_flags("off")
        print("flags reset to off; lag now:", _lag())
        return 0
    if args.status:
        print(json.dumps({"lag": _lag(), "flag": _FLAG}, indent=2))
        return 0
    if args.inject:
        out = inject()
        print(json.dumps(out, indent=2))
        print("\nNow run the live approval listener, then click Approve in Slack:")
        print(f"  set -a; source .env; set +a; PYTHONPATH=. .venv/bin/python -m sentinel.actions.socketmode "
              f"--executor live --health-query '{_HEALTH_QUERY}' --journal {_JOURNAL} --max-events 1")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
