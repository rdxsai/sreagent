"""Typed I/O contracts for the tool layer.

Returns are deliberately lean: span attribute blobs are dropped in favor of the
fields an investigator actually reasons over. `Topology`, `ChangeEvent`, and
`RootCause` are reused from the fixture schemas so the grader can compare a
`RootCauseReport` against `PrivateTruth` directly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from sentinel.fixtures.schemas import ChangeEvent, RootCause, Topology, TopologyEdge

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
    "TopologyEdge",
    "ServiceInput",
    "Dependencies",
    "BlastRadius",
    "CriticalPathInput",
    "CriticalPath",
    "OriginPathOutput",
    "OnsetWindowInput",
    "TopologyDelta",
    "SignalsInput",
    "SignalShift",
    "SignalAlignment",
    "MetricToTracesInput",
    "MetricToTracesOutput",
    "GatherEvidenceInput",
    "RuleOutInput",
    "RuleOutVerdict",
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


# ---- topology -------------------------------------------------------------


class ServiceInput(BaseModel):
    service: str = Field(min_length=1, description="the service to inspect, e.g. 'checkout'")


class Dependencies(BaseModel):
    service: str
    callers: list[str] = Field(description="services that directly call this service (one hop upstream)")
    callees: list[str] = Field(description="services this service directly calls (one hop downstream)")


class BlastRadius(BaseModel):
    service: str
    upstream: list[str] = Field(description="services that transitively depend on this one; impacted if it degrades")
    downstream: list[str] = Field(description="services this one transitively depends on; possible upstream causes")


class CriticalPathInput(BaseModel):
    target: str = Field(min_length=1, description="the service to reach, e.g. 'payment'")
    source: str = Field(default="frontend", description="the user-facing entry point to start from")


class CriticalPath(BaseModel):
    source: str
    target: str
    path: list[str] = Field(description="service call path source -> ... -> target; empty if unreachable")
    found: bool


class OriginPathOutput(BaseModel):
    classification: Literal["service", "edge", "unknown"]
    origin_service: str | None = Field(default=None, description="the originating service when classification == service")
    path: list[str] = Field(default_factory=list, description="error-propagation path of services from entry to terminal")
    terminal_has_server_error: bool = Field(description="true if the terminal service's own server spans error (service fault, not edge)")
    evidence: list[str] = Field(default_factory=list)


class OnsetWindowInput(BaseModel):
    onset_second: int = Field(ge=0, description="trace-based onset; edges erroring before this are baseline, after are new")


class TopologyDelta(BaseModel):
    new_error_edges: list[TopologyEdge] = Field(description="caller->callee edges that begin erroring only after onset")
    note: str | None = None


# ---- correlate (cross-signal) ---------------------------------------------


class SignalsInput(BaseModel):
    service: str = Field(min_length=1, description="the service whose signals to align")
    onset_second: int = Field(ge=0, description="split point: means are taken before vs at/after this")


class SignalShift(BaseModel):
    metric: str
    pre_mean: float
    post_mean: float
    shifted: bool


class SignalAlignment(BaseModel):
    service: str
    onset_second: int
    signals: list[SignalShift]
    shifted_metrics: list[str] = Field(description="metrics that moved after onset (error_rate, latency, cpu)")


class MetricToTracesInput(BaseModel):
    service: str = Field(min_length=1)
    onset_second: int = Field(ge=0, description="only consider spans at/after this time")
    status: str = Field(default="ERROR", description="span status to exemplify: ERROR or OK")
    limit: int = Field(default=5, ge=1, le=20)


class MetricToTracesOutput(BaseModel):
    service: str
    exemplar_trace_ids: list[str] = Field(description="trace ids to open with traces_get_trace")
    sample: list[SpanSummary]
    note: str | None = None


# ---- hypothesis -----------------------------------------------------------


class GatherEvidenceInput(BaseModel):
    hypothesis: Hypothesis


class RuleOutInput(BaseModel):
    onset_second: int = Field(ge=0)
    service: str | None = Field(default=None, description="a suspected service to test for elimination")
    change_id: str | None = Field(default=None, description="a suspected change id to test for elimination")


class RuleOutVerdict(BaseModel):
    ruled_out: bool
    reason: str
