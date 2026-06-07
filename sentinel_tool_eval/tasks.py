"""Evaluation tasks: one incident per recorded scenario.

The task prompt states the symptom and the frozen alerts (what an on-call
engineer is paged with) and asks for the root cause. It never names a tool or a
parameter, so the model's tool selection is what is under test. Each task points
at the eval-only truth the grader will score against.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sentinel.fixtures.replay import load_public_fixture

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Scenario:
    id: str
    public_dir: Path
    truth_path: Path


def _scenario(name: str) -> Scenario:
    base = ROOT / "fixtures" / name
    return Scenario(
        id=name,
        public_dir=base / "public",
        truth_path=base / "eval_only" / "truth.json",
    )


SCENARIOS: list[Scenario] = [
    _scenario("payment_failure_001"),
    _scenario("payment_unreachable_001"),
    _scenario("product_catalog_failure_001"),
    _scenario("cart_failure_001"),
    _scenario("ad_manual_gc_001"),
    _scenario("ad_high_cpu_001"),
]


def build_task_prompt(scenario: Scenario) -> str:
    manifest = load_public_fixture(scenario.public_dir).manifest
    lines = [
        "You are paged for an incident in a microservices application.",
        "",
        f"Symptom: {manifest.symptom}",
        "",
        "Firing alerts:",
    ]
    for alert in manifest.alerts:
        lines.append(
            f"- {alert.alertname} ({alert.severity}) at second {alert.starts_at_second}: "
            f"{alert.annotations.get('summary', '')}"
        )
    lines += [
        "",
        f"Recording window: seconds {manifest.window.start} to {manifest.window.end}.",
        "",
        "Investigate the recorded telemetry and determine the root cause: which service "
        "or which caller->callee dependency is at fault, and the failure type. Identify "
        "which recent change caused it and rule out the others. Submit your conclusion "
        "with the report tool.",
    ]
    return "\n".join(lines)
