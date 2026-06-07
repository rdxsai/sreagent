"""Typed contracts for OpenTelemetry Demo fixture files."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model that rejects unknown fields in fixture contracts."""

    model_config = ConfigDict(extra="forbid")


class TimeWindow(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def end_must_follow_start(self) -> TimeWindow:
        if self.end <= self.start:
            raise ValueError("window.end must be greater than window.start")
        return self


class PublicManifest(StrictModel):
    scenario_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    time_unit: str = Field(min_length=1)
    window: TimeWindow
    symptom: str = Field(min_length=1)
    available_signals: list[str] = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)
    alerts: list[DerivedAlert] = Field(min_length=1)   # the frozen firing SET (>=1). The agent triages.


class DerivedAlert(StrictModel):
    alertname: str = Field(min_length=1)        # from the symptom allow-list
    severity: Literal["warning", "critical"]
    starts_at_second: int = Field(ge=0)         # window-relative onset of the symptom
    labels: dict[str, str] = Field(default_factory=dict)        # symptom-level only
    annotations: dict[str, str] = Field(default_factory=dict)   # templated from value/starts_at only
    value: float                                # breaching value of the symptom series
    expr: str = Field(min_length=1)             # the PromQL that fired (symptom series only)
    fingerprint: str = Field(min_length=1)      # stable hash of alertname + sorted labels


class TopologyEdge(StrictModel):
    caller: str = Field(min_length=1)
    callee: str = Field(min_length=1)


class Topology(StrictModel):
    services: list[str] = Field(min_length=1)
    edges: list[TopologyEdge] = Field(default_factory=list)

    @field_validator("services")
    @classmethod
    def services_must_be_unique(cls, services: list[str]) -> list[str]:
        if len(services) != len(set(services)):
            raise ValueError("topology services must be unique")
        return services


class TelemetryAttributes(StrictModel):
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class MetricRow(TelemetryAttributes):
    time: int = Field(ge=0)
    service: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)


class LogRow(TelemetryAttributes):
    time: int = Field(ge=0)
    service: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    message: str = Field(min_length=1)
    trace_id: str | None = None


class TraceRow(TelemetryAttributes):
    trace_id: str = Field(min_length=1)
    span_id: str = Field(min_length=1)
    parent_span_id: str | None = None
    time: int = Field(ge=0)
    service: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    duration_ms: float = Field(ge=0)
    status: str = Field(min_length=1)


class ChangeEvent(StrictModel):
    id: str = Field(min_length=1)
    time: int = Field(ge=0)
    service: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    diff_touches: list[str] = Field(default_factory=list)


class InjectionMetadata(StrictModel):
    raw_flag_key: str = Field(min_length=1)
    variant: str = Field(min_length=1)
    enabled_at_second: int = Field(ge=0)


class RootCause(StrictModel):
    kind: str = Field(min_length=1)
    type: str = Field(min_length=1)
    service: str | None = None
    caller: str | None = None
    callee: str | None = None


class PrivateTruth(StrictModel):
    scenario_id: str = Field(min_length=1)
    injection: InjectionMetadata
    root_cause: RootCause
    culprit_change_id: str = Field(min_length=1)
    expected_evidence: list[str] = Field(min_length=1)
    decoy_change_ids: list[str] = Field(min_length=1)


class PublicFixture(StrictModel):
    """In-memory view of a public fixture directory."""

    root: Path
    manifest: PublicManifest
    metrics: list[MetricRow]
    logs: list[LogRow]
    traces: list[TraceRow]
    changes: list[ChangeEvent]
    runbooks: list[dict[str, JsonValue]] = Field(default_factory=list)


class ScenarioTiming(StrictModel):
    warmup_seconds: int = Field(ge=0)
    injection_at_seconds: int = Field(ge=0)
    recording_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def injection_must_fit_recording(self) -> ScenarioTiming:
        if self.injection_at_seconds >= self.recording_seconds:
            raise ValueError("injection_at_seconds must fall inside recording_seconds")
        return self


class ScenarioWorkload(StrictModel):
    profile: str = Field(min_length=1)
    users: int = Field(gt=0)
    spawn_rate: float = Field(gt=0)


class ScenarioPublicConfig(StrictModel):
    symptom: str = Field(min_length=1)
    sanitized_change: ChangeEvent
    decoy_changes: list[ChangeEvent] = Field(min_length=1)


class ScenarioTruthConfig(StrictModel):
    root_cause: RootCause


class ScenarioDefinition(StrictModel):
    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    raw_flag_key: str = Field(min_length=1)
    variant: str = Field(min_length=1)
    timing: ScenarioTiming
    workload: ScenarioWorkload
    public: ScenarioPublicConfig
    truth: ScenarioTruthConfig
