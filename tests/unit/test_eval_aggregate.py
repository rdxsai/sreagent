"""Tests for the reliability aggregation (pass@k, pass^k, dimension rates)."""

from __future__ import annotations

from sentinel_tool_eval.aggregate import aggregate


def _run(correct: bool, **dims) -> dict:
    base = {
        "correct": correct,
        "location_correct": correct,
        "culprit_correct": correct,
        "decoys_ruled_out": correct,
        "type_match": correct,
        "cost": 0.5,
        "calls": 40,
    }
    base.update(dims)
    return base


def test_pass_at_k_and_pass_hat_k() -> None:
    runs = {
        "A": [_run(True), _run(True), _run(True)],     # solved every run
        "B": [_run(True), _run(False), _run(True)],    # flaky
        "C": [_run(False), _run(False), _run(False)],  # never solved
    }
    rep = aggregate(runs)
    assert rep.total_runs == 9 and rep.total_passes == 5
    assert abs(rep.micro_pass_rate - 5 / 9) < 1e-9
    assert abs(rep.pass_at_k - 2 / 3) < 1e-9   # A and B passed at least once
    assert abs(rep.pass_hat_k - 1 / 3) < 1e-9  # only A passed all runs
    b = next(s for s in rep.per_scenario if s.scenario_id == "B")
    assert b.passed_any and not b.passed_all


def test_dimension_rates_and_means() -> None:
    runs = {"A": [_run(True, type_match=False), _run(True, type_match=True)]}
    rep = aggregate(runs)
    assert abs(rep.dimension_rates["type_match"] - 0.5) < 1e-9
    assert abs(rep.dimension_rates["culprit_correct"] - 1.0) < 1e-9
    assert abs(rep.mean_cost - 0.5) < 1e-9
    assert abs(rep.mean_calls - 40.0) < 1e-9
