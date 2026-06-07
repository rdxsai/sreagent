from __future__ import annotations
import json
from pathlib import Path


class CoverageError(ValueError):
    """Raised when the alert firing matrix violates coverage or no-index invariants."""


def build_coverage_matrix(fixture_dirs: list[Path]) -> dict[str, list[str]]:
    """Map scenario_id -> sorted alertnames, read from each fixture's public/manifest.json.

    Reads the manifest JSON directly (scenario_id + alerts[].alertname); does not
    require full schema validation, so it works on any well-formed manifest.
    """
    matrix: dict[str, list[str]] = {}
    for fixture_dir in fixture_dirs:
        manifest_path = Path(fixture_dir) / "public" / "manifest.json"
        with manifest_path.open(encoding="utf-8") as handle:
            manifest = json.load(handle)
        scenario_id = manifest["scenario_id"]
        alertnames = sorted(a["alertname"] for a in manifest.get("alerts", []))
        matrix[scenario_id] = alertnames
    return matrix


def assert_coverage_invariants(matrix: dict[str, list[str]]) -> None:
    """Enforce E1 coverage, E2 sharing, E3 no-set-is-a-1:1-fault-index."""
    problems: list[str] = []

    # E1: every scenario fires at least one alert
    for scenario_id, alertnames in matrix.items():
        if not alertnames:
            problems.append(f"E1 coverage: scenario {scenario_id} fired no alert")

    # E2: every alertname fires for >= 2 distinct scenarios
    scenarios_by_alert: dict[str, set[str]] = {}
    for scenario_id, alertnames in matrix.items():
        for name in alertnames:
            scenarios_by_alert.setdefault(name, set()).add(scenario_id)
    for name, scenarios in scenarios_by_alert.items():
        if len(scenarios) < 2:
            problems.append(f"E2 sharing: alert {name} fires for only {sorted(scenarios)}")

    # E3: every distinct firing SET is shared by >= 2 scenarios (no set is a unique fault fingerprint)
    scenarios_by_set: dict[frozenset[str], set[str]] = {}
    for scenario_id, alertnames in matrix.items():
        scenarios_by_set.setdefault(frozenset(alertnames), set()).add(scenario_id)
    for alert_set, scenarios in scenarios_by_set.items():
        if len(scenarios) < 2:
            problems.append(f"E3 no-index: firing set {sorted(alert_set)} is unique to {sorted(scenarios)}")

    if problems:
        raise CoverageError("; ".join(problems))


def write_coverage_matrix(matrix: dict[str, list[str]], path: Path) -> None:
    path.write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8")
