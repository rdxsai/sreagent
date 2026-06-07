"""Recording workflow for OpenTelemetry Demo fixture scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import sleep, time
from typing import Any, Callable, Protocol

import yaml

from labs.otel.alerting import derive_alerts, load_rules
from labs.otel.normalizer import (
    normalize_jaeger_traces,
    normalize_opensearch_logs,
    normalize_prometheus_matrix,
)
from labs.otel.recorder import JaegerClient, OpenSearchClient, PrometheusClient
from labs.otel.source import OTEL_DEMO_GIT_SHA
from labs.otel.validator import QualityGates, ValidationReport, validate_fixture
from labs.otel.writer import write_fixture
from sentinel.fixtures.schemas import (
    ChangeEvent,
    InjectionMetadata,
    LogRow,
    MetricRow,
    PrivateTruth,
    PublicManifest,
    ScenarioDefinition,
    TimeWindow,
    TraceRow,
)


class ScenarioWorkflowError(ValueError):
    """Raised when a scenario cannot be recorded or assembled."""


class FlagController(Protocol):
    def read(self) -> dict[str, Any]:
        ...

    def reset_all_flags(self, variant: str = "off") -> dict[str, Any]:
        ...

    def set_flag_variant(self, raw_flag_key: str, variant: str) -> dict[str, Any]:
        ...


class PrometheusRangeClient(Protocol):
    def query_range(self, query: str, *, start: float, end: float, step: str) -> dict[str, Any]:
        ...


class JaegerTraceClient(Protocol):
    def services(self) -> list[str]:
        ...

    def traces(
        self,
        service: str,
        *,
        limit: int = 100,
        start_epoch_seconds: float | None = None,
        end_epoch_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        ...


class OpenSearchLogClient(Protocol):
    def search_logs(
        self,
        *,
        size: int = 100,
        start_epoch_seconds: float | None = None,
        end_epoch_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class MetricQuery:
    metric_name: str
    query: str
    unit: str
    service_label: str = "service_name"


@dataclass(frozen=True)
class PrometheusMatrixCapture:
    metric_name: str
    unit: str
    result: dict[str, Any]
    service_label: str = "service_name"


@dataclass(frozen=True)
class RawTelemetryCapture:
    window_start_epoch_seconds: float
    window_end_epoch_seconds: float
    prometheus_matrices: tuple[PrometheusMatrixCapture, ...] = ()
    jaeger_traces: tuple[dict[str, Any], ...] = ()
    opensearch_logs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class TelemetryClients:
    prometheus: PrometheusRangeClient
    jaeger: JaegerTraceClient
    opensearch: OpenSearchLogClient

    @classmethod
    def from_environment(cls) -> TelemetryClients:
        return cls(
            prometheus=PrometheusClient.from_environment(),
            jaeger=JaegerClient.from_environment(),
            opensearch=OpenSearchClient.from_environment(),
        )


@dataclass(frozen=True)
class RecordingOptions:
    metric_queries: tuple[MetricQuery, ...] = field(default_factory=lambda: DEFAULT_METRIC_QUERIES)
    prometheus_step: str = "15s"
    trace_services: tuple[str, ...] = ()
    trace_limit_per_service: int = 100
    # Total log rows to capture, spread evenly across log_buckets sub-windows so
    # post-onset logs are represented instead of only the earliest slice of the window.
    log_limit: int = 20000
    log_buckets: int = 12
    validate_output: bool = True
    validation_gates: QualityGates = field(default_factory=QualityGates)


DEFAULT_METRIC_QUERIES: tuple[MetricQuery, ...] = (
    MetricQuery(
        metric_name="request_error_rate",
        unit="ratio",
        query=(
            "(sum by (service_name) "
            '(rate(traces_span_metrics_calls_total{status_code="STATUS_CODE_ERROR"}[3m])) '
            "/ sum by (service_name) "
            "(rate(traces_span_metrics_calls_total[3m]))) "
            "or (0 * sum by (service_name) "
            "(rate(traces_span_metrics_calls_total[3m])))"
        ),
    ),
    MetricQuery(
        metric_name="latency_p95_ms",
        unit="ms",
        query=(
            "histogram_quantile(0.95, "
            "sum by (le, service_name) "
            "(rate(traces_span_metrics_duration_milliseconds_bucket[3m])))"
        ),
    ),
)

SleepFn = Callable[[float], None]
ClockFn = Callable[[], float]


def load_scenarios(config_path: Path) -> list[ScenarioDefinition]:
    """Load scenario definitions from the lab YAML catalog."""

    with config_path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ScenarioWorkflowError("scenario config must be a mapping")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise ScenarioWorkflowError("scenario config missing scenarios list")
    return [ScenarioDefinition.model_validate(item) for item in scenarios]


def load_scenario(config_path: Path, scenario_id: str | None = None) -> ScenarioDefinition:
    """Load one scenario by id, or the first scenario when no id is provided."""

    scenarios = load_scenarios(config_path)
    if not scenarios:
        raise ScenarioWorkflowError("scenario config contains no scenarios")
    if scenario_id is None:
        return scenarios[0]
    for scenario in scenarios:
        if scenario.id == scenario_id:
            return scenario
    raise ScenarioWorkflowError(f"unknown scenario id: {scenario_id}")


def collect_raw_capture(
    clients: TelemetryClients,
    *,
    window_start_epoch_seconds: float,
    window_end_epoch_seconds: float,
    options: RecordingOptions = RecordingOptions(),
) -> RawTelemetryCapture:
    """Read metrics, traces, and logs from the verified telemetry backends."""

    matrices = tuple(
        PrometheusMatrixCapture(
            metric_name=query.metric_name,
            unit=query.unit,
            result=clients.prometheus.query_range(
                query.query,
                start=window_start_epoch_seconds,
                end=window_end_epoch_seconds,
                step=options.prometheus_step,
            ),
            service_label=query.service_label,
        )
        for query in options.metric_queries
    )
    trace_services = options.trace_services or tuple(clients.jaeger.services())
    traces: list[dict[str, Any]] = []
    for service in trace_services:
        traces.extend(
            clients.jaeger.traces(
                service,
                limit=options.trace_limit_per_service,
                start_epoch_seconds=window_start_epoch_seconds,
                end_epoch_seconds=window_end_epoch_seconds,
            )
        )

    return RawTelemetryCapture(
        window_start_epoch_seconds=window_start_epoch_seconds,
        window_end_epoch_seconds=window_end_epoch_seconds,
        prometheus_matrices=matrices,
        jaeger_traces=tuple(traces),
        opensearch_logs=_collect_bucketed_logs(
            clients.opensearch,
            window_start_epoch_seconds=window_start_epoch_seconds,
            window_end_epoch_seconds=window_end_epoch_seconds,
            options=options,
        ),
    )


def _collect_bucketed_logs(
    opensearch: OpenSearchLogClient,
    *,
    window_start_epoch_seconds: float,
    window_end_epoch_seconds: float,
    options: RecordingOptions,
) -> tuple[dict[str, Any], ...]:
    """Pull logs evenly across the window so post-onset rows are not crowded out.

    A single time-sorted query returns only the earliest rows of a high-volume
    window. Splitting the window into buckets and pulling a share from each keeps
    coverage across the whole incident, including after onset.
    """

    buckets = max(1, options.log_buckets)
    per_bucket = max(1, -(-options.log_limit // buckets))
    span = (window_end_epoch_seconds - window_start_epoch_seconds) / buckets
    seen_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    for index in range(buckets):
        bucket_start = window_start_epoch_seconds + span * index
        bucket_end = (
            window_end_epoch_seconds
            if index == buckets - 1
            else window_start_epoch_seconds + span * (index + 1)
        )
        for hit in opensearch.search_logs(
            size=per_bucket,
            start_epoch_seconds=bucket_start,
            end_epoch_seconds=bucket_end,
        ):
            identifier = hit.get("_id")
            if isinstance(identifier, str):
                if identifier in seen_ids:
                    continue
                seen_ids.add(identifier)
            rows.append(hit)
    return tuple(rows)


def assemble_recorded_fixture(
    scenario: ScenarioDefinition,
    output_dir: Path,
    capture: RawTelemetryCapture,
    *,
    prometheus: PrometheusRangeClient,
    flag_snapshot_before: dict[str, Any] | None = None,
    flag_snapshot_after: dict[str, Any] | None = None,
    recorded_at: datetime | None = None,
    options: RecordingOptions = RecordingOptions(),
) -> ValidationReport:
    """Normalize a raw capture and write the sealed fixture directory."""

    metrics = _normalize_metrics(capture)
    traces = normalize_jaeger_traces(
        list(capture.jaeger_traces),
        window_start_epoch_seconds=capture.window_start_epoch_seconds,
    )
    logs = normalize_opensearch_logs(
        list(capture.opensearch_logs),
        window_start_epoch_seconds=capture.window_start_epoch_seconds,
    )
    changes = _public_changes(scenario)
    scenario_dir = output_dir / scenario.id

    write_fixture(
        scenario_dir,
        manifest=_manifest(scenario, capture, prometheus),
        metrics=metrics,
        logs=logs,
        traces=traces,
        changes=changes,
        truth=_truth(scenario),
        injection_log=_injection_log(
            scenario,
            capture=capture,
            recorded_at=recorded_at or datetime.now(timezone.utc),
        ),
        eval_only_json=_flag_snapshots(
            before=flag_snapshot_before,
            after=flag_snapshot_after,
        ),
    )
    if not options.validate_output:
        return ValidationReport(scenario.id, ())
    return validate_fixture(scenario_dir, options.validation_gates)


def record_scenario_definition(
    scenario: ScenarioDefinition,
    output_dir: Path,
    *,
    flag_client: FlagController,
    telemetry_clients: TelemetryClients,
    options: RecordingOptions = RecordingOptions(),
    sleep_fn: SleepFn = sleep,
    clock: ClockFn = time,
) -> ValidationReport:
    """Control a live scenario, collect telemetry, and write a sealed fixture."""

    flag_snapshot_before = flag_client.reset_all_flags()
    sleep_fn(float(scenario.timing.warmup_seconds))
    window_start = clock()
    sleep_fn(float(scenario.timing.injection_at_seconds))
    flag_snapshot_after = flag_client.set_flag_variant(scenario.raw_flag_key, scenario.variant)
    remaining = scenario.timing.recording_seconds - scenario.timing.injection_at_seconds
    sleep_fn(float(remaining))
    window_end = window_start + scenario.timing.recording_seconds
    try:
        capture = collect_raw_capture(
            telemetry_clients,
            window_start_epoch_seconds=window_start,
            window_end_epoch_seconds=window_end,
            options=options,
        )
        return assemble_recorded_fixture(
            scenario,
            output_dir,
            capture,
            prometheus=telemetry_clients.prometheus,
            flag_snapshot_before=flag_snapshot_before,
            flag_snapshot_after=flag_snapshot_after,
            options=options,
        )
    finally:
        flag_client.reset_all_flags()


def _normalize_metrics(capture: RawTelemetryCapture) -> list[MetricRow]:
    rows: list[MetricRow] = []
    for matrix in capture.prometheus_matrices:
        rows.extend(
            normalize_prometheus_matrix(
                matrix.result,
                metric_name=matrix.metric_name,
                unit=matrix.unit,
                window_start_epoch_seconds=capture.window_start_epoch_seconds,
                service_label=matrix.service_label,
            )
        )
    return rows


def _manifest(
    scenario: ScenarioDefinition,
    capture: RawTelemetryCapture,
    prometheus: PrometheusRangeClient,
) -> PublicManifest:
    alerts = derive_alerts(
        window_start_epoch_seconds=capture.window_start_epoch_seconds,
        window_end_epoch_seconds=capture.window_end_epoch_seconds,
        prometheus=prometheus,
        rules=load_rules(),
    )
    return PublicManifest(
        scenario_id=scenario.id,
        source="opentelemetry-demo",
        time_unit="second",
        window=TimeWindow(start=0, end=scenario.timing.recording_seconds),
        symptom=scenario.public.symptom,
        available_signals=["metrics", "logs", "traces", "changes"],
        notes=[
            f"Recorded from OpenTelemetry Demo pinned commit {OTEL_DEMO_GIT_SHA}.",
            "Feature flag names and private injection metadata are intentionally redacted.",
        ],
        alerts=alerts,
    )


def _truth(scenario: ScenarioDefinition) -> PrivateTruth:
    return PrivateTruth(
        scenario_id=scenario.id,
        injection=InjectionMetadata(
            raw_flag_key=scenario.raw_flag_key,
            variant=scenario.variant,
            enabled_at_second=scenario.timing.injection_at_seconds,
        ),
        root_cause=scenario.truth.root_cause,
        culprit_change_id=scenario.public.sanitized_change.id,
        expected_evidence=_expected_evidence(scenario),
        decoy_change_ids=[change.id for change in scenario.public.decoy_changes],
    )


def _expected_evidence(scenario: ScenarioDefinition) -> list[str]:
    root = scenario.truth.root_cause
    culprit = scenario.public.sanitized_change.id
    if root.kind == "edge" and root.caller and root.callee:
        return [
            f"{root.caller} error or latency signals increase after onset",
            f"{root.caller} spans involving {root.callee} show post-onset failures or slowness",
            f"logs include post-onset errors on {root.caller} or {root.callee}",
            f"symptoms begin after sanitized change {culprit}",
        ]
    service = root.service or root.caller or root.callee or scenario.public.sanitized_change.service
    return [
        f"{service} error or latency signals increase after onset",
        f"{service} logs or traces show post-onset failure evidence",
        f"symptoms begin after sanitized change {culprit}",
    ]


def _public_changes(scenario: ScenarioDefinition) -> list[ChangeEvent]:
    return [*scenario.public.decoy_changes, scenario.public.sanitized_change]


def _injection_log(
    scenario: ScenarioDefinition,
    *,
    capture: RawTelemetryCapture,
    recorded_at: datetime,
) -> dict[str, Any]:
    return {
        "scenario_id": scenario.id,
        "raw_flag_key": scenario.raw_flag_key,
        "variant": scenario.variant,
        "enabled_at_second": scenario.timing.injection_at_seconds,
        "window_start_epoch_seconds": capture.window_start_epoch_seconds,
        "window_end_epoch_seconds": capture.window_end_epoch_seconds,
        "recorded_at": recorded_at.astimezone(timezone.utc).isoformat(),
    }


def _flag_snapshots(
    *,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    if before is not None:
        snapshots["raw_flag_snapshot.before.json"] = before
    if after is not None:
        snapshots["raw_flag_snapshot.after.json"] = after
    return snapshots
