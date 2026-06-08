"""Reliability aggregation for the eval harness.

A single pass is not an evaluation: the agent is non-deterministic, so we run each
scenario k times and report how often it succeeds, not just whether it can. This is
the framework-free version of what LangSmith/OpenAI eval guidance calls running an
experiment multiple times and reporting pass@k (>=1 of k) and pass^k (all k), plus
per-dimension accuracy against the sealed ground truth.

The aggregation is pure (no API, no I/O) so it is unit-tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_DIMENSIONS = ("location_correct", "culprit_correct", "decoys_ruled_out", "type_match")


@dataclass
class ScenarioReliability:
    scenario_id: str
    runs: int
    passes: int
    dimension_passes: dict[str, int] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        return self.passes / self.runs if self.runs else 0.0

    @property
    def passed_all(self) -> bool:
        return self.runs > 0 and self.passes == self.runs

    @property
    def passed_any(self) -> bool:
        return self.passes > 0


@dataclass
class SuiteReport:
    per_scenario: list[ScenarioReliability]
    total_runs: int
    total_passes: int
    dimension_rates: dict[str, float]
    mean_cost: float
    mean_calls: float

    @property
    def micro_pass_rate(self) -> float:
        """Per-run accuracy: expected correctness of a single run."""
        return self.total_passes / self.total_runs if self.total_runs else 0.0

    @property
    def pass_at_k(self) -> float:
        """Fraction of scenarios solved at least once across their k runs."""
        n = len(self.per_scenario)
        return sum(1 for s in self.per_scenario if s.passed_any) / n if n else 0.0

    @property
    def pass_hat_k(self) -> float:
        """Fraction of scenarios solved on every one of their k runs (consistency)."""
        n = len(self.per_scenario)
        return sum(1 for s in self.per_scenario if s.passed_all) / n if n else 0.0


def aggregate(runs_by_scenario: dict[str, list[dict]]) -> SuiteReport:
    per_scenario: list[ScenarioReliability] = []
    dim_pass = {d: 0 for d in _DIMENSIONS}
    total_runs = 0
    total_passes = 0
    costs: list[float] = []
    calls: list[float] = []
    for scenario_id, runs in runs_by_scenario.items():
        passes = sum(1 for r in runs if r.get("correct"))
        dpasses = {d: sum(1 for r in runs if r.get(d)) for d in _DIMENSIONS}
        per_scenario.append(ScenarioReliability(scenario_id, len(runs), passes, dpasses))
        total_runs += len(runs)
        total_passes += passes
        for d in _DIMENSIONS:
            dim_pass[d] += dpasses[d]
        costs.extend(float(r.get("cost", 0.0)) for r in runs)
        calls.extend(float(r.get("calls", 0)) for r in runs)
    dim_rates = {d: (dim_pass[d] / total_runs if total_runs else 0.0) for d in _DIMENSIONS}
    return SuiteReport(
        per_scenario=per_scenario,
        total_runs=total_runs,
        total_passes=total_passes,
        dimension_rates=dim_rates,
        mean_cost=sum(costs) / len(costs) if costs else 0.0,
        mean_calls=sum(calls) / len(calls) if calls else 0.0,
    )


def format_report(report: SuiteReport) -> str:
    lines = ["", "===== RELIABILITY (multi-run) ====="]
    for s in report.per_scenario:
        flag = "  [flaky]" if s.passed_any and not s.passed_all else ""
        lines.append(f"  {s.scenario_id}: {s.passes}/{s.runs} pass ({s.pass_rate:.0%}){flag}")
    lines.append(f"  micro pass-rate (per run): {report.micro_pass_rate:.0%} ({report.total_passes}/{report.total_runs})")
    lines.append(f"  pass@k  (>=1 of k): {report.pass_at_k:.0%} of scenarios")
    lines.append(f"  pass^k  (all of k): {report.pass_hat_k:.0%} of scenarios")
    dims = ", ".join(f"{d.replace('_correct', '').replace('_', ' ')}={r:.0%}" for d, r in report.dimension_rates.items())
    lines.append(f"  dimension pass-rates: {dims}")
    lines.append(f"  mean cost=${report.mean_cost:.4f}  mean tool_calls={report.mean_calls:.1f}")
    return "\n".join(lines)
