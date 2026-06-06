"""Fixture validation for the OpenTelemetry Demo lab."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from labs.otel.redactor import RedactionError, assert_no_banned_tokens
from sentinel.fixtures import load_public_fixture
from sentinel.fixtures.schemas import MetricRow, PrivateTruth, PublicFixture


@dataclass(frozen=True)
class QualityGates:
    error_rate_delta_min: float = 0.05
    latency_p95_delta_min_ratio: float = 1.5
    # Ceiling on the mean pre-onset error rate of the target service. Uses the mean
    # (not the worst sample) so transient baseline blips in real telemetry do not
    # fail an otherwise clean recording, with headroom for rate-quantization noise.
    pre_onset_error_rate_max: float = 0.10
    minimum_trace_count: int = 50
    minimum_log_count: int = 20


@dataclass(frozen=True)
class ValidationReport:
    scenario_id: str
    errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise FixtureValidationError(self)


class FixtureValidationError(ValueError):
    """Raised when a fixture does not pass lab validation."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__("; ".join(report.errors))


def validate_fixture(
    scenario_dir: Path,
    gates: QualityGates = QualityGates(),
) -> ValidationReport:
    """Validate one recorded fixture directory."""

    scenario_root = scenario_dir.resolve()
    public_dir = scenario_root / "public"
    truth_path = scenario_root / "eval_only" / "truth.json"
    errors: list[str] = []

    try:
        assert_no_banned_tokens(public_dir)
    except RedactionError as exc:
        errors.append(str(exc))

    truth = _load_truth(truth_path, errors)
    fixture = _load_public_fixture(public_dir, errors)
    if truth is None or fixture is None:
        return ValidationReport(scenario_root.name, tuple(errors))

    if fixture.manifest.scenario_id != truth.scenario_id:
        errors.append("public manifest scenario_id does not match private truth")

    _validate_manifest(fixture, errors)
    _validate_decoys(fixture, truth, errors)
    _validate_replay(public_dir, fixture, errors)
    _validate_signal_quality(fixture, truth, gates, errors)

    return ValidationReport(fixture.manifest.scenario_id, tuple(errors))


def _load_truth(path: Path, errors: list[str]) -> PrivateTruth | None:
    if not path.exists():
        errors.append("private truth.json is missing")
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            return PrivateTruth.model_validate(json.load(handle))
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"private truth.json is invalid: {exc}")
        return None


def _load_public_fixture(public_dir: Path, errors: list[str]) -> PublicFixture | None:
    try:
        return load_public_fixture(public_dir)
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"public fixture is invalid: {exc}")
        return None


def _validate_manifest(fixture: PublicFixture, errors: list[str]) -> None:
    manifest = fixture.manifest
    if "raw_flag_key" in manifest.model_dump_json():
        errors.append("public manifest includes raw_flag_key")
    required_signals = {"metrics", "logs", "traces", "changes"}
    missing = required_signals.difference(manifest.available_signals)
    if missing:
        errors.append(f"public manifest missing available signals: {sorted(missing)}")


def _validate_decoys(
    fixture: PublicFixture,
    truth: PrivateTruth,
    errors: list[str],
) -> None:
    changes = {change.id: change for change in fixture.changes}
    if not truth.decoy_change_ids:
        errors.append("private truth has no decoy change ids")
        return

    culprit = changes.get(truth.culprit_change_id)
    if culprit is None:
        errors.append("culprit change is missing from public changes")

    for decoy_id in truth.decoy_change_ids:
        decoy = changes.get(decoy_id)
        if decoy is None:
            errors.append(f"decoy change {decoy_id} is missing from public changes")
            continue
        if decoy_id == truth.culprit_change_id:
            errors.append(f"decoy change {decoy_id} duplicates culprit change id")
        if culprit is not None and decoy.model_dump() == culprit.model_dump():
            errors.append(f"decoy change {decoy_id} is identical to culprit change")


def _validate_replay(
    public_dir: Path,
    fixture: PublicFixture,
    errors: list[str],
) -> None:
    replayed = load_public_fixture(public_dir)
    if replayed.model_dump(mode="json") != fixture.model_dump(mode="json"):
        errors.append("public fixture replay is not deterministic")


def _validate_signal_quality(
    fixture: PublicFixture,
    truth: PrivateTruth,
    gates: QualityGates,
    errors: list[str],
) -> None:
    onset = truth.injection.enabled_at_second
    target_services = _target_services(truth)

    if len(fixture.traces) < gates.minimum_trace_count:
        errors.append("fixture has fewer traces than minimum_trace_count")
    if len(fixture.logs) < gates.minimum_log_count:
        errors.append("fixture has fewer logs than minimum_log_count")

    target_metrics = [
        row
        for row in fixture.metrics
        if row.service in target_services and _is_error_or_latency_metric(row)
    ]
    if not any(row.time >= onset for row in target_metrics):
        errors.append("target service has no post-onset error or latency metric")

    pre_error_rates = [
        row.value
        for row in fixture.metrics
        if row.time < onset and row.service in target_services and _is_error_metric(row)
    ]
    if pre_error_rates and _average(pre_error_rates) > gates.pre_onset_error_rate_max:
        errors.append("mean pre-onset target error rate exceeds healthy gate")

    error_delta = _post_minus_pre(
        fixture.metrics,
        onset=onset,
        services=target_services,
        predicate=_is_error_metric,
    )
    latency_ratio = _post_over_pre(
        fixture.metrics,
        onset=onset,
        services=target_services,
        predicate=_is_latency_metric,
    )
    has_error_impact = error_delta is not None and error_delta >= gates.error_rate_delta_min
    has_latency_impact = (
        latency_ratio is not None
        and latency_ratio >= gates.latency_p95_delta_min_ratio
    )
    if not has_error_impact and not has_latency_impact:
        errors.append("post-onset impact is below error and latency gates")

    has_log_evidence = any(row.time >= onset and row.service in target_services for row in fixture.logs)
    has_trace_evidence = any(
        row.time >= onset and row.service in target_services for row in fixture.traces
    )
    if not has_log_evidence and not has_trace_evidence:
        errors.append("target service has no post-onset log or trace evidence")


def _target_services(truth: PrivateTruth) -> set[str]:
    services = {truth.root_cause.service, truth.root_cause.caller, truth.root_cause.callee}
    return {service for service in services if service}


def _post_minus_pre(
    rows: list[MetricRow],
    *,
    onset: int,
    services: set[str],
    predicate: Callable[[MetricRow], bool],
) -> float | None:
    pre = [row.value for row in rows if row.time < onset and row.service in services and predicate(row)]
    post = [row.value for row in rows if row.time >= onset and row.service in services and predicate(row)]
    if not pre or not post:
        return None
    return _average(post) - _average(pre)


def _post_over_pre(
    rows: list[MetricRow],
    *,
    onset: int,
    services: set[str],
    predicate: Callable[[MetricRow], bool],
) -> float | None:
    pre = [row.value for row in rows if row.time < onset and row.service in services and predicate(row)]
    post = [row.value for row in rows if row.time >= onset and row.service in services and predicate(row)]
    if not pre or not post:
        return None
    baseline = _average(pre)
    if baseline <= 0:
        return None
    return _average(post) / baseline


def _is_error_or_latency_metric(row: MetricRow) -> bool:
    return _is_error_metric(row) or _is_latency_metric(row)


def _is_error_metric(row: MetricRow) -> bool:
    return "error" in row.metric


def _is_latency_metric(row: MetricRow) -> bool:
    return "latency" in row.metric or "duration" in row.metric


def _average(values: list[float]) -> float:
    return sum(values) / len(values)
