"""Run the tool eval over the recorded scenarios and print a scored report.

Usage:
    python -m sentinel_tool_eval.run                # all scenarios
    python -m sentinel_tool_eval.run payment_failure_001   # one scenario
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

import anthropic

import sentinel.tools  # noqa: F401  (populate the registry)
from sentinel_tool_eval.aggregate import aggregate, format_report
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
    print(
        f"  stop={r.stop} iterations={r.iterations} tool_calls={r.call_count} "
        f"(manager={len(r.calls)} workers={len(r.worker_calls)}) subagents={r.subagents} "
        f"tool_errors={r.tool_errors} hook_denials={r.denials}"
    )
    print(f"  manager_calls={dict(Counter(r.calls))}")
    if r.worker_calls:
        print(f"  worker_calls={dict(Counter(r.worker_calls))}")
    if r.internal_calls:
        print(f"  internal_tool_calls={r.internal_call_count} "
              f"internal_calls={dict(Counter(r.internal_calls))}")
    if r.findings:
        for f in r.findings:
            print(f"    finding: {f.get('service')} is_origin={f.get('is_origin')} "
                  f"fault={f.get('fault_type')} suspect={f.get('suspect_change_id')} conf={f.get('confidence')}")
    mu, wu = r.usage, r.worker_usage
    print(
        f"  tokens manager(opus): in={mu.get('input', 0)} out={mu.get('output', 0)} cache_read={mu.get('cache_read', 0)} | "
        f"workers(sonnet): in={wu.get('input', 0)} out={wu.get('output', 0)} cache_read={wu.get('cache_read', 0)} | "
        f"est_cost=${r.est_cost_usd:.4f}"
    )
    if r.feedback:
        print(f"  feedback:\n    " + r.feedback.replace("\n", "\n    "))


def _result_json(result: TaskResult) -> dict:
    return {
        "scenario_id": result.scenario_id,
        "grade": result.grade,
        "report": result.report,
        "calls": result.calls,
        "worker_calls": result.worker_calls,
        "internal_calls": result.internal_calls,
        "subagents": result.subagents,
        "findings": result.findings,
        "tool_errors": result.tool_errors,
        "iterations": result.iterations,
        "stop": result.stop,
        "usage": result.usage,
        "worker_usage": result.worker_usage,
        "feedback": result.feedback,
        "denials": result.denials,
    }


def _run_record(result: TaskResult) -> dict:
    g = result.grade
    return {
        "correct": g.get("correct"),
        "location_correct": g.get("location_correct"),
        "culprit_correct": g.get("culprit_correct"),
        "decoys_ruled_out": g.get("decoys_ruled_out"),
        "type_match": g.get("type_match"),
        "cost": result.est_cost_usd,
        "calls": result.call_count,
    }


def main() -> int:
    if not load_api_key():
        print("ANTHROPIC_API_KEY not found in environment or .env", file=sys.stderr)
        return 2

    wanted = set(sys.argv[1:])
    scenarios = [s for s in SCENARIOS if not wanted or s.id in wanted]
    if not scenarios:
        print(f"no matching scenarios; known: {[s.id for s in SCENARIOS]}", file=sys.stderr)
        return 2

    repeats = int(os.environ.get("SENTINEL_EVAL_REPEATS", "1"))
    client = anthropic.Anthropic()
    RESULTS_DIR.mkdir(exist_ok=True)

    all_results: list[TaskResult] = []
    runs_by_scenario: dict[str, list[dict]] = {}
    for scenario in scenarios:
        runs_by_scenario[scenario.id] = []
        for i in range(repeats):
            label = scenario.id + (f" (run {i + 1}/{repeats})" if repeats > 1 else "")
            print(f"\nrunning {label} ...", flush=True)
            result = run_task(client, scenario)
            all_results.append(result)
            _print_task(result)
            suffix = f".run{i + 1}" if repeats > 1 else ""
            (RESULTS_DIR / f"{scenario.id}{suffix}.json").write_text(
                json.dumps(_result_json(result), indent=2), encoding="utf-8"
            )
            runs_by_scenario[scenario.id].append(_run_record(result))

    correct = sum(1 for r in all_results if r.grade.get("correct"))
    total_cost = sum(r.est_cost_usd for r in all_results)
    print("\n" + "=" * 48)
    print(f"accuracy: {correct}/{len(all_results)} correct ({len(scenarios)} scenarios x {repeats} run(s))")
    print(f"total est_cost=${total_cost:.4f}")

    if repeats > 1:
        report = aggregate(runs_by_scenario)
        print(format_report(report))
        (RESULTS_DIR / "_reliability.json").write_text(
            json.dumps(
                {
                    "per_scenario": [vars(s) for s in report.per_scenario],
                    "micro_pass_rate": report.micro_pass_rate,
                    "pass_at_k": report.pass_at_k,
                    "pass_hat_k": report.pass_hat_k,
                    "dimension_rates": report.dimension_rates,
                    "mean_cost": report.mean_cost,
                    "mean_calls": report.mean_calls,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
