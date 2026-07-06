"""Run the agent against a live New Relic incident and grade it.

Usage:
    python -m sentinel_tool_eval.live runs/live/kafka_queue_problems_live_001_<ts>

Reads run_meta.json (public context) and truth.json (eval-only) from the run
directory produced by labs.otel.live_incident, builds a NewRelicStore over the
incident window, runs the same manager orchestration as the fixture eval, and
grades with the unchanged grader.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import anthropic

import sentinel.tools  # noqa: F401  (populate the registry)
from sentinel.fixtures.schemas import DerivedAlert, PrivateTruth
from sentinel.newrelic.client import NerdGraphClient
from sentinel.newrelic.store import NewRelicStore
from sentinel_tool_eval.env import load_api_key
from sentinel_tool_eval.harness import DEFAULT_TOOL_MODE, TaskResult, run_task_with
from sentinel_tool_eval.run import _print_task, _result_json


def load_run(run_dir: Path) -> tuple[dict, PrivateTruth]:
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    truth = PrivateTruth.model_validate_json((run_dir / "truth.json").read_text(encoding="utf-8"))
    return meta, truth


def build_live_prompt(meta: dict) -> str:
    window_s = (meta["window_end_ms"] - meta["window_start_ms"]) // 1000
    lines = [
        "You are paged for an incident in a microservices application.",
        "",
        f"Symptom: {meta['symptom']}",
        "",
        "Firing alerts:",
    ]
    for alert in meta["alerts"]:
        lines.append(
            f"- {alert['alertname']} ({alert['severity']}) at second {alert['starts_at_second']}: "
            f"{alert.get('annotations', {}).get('summary', '')}"
        )
    lines += [
        "",
        f"Recording window: seconds 0 to {window_s}.",
        "",
        "Investigate the recorded telemetry and determine the root cause: which service "
        "or which caller->callee dependency is at fault, and the failure type. Identify "
        "which recent change caused it and rule out the others. Submit your conclusion "
        "with the report tool.",
    ]
    return "\n".join(lines)


def run_live_eval(client: anthropic.Anthropic, run_dir: Path, *, tool_mode: str = DEFAULT_TOOL_MODE) -> TaskResult:
    meta, truth = load_run(run_dir)
    store = NewRelicStore(
        NerdGraphClient.from_env(),
        window_start_ms=meta["window_start_ms"],
        window_end_ms=meta["window_end_ms"],
        alerts=[DerivedAlert.model_validate(a) for a in meta["alerts"]],
    )
    return run_task_with(
        client, store, truth, build_live_prompt(meta), meta["scenario_id"], tool_mode=tool_mode
    )


def main() -> int:
    if not load_api_key():
        print("ANTHROPIC_API_KEY not found in environment or .env", file=sys.stderr)
        return 2
    if len(sys.argv) != 2:
        print("usage: python -m sentinel_tool_eval.live <run_dir>", file=sys.stderr)
        return 2
    run_dir = Path(sys.argv[1])
    result = run_live_eval(anthropic.Anthropic(), run_dir)
    _print_task(result)
    (run_dir / "result.json").write_text(
        json.dumps(_result_json(result), indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
