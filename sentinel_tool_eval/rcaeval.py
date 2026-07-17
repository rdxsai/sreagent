"""Run Sentinel over converted RCAEval cases and score AC@1 localization.

Reuses the store-agnostic run_task_with with a location-only grader. Cases are
converted public fixtures under rcaeval/converted/<case_id>/.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import anthropic

import sentinel.tools  # noqa: F401  (registers the tool namespaces as a side effect)
from sentinel.tools.store import FixtureStore
from sentinel_rcaeval.case import parse_case_name
from sentinel_rcaeval.truth import FAULT_CATEGORY, RCAEvalTruth
from sentinel_tool_eval.harness import TaskResult, run_task_with
from sentinel_tool_eval.rcaeval_grader import grade_localization
from sentinel_tool_eval.tasks import Scenario, build_task_prompt


def discover_cases(converted_root: Path, slice_ids: list[str] | None = None) -> list[Scenario]:
    root = Path(converted_root)
    dirs = sorted(p for p in root.iterdir() if (p / "public").is_dir())
    if slice_ids:
        wanted = set(slice_ids)
        dirs = [p for p in dirs if p.name in wanted]
    return [
        Scenario(id=p.name, public_dir=p / "public", truth_path=p / "eval_only" / "truth.json")
        for p in dirs
    ]


def _bump(table: dict, key: str, hit: bool) -> None:
    cell = table.setdefault(key, {"hits": 0, "n": 0, "ac1": 0.0})
    cell["n"] += 1
    cell["hits"] += 1 if hit else 0
    cell["ac1"] = cell["hits"] / cell["n"]


def aggregate_scorecard(graded: list[tuple[str, dict]]) -> dict:
    by_system: dict[str, dict] = {}
    by_fault: dict[str, dict] = {}
    by_category: dict[str, dict] = {}
    hits = 0
    for case_id, grade in graded:
        system, _service, fault, _instance = parse_case_name(case_id)
        hit = bool(grade.get("correct"))
        hits += 1 if hit else 0
        _bump(by_system, system, hit)
        _bump(by_fault, fault, hit)
        _bump(by_category, FAULT_CATEGORY.get(fault, "unknown"), hit)
    n = len(graded)
    return {
        "overall_ac1": (hits / n) if n else 0.0,
        "n": n,
        "hits": hits,
        "by_system": by_system,
        "by_fault": by_fault,
        "by_category": by_category,
    }


def run_case(client: anthropic.Anthropic, scenario: Scenario) -> TaskResult:
    truth = RCAEvalTruth.model_validate_json(scenario.truth_path.read_text(encoding="utf-8"))
    store = FixtureStore(scenario.public_dir)
    prompt = build_task_prompt(scenario)
    return run_task_with(client, store, truth, prompt, scenario.id, grader=grade_localization)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Sentinel over converted RCAEval cases.")
    parser.add_argument("--converted-root", default="rcaeval/converted")
    parser.add_argument("--out", default="runs/rcaeval")
    parser.add_argument("cases", nargs="*", help="optional case-id slice; empty runs all discovered cases")
    args = parser.parse_args(argv)

    scenarios = discover_cases(Path(args.converted_root), args.cases or None)
    if not scenarios:
        print(f"no converted cases under {args.converted_root}")
        return 1

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    graded: list[tuple[str, dict]] = []
    skipped: list[dict] = []
    for scenario in scenarios:
        try:
            result = run_case(client, scenario)
        except Exception as exc:  # skip a broken case, never abort the sweep
            skipped.append({"case": scenario.id, "error": repr(exc)})
            print(f"SKIP {scenario.id}: {exc!r}")
            continue
        graded.append((scenario.id, result.grade))
        (out_dir / f"{scenario.id}.json").write_text(
            json.dumps({"grade": result.grade, "calls": result.call_count,
                        "cost_usd": result.est_cost_usd}, indent=2),
            encoding="utf-8",
        )
        print(f"{scenario.id}: correct={result.grade.get('correct')} calls={result.call_count}")

    scorecard = aggregate_scorecard(graded)
    scorecard["skipped"] = skipped
    (out_dir / "scorecard.json").write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
    print(f"AC@1 = {scorecard['overall_ac1']:.3f} over n={scorecard['n']} "
          f"({len(skipped)} skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
