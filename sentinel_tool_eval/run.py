"""Run the tool eval over the recorded scenarios and print a scored report.

Usage:
    python -m sentinel_tool_eval.run                # all scenarios
    python -m sentinel_tool_eval.run payment_failure_001   # one scenario
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import anthropic

import sentinel.tools  # noqa: F401  (populate the registry)
from sentinel_tool_eval.env import load_api_key
from sentinel_tool_eval.harness import TaskResult, run_task
from sentinel_tool_eval.tasks import SCENARIOS

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _print_task(r: TaskResult) -> None:
    g = r.grade
    verdict = "CORRECT" if g.get("correct") else "WRONG"
    print(f"\n=== {r.scenario_id}: {verdict} ===")
    print(
        f"  location={g.get('location_correct')} culprit={g.get('culprit_correct')} "
        f"decoys_ruled_out={g.get('decoys_ruled_out')} type_match={g.get('type_match')}"
    )
    if r.report:
        rc = r.report.get("root_cause", {})
        print(
            f"  reported: {rc} culprit={r.report.get('culprit_change_id')} "
            f"ruled_out={r.report.get('ruled_out_change_ids')}"
        )
    seq = Counter(r.calls)
    print(f"  stop={r.stop} iterations={r.iterations} tool_calls={r.call_count} tool_errors={r.tool_errors}")
    print(f"  call_mix={dict(seq)}")
    print(f"  call_sequence={r.calls}")
    u = r.usage
    print(
        f"  tokens: input={u.get('input', 0)} output={u.get('output', 0)} "
        f"cache_read={u.get('cache_read', 0)} cache_write={u.get('cache_write', 0)} "
        f"est_cost=${r.est_cost_usd:.4f}"
    )
    if r.feedback:
        print(f"  feedback:\n    " + r.feedback.replace("\n", "\n    "))


def main() -> int:
    if not load_api_key():
        print("ANTHROPIC_API_KEY not found in environment or .env", file=sys.stderr)
        return 2

    wanted = set(sys.argv[1:])
    scenarios = [s for s in SCENARIOS if not wanted or s.id in wanted]
    if not scenarios:
        print(f"no matching scenarios; known: {[s.id for s in SCENARIOS]}", file=sys.stderr)
        return 2

    client = anthropic.Anthropic()
    RESULTS_DIR.mkdir(exist_ok=True)

    results: list[TaskResult] = []
    for scenario in scenarios:
        print(f"\nrunning {scenario.id} ...", flush=True)
        result = run_task(client, scenario)
        results.append(result)
        _print_task(result)
        (RESULTS_DIR / f"{scenario.id}.json").write_text(
            json.dumps(
                {
                    "scenario_id": result.scenario_id,
                    "grade": result.grade,
                    "report": result.report,
                    "calls": result.calls,
                    "tool_errors": result.tool_errors,
                    "iterations": result.iterations,
                    "stop": result.stop,
                    "usage": result.usage,
                    "feedback": result.feedback,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    correct = sum(1 for r in results if r.grade.get("correct"))
    total_cost = sum(r.est_cost_usd for r in results)
    total_out = sum(r.usage.get("output", 0) for r in results)
    total_in = sum(r.usage.get("input", 0) for r in results)
    total_cache_read = sum(r.usage.get("cache_read", 0) for r in results)
    print("\n" + "=" * 48)
    print(f"accuracy: {correct}/{len(results)} correct")
    print(
        f"tokens total: input={total_in} output={total_out} cache_read={total_cache_read} "
        f"est_cost=${total_cost:.4f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
