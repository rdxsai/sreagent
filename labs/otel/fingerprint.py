"""Scenario fingerprinting for confusability checks."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict

from sentinel.fixtures import load_public_fixture
from sentinel.fixtures.schemas import MetricRow, PublicFixture


class ScenarioFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    impacted_services: list[str]
    earliest_anomalous_service: str | None
    top_error_services: list[str]
    top_latency_services: list[str]
    dominant_log_clusters: list[str]
    slowest_critical_path_spans: list[str]
    resource_anomalies: list[str]
    change_event_timing: dict[str, int]


def fingerprint_fixture(
    public_dir: Path,
    *,
    onset_second: int | None = None,
) -> ScenarioFingerprint:
    """Compute a deterministic fingerprint from observable fixture data."""

    fixture = load_public_fixture(public_dir)
    onset = onset_second
    if onset is None:
        onset = (fixture.manifest.window.start + fixture.manifest.window.end) // 2

    impacted_services = _impacted_services(fixture.metrics, onset)
    return ScenarioFingerprint(
        scenario_id=fixture.manifest.scenario_id,
        impacted_services=impacted_services,
        earliest_anomalous_service=_earliest_anomalous_service(fixture.metrics, onset),
        top_error_services=_top_metric_services(fixture.metrics, onset, _is_error_metric),
        top_latency_services=_top_metric_services(fixture.metrics, onset, _is_latency_metric),
        dominant_log_clusters=_dominant_log_clusters(fixture, onset),
        slowest_critical_path_spans=_slowest_spans(fixture),
        resource_anomalies=_resource_anomalies(fixture.metrics, onset),
        change_event_timing={change.id: change.time for change in fixture.changes},
    )


def fingerprints_are_trivially_distinct(
    left: ScenarioFingerprint,
    right: ScenarioFingerprint,
) -> bool:
    """Return true when two fingerprints share no major observable surface."""

    overlap_fields = (
        (left.impacted_services, right.impacted_services),
        (left.top_error_services, right.top_error_services),
        (left.top_latency_services, right.top_latency_services),
        (left.dominant_log_clusters, right.dominant_log_clusters),
        (left.resource_anomalies, right.resource_anomalies),
    )
    return not any(set(a).intersection(b) for a, b in overlap_fields)


def _impacted_services(rows: list[MetricRow], onset: int) -> list[str]:
    impacted: set[str] = set()
    for service in sorted({row.service for row in rows}):
        service_rows = [row for row in rows if row.service == service]
        if _post_minus_pre(service_rows, onset, _is_error_metric, minimum_delta=0.05):
            impacted.add(service)
        if _post_over_pre(service_rows, onset, _is_latency_metric, minimum_ratio=1.5):
            impacted.add(service)
        if _post_over_pre(service_rows, onset, _is_resource_metric, minimum_ratio=1.25):
            impacted.add(service)
    return sorted(impacted)


def _earliest_anomalous_service(rows: list[MetricRow], onset: int) -> str | None:
    baselines = _metric_baselines(rows, onset)
    candidates: list[tuple[int, str]] = []
    for row in rows:
        if row.time < onset:
            continue
        baseline = baselines.get((row.service, row.metric))
        if baseline is None:
            continue
        if _is_anomalous(row, baseline):
            candidates.append((row.time, row.service))
    if not candidates:
        return None
    return sorted(candidates)[0][1]


def _top_metric_services(
    rows: list[MetricRow],
    onset: int,
    predicate: Callable[[MetricRow], bool],
    *,
    limit: int = 5,
) -> list[str]:
    by_service: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.time >= onset and predicate(row):
            by_service[row.service].append(row.value)
    ranked = sorted(
        ((sum(values) / len(values), service) for service, values in by_service.items()),
        key=lambda item: (-item[0], item[1]),
    )
    return [service for _, service in ranked[:limit]]


def _dominant_log_clusters(
    fixture: PublicFixture,
    onset: int,
    *,
    limit: int = 5,
) -> list[str]:
    counter: Counter[str] = Counter()
    for row in fixture.logs:
        if row.time >= onset:
            counter[_normalize_log_message(row.message)] += 1
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [message for message, _ in ranked[:limit]]


def _slowest_spans(fixture: PublicFixture, *, limit: int = 5) -> list[str]:
    ranked = sorted(
        fixture.traces,
        key=lambda row: (-row.duration_ms, row.service, row.operation, row.span_id),
    )
    return [f"{row.service}:{row.operation}" for row in ranked[:limit]]


def _resource_anomalies(rows: list[MetricRow], onset: int) -> list[str]:
    anomalies: set[str] = set()
    for service in sorted({row.service for row in rows}):
        service_rows = [row for row in rows if row.service == service]
        if _post_over_pre(service_rows, onset, _is_resource_metric, minimum_ratio=1.25):
            anomalies.add(service)
    return sorted(anomalies)


def _metric_baselines(rows: list[MetricRow], onset: int) -> dict[tuple[str, str], float]:
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        if row.time < onset:
            values[(row.service, row.metric)].append(row.value)
    return {key: sum(metric_values) / len(metric_values) for key, metric_values in values.items()}


def _is_anomalous(row: MetricRow, baseline: float) -> bool:
    if _is_error_metric(row):
        return row.value >= baseline + 0.05
    if _is_latency_metric(row):
        return baseline > 0 and row.value >= baseline * 1.5
    if _is_resource_metric(row):
        return baseline > 0 and row.value >= baseline * 1.25
    return False


def _post_minus_pre(
    rows: list[MetricRow],
    onset: int,
    predicate: Callable[[MetricRow], bool],
    *,
    minimum_delta: float,
) -> bool:
    pre = [row.value for row in rows if row.time < onset and predicate(row)]
    post = [row.value for row in rows if row.time >= onset and predicate(row)]
    return bool(pre and post and (sum(post) / len(post)) - (sum(pre) / len(pre)) >= minimum_delta)


def _post_over_pre(
    rows: list[MetricRow],
    onset: int,
    predicate: Callable[[MetricRow], bool],
    *,
    minimum_ratio: float,
) -> bool:
    pre = [row.value for row in rows if row.time < onset and predicate(row)]
    post = [row.value for row in rows if row.time >= onset and predicate(row)]
    if not pre or not post:
        return False
    baseline = sum(pre) / len(pre)
    return baseline > 0 and (sum(post) / len(post)) / baseline >= minimum_ratio


def _is_error_metric(row: MetricRow) -> bool:
    return "error" in row.metric


def _is_latency_metric(row: MetricRow) -> bool:
    return "latency" in row.metric or "duration" in row.metric


def _is_resource_metric(row: MetricRow) -> bool:
    return "cpu" in row.metric or "memory" in row.metric


def _normalize_log_message(message: str) -> str:
    normalized = message.lower()
    normalized = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", normalized)
    normalized = re.sub(r"\b\d+\b", "<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized[:120]
