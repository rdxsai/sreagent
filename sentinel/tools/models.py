"""Typed I/O contracts for the tool layer.

Returns are deliberately lean: span attribute blobs are dropped in favor of the
fields an investigator actually reasons over. `Topology`, `ChangeEvent`, and
`RootCause` are reused from the fixture schemas so the grader can compare a
`RootCauseReport` against `PrivateTruth` directly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from sentinel.fixtures.schemas import ChangeEvent, RootCause, Topology

__all__ = [
    "NoArgs",
    "SpanSummary",
    "Onset",
    "Origin",
    "TracesFindInput",
    "TracesFindOutput",
    "GetTraceInput",
    "GetTraceOutput",
    "SeriesKey",
    "SeriesPoint",
    "Series",
    "ListSeriesOutput",
    "MetricSeriesInput",
    "CompareBaselineInput",
    "CompareBaselineOutput",
    "DetectShiftInput",
    "DetectShiftOutput",
    "LogLine",
    "LogsOutput",
    "LogsSearchInput",
    "LogsForTraceInput",
    "ChangesSearchInput",
    "ChangesLookbackInput",
    "ChangesOutput",
    "FaultObservation",
    "AttributeFaultInput",
    "Attribution",
    "TimelineInput",
    "TimelineEntry",
    "TimelineOutput",
    "Hypothesis",
    "EvidenceReport",
    "RootCauseReport",
    "ReportAck",
    "ChangeEvent",
    "RootCause",
    "Topology",
]

SpanKind = Literal["server", "client", "internal", "producer", "consumer"]


class NoArgs(BaseModel):
    """Tool takes no arguments."""


# ---- traces ---------------------------------------------------------------


class SpanSummary(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    service: str
    operation: str
    span_kind: str | None = None
    status: str
    duration_ms: float
    time: int
    callee: str | None = Field(default=None, description="resolved downstream service, when this is a client span")


class Onset(BaseModel):
    found: bool
    second: int | None = Field(default=None, description="time of the first error span (true onset)")
    service: str | None = None
    span_kind: str | None = None
    trace_id: str | None = None


class Origin(BaseModel):
    classification: Literal["service", "edge", "unknown"]
    service: str | None = Field(default=None, description="faulting service when classification == service")
    caller: str | None = Field(default=None, description="calling service when classification == edge")
    callee: str | None = Field(default=None, description="called service when classification == edge")
    span_kind: str | None = None
    first_error_second: int | None = None
    trace_id: str | None = None
    evidence: list[str] = Field(default_factory=list)


class TracesFindInput(BaseModel):
    service: str | None = Field(default=None, description="filter by emitting service")
    span_kind: str | None = Field(default=None, description="server | client | internal | producer | consumer")
    status: str | None = Field(default=None, description="OK or ERROR")
    callee: str | None = Field(default=None, description="downstream service of a client span, e.g. 'payment'")
    operation_contains: str | None = Field(default=None, description="substring match on the span operation")
    start: int | None = None
    end: int | None = None
    limit: int = Field(default=50, ge=1, le=500)


class TracesFindOutput(BaseModel):
    spans: list[SpanSummary]
    total_matched: int
    returned: int
    truncated: bool
    note: str | None = None


class GetTraceInput(BaseModel):
    trace_id: str = Field(min_length=1)


class GetTraceOutput(BaseModel):
    spans: list[SpanSummary]
    count: int


# ---- metrics --------------------------------------------------------------


class SeriesKey(BaseModel):
    service: str
    metric: str
    unit: str


class ListSeriesOutput(BaseModel):
    series: list[SeriesKey]


class SeriesPoint(BaseModel):
    time: int
    value: float


class MetricSeriesInput(BaseModel):
    service: str
    metric: str = Field(description="request_error_rate or latency_p95_ms")
    start: int | None = None
    end: int | None = None
    max_points: int = Field(default=60, ge=2, le=300)


class Series(BaseModel):
    service: str
    metric: str
    unit: str
    points: list[SeriesPoint]
    summary: dict[str, float]
    truncated: bool = False
    note: str | None = None


class CompareBaselineInput(BaseModel):
    service: str
    metric: str
    baseline_start: int
    baseline_end: int
    compare_start: int
    compare_end: int


class CompareBaselineOutput(BaseModel):
    baseline_mean: float
    compare_mean: float
    delta: float
    pct_change: float | None
    shifted: bool


class DetectShiftInput(BaseModel):
    service: str
    metric: str


class DetectShiftOutput(BaseModel):
    shift_second: int | None
    before_mean: float
    after_mean: float
    magnitude: float


# ---- logs -----------------------------------------------------------------


class LogLine(BaseModel):
    time: int
    service: str
    severity: str
    message: str
    trace_id: str | None = None


class LogsOutput(BaseModel):
    logs: list[LogLine]
    total_matched: int
    returned: int
    truncated: bool
    note: str | None = None


class LogsSearchInput(BaseModel):
    service: str | None = None
    severity_min: str | None = Field(default=None, description="lowest severity to include, e.g. 'error'")
    contains: str | None = Field(default=None, description="case-insensitive substring of the message")
    start: int | None = None
    end: int | None = None
    limit: int = Field(default=30, ge=1, le=200)


class LogsForTraceInput(BaseModel):
    trace_id: str = Field(min_length=1)
    severity_min: str | None = None
    limit: int = Field(default=30, ge=1, le=200)


# ---- changes --------------------------------------------------------------


class ChangesSearchInput(BaseModel):
    service: str | None = None
    start: int | None = None
    end: int | None = None


class ChangesLookbackInput(BaseModel):
    onset_second: int = Field(ge=0, description="trace-based onset; only changes strictly before this are returned")
    lookback_seconds: int | None = Field(default=None, description="how far back to look; default to window start")
    service: str | None = None


class ChangesOutput(BaseModel):
    changes: list[ChangeEvent]


# ---- correlate ------------------------------------------------------------


class FaultObservation(BaseModel):
    service: str
    role: Literal["server", "client"]
    error_count: int = Field(ge=0)
    callee: str | None = Field(default=None, description="downstream service for a client observation")


class AttributeFaultInput(BaseModel):
    observations: list[FaultObservation] = Field(min_length=1)


class Attribution(BaseModel):
    kind: Literal["service", "edge", "unknown"]
    service: str | None = None
    caller: str | None = None
    callee: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class TimelineEntry(BaseModel):
    second: int
    kind: str
    detail: str


class TimelineInput(BaseModel):
    onset_second: int = Field(ge=0)
    changes: list[ChangeEvent] = Field(default_factory=list)


class TimelineOutput(BaseModel):
    entries: list[TimelineEntry]
    changes_before_onset: list[str]


# ---- manager / subagent contracts (used fully in the subagent phase) ------


class Hypothesis(BaseModel):
    id: str
    kind: Literal["service", "edge"]
    service: str | None = None
    caller: str | None = None
    callee: str | None = None
    onset_second: int
    rationale: str


class EvidenceReport(BaseModel):
    hypothesis_id: str
    supported: bool
    confidence: float = Field(ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list)
    suspect_change_id: str | None = None


# ---- report ---------------------------------------------------------------


class RootCauseReport(BaseModel):
    root_cause: RootCause
    culprit_change_id: str = Field(min_length=1)
    ruled_out_change_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)


class ReportAck(BaseModel):
    accepted: bool
