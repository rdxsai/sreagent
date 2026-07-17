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
    "SlowestInput",
    "OperationLatency",
    "SlowestOutput",
    "ErrorSummaryInput",
    "ErrorSummaryOutput",
    "TreeNode",
    "SpanTreeOutput",
    "SpanLatency",
    "LatencyBreakdownOutput",
    "ComparePrePostInput",
    "ComparePrePostOutput",
    "TopMoversInput",
    "MetricMover",
    "TopMoversOutput",
    "SaturationInput",
    "ServiceCpu",
    "SaturationOutput",
    "ErrorBudgetInput",
    "ErrorBudgetOutput",
    "SummaryAllInput",
    "ServiceSnapshot",
    "SummaryAllOutput",
    "ErrorClustersInput",
    "LogCluster",
    "ErrorClustersOutput",
    "LevelHistogramInput",
    "LevelBucket",
    "LevelHistogramOutput",
    "FirstErrorInput",
    "FirstErrorEntry",
    "FirstErrorOutput",
    "ChangesRankCulpritInput",
    "CulpritCandidate",
    "ChangesRankOutput",
    "BuildEvidenceInput",
    "EvidenceBundle",
    "ReportCheck",
    "ServiceTraceSummary",
    "OnsetConsensus",
    "LatencyOriginInput",
    "LatencyOrigin",
    "EdgeLatencyInput",
    "EdgeShift",
    "EdgeLatencyOrigin",
    "ServiceFinding",
    "InvestigateServiceInput",
    "InvestigateParallelInput",
    "ParallelFindings",
    "InvestigateChangeInput",
    "ChangeVerdict",
    "FindingAck",
    "RunbookSearchInput",
    "RunbookMatch",
    "RunbookSearchOutput",
    "RunbookGetInput",
    "Runbook",
    "RunbookGetOutput",
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
    culprit_change_id: str | None = Field(default=None)
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


# ---- traces (depth) -------------------------------------------------------


class SlowestInput(BaseModel):
    start: int | None = Field(default=None, description="only consider spans at/after this second")
    status: str | None = Field(default=None, description="optionally restrict to OK or ERROR spans")
    limit: int = Field(default=10, ge=1, le=50)


class OperationLatency(BaseModel):
    service: str
    operation: str
    span_kind: str | None = None
    count: int
    p95_ms: float
    max_ms: float


class SlowestOutput(BaseModel):
    operations: list[OperationLatency] = Field(description="operations ranked by p95 latency, slowest first")


class ErrorSummaryInput(BaseModel):
    start: int | None = Field(default=None, description="only count error spans at/after this second")


class ErrorSummaryOutput(BaseModel):
    observations: list[FaultObservation] = Field(
        description="per (service, server/client) error counts; feed directly to correlate_attribute_fault"
    )


class TreeNode(BaseModel):
    depth: int = Field(description="nesting level; render with indentation")
    service: str
    operation: str
    span_kind: str | None = None
    status: str
    duration_ms: float


class SpanTreeOutput(BaseModel):
    nodes: list[TreeNode] = Field(description="spans in depth-first order; depth gives the call hierarchy")
    count: int


class SpanLatency(BaseModel):
    service: str
    operation: str
    duration_ms: float
    pct_of_root: float = Field(description="this span's duration as a fraction of the trace's root span")


class LatencyBreakdownOutput(BaseModel):
    spans: list[SpanLatency] = Field(description="spans ranked by duration, slowest first; find the dominant span")
    root_duration_ms: float


class ComparePrePostInput(BaseModel):
    operation_contains: str = Field(min_length=1, description="substring of the operation to compare, e.g. 'Charge'")
    onset_second: int = Field(ge=0)


class ComparePrePostOutput(BaseModel):
    healthy_trace_id: str | None = Field(default=None, description="an OK trace before onset, open with traces_get_trace")
    failing_trace_id: str | None = Field(default=None, description="an ERROR trace at/after onset")
    note: str | None = None


# ---- metrics (depth) ------------------------------------------------------


class TopMoversInput(BaseModel):
    metric: str = Field(description="any metric from metrics_list_series (e.g. request_error_rate, latency_p95_ms, memory_mb, cpu_utilization)")
    onset_second: int = Field(ge=0, description="split point for the pre/post means")
    limit: int = Field(default=10, ge=1, le=30)


class MetricMover(BaseModel):
    service: str
    pre_mean: float
    post_mean: float
    delta: float


class TopMoversOutput(BaseModel):
    metric: str
    movers: list[MetricMover] = Field(description="services ranked by post-minus-pre change, largest increase first")


class SaturationInput(BaseModel):
    onset_second: int = Field(default=0, ge=0, description="pre/post means split at this second")


class ResourceShift(BaseModel):
    service: str
    metric: str
    unit: str
    pre_mean: float
    post_mean: float


class SaturationOutput(BaseModel):
    risers: list[ResourceShift] = Field(
        description="services whose resource metrics (cpu, memory, ...) rose significantly after onset, largest relative rise first"
    )
    note: str = Field(default="", description="which resource metrics the backend advertises, or why the list is empty")


class ErrorBudgetInput(BaseModel):
    service: str = Field(min_length=1)
    slo_error_rate: float = Field(default=0.01, ge=0.0, le=1.0, description="the acceptable error-rate SLO")
    start: int | None = None
    end: int | None = None


class ErrorBudgetOutput(BaseModel):
    service: str
    observed_error_rate: float
    slo_error_rate: float
    budget_burn: float = Field(description="observed / slo; >1 means the budget is exhausted")
    breached: bool


class SummaryAllInput(BaseModel):
    onset_second: int = Field(default=0, ge=0, description="means are taken at/after this second")


class ServiceSnapshot(BaseModel):
    service: str
    error_rate: float
    latency_p95_ms: float
    resources: dict[str, float] = Field(
        default_factory=dict,
        description="post-onset mean of every other advertised metric (e.g. cpu_cores, memory_mb, cpu_utilization), keyed by metric name",
    )


class SummaryAllOutput(BaseModel):
    services: list[ServiceSnapshot] = Field(
        description="per-service snapshot of error rate, p95 latency, and all advertised resource metrics, highest error rate first"
    )


# ---- logs (depth) ---------------------------------------------------------


class ErrorClustersInput(BaseModel):
    service: str | None = Field(default=None, description="restrict to one service")
    start: int | None = Field(default=None, description="only cluster error logs at/after this second")
    limit: int = Field(default=10, ge=1, le=30)


class LogCluster(BaseModel):
    template: str = Field(description="the error message with numbers/ids normalized to '#'")
    count: int
    example: str
    services: list[str]


class ErrorClustersOutput(BaseModel):
    clusters: list[LogCluster] = Field(description="error-log templates by frequency; the dominant failure modes")


class LevelHistogramInput(BaseModel):
    service: str | None = None
    bucket_seconds: int = Field(default=60, ge=5, le=600, description="time bucket width")


class LevelBucket(BaseModel):
    start: int
    counts: dict[str, int] = Field(description="log count per severity level in this bucket")


class LevelHistogramOutput(BaseModel):
    buckets: list[LevelBucket] = Field(description="log-level counts over time; spot when errors surge")


class FirstErrorInput(BaseModel):
    severity_min: str = Field(default="error", description="lowest severity to consider, e.g. 'error'")


class FirstErrorEntry(BaseModel):
    service: str
    time: int
    severity: str
    message: str


class FirstErrorOutput(BaseModel):
    first: list[FirstErrorEntry] = Field(description="each service's earliest error-level log, earliest first")


# ---- changes (depth) ------------------------------------------------------


class ChangesRankCulpritInput(BaseModel):
    onset_second: int = Field(ge=0, description="trace-based onset; changes at/after are excluded as non-causal")
    suspected_service: str | None = Field(
        default=None, description="the implicated service from attribution; its changes are scored highest"
    )


class CulpritCandidate(BaseModel):
    change_id: str
    service: str
    time: int
    score: float
    reason: str


class ChangesRankOutput(BaseModel):
    ranked: list[CulpritCandidate] = Field(
        description="changes before onset, scored by service match then proximity; inspect content to disambiguate ties"
    )


# ---- report (depth) -------------------------------------------------------


class BuildEvidenceInput(BaseModel):
    onset_second: int = Field(ge=0)
    suspected_service: str = Field(min_length=1, description="the implicated service or caller")
    culprit_change_id: str | None = Field(default=None, description="the change you believe is the culprit")


class EvidenceBundle(BaseModel):
    evidence: list[str] = Field(description="ready-to-attach evidence lines for the report")
    timeline: list[TimelineEntry] = Field(description="changes and onset ordered in time")


class ReportCheck(BaseModel):
    ok: bool
    issues: list[str] = Field(default_factory=list, description="what to fix before submitting; empty when ready")


# ---- traces / correlate (extra) -------------------------------------------


class ServiceTraceSummary(BaseModel):
    service: str
    total_spans: int
    error_spans: int
    error_rate: float
    p95_ms: float
    server_error_spans: int = Field(description="error spans the service itself emitted as a server (own-fault signal)")


class OnsetConsensus(BaseModel):
    trace_onset_second: int | None = Field(default=None, description="first error span time")
    first_error_log_second: int | None = Field(default=None, description="earliest error-level log time")
    consensus_onset_second: int | None = Field(default=None, description="the earliest corroborated onset to anchor on")
    agreement: bool = Field(description="true if the trace and log onsets are within one minute")
    note: str | None = None


class LatencyOriginInput(BaseModel):
    onset_second: int = Field(default=0, ge=0, description="pre/post self-time split at this second")
    min_self_ms: float = Field(default=50.0, ge=0.0, description="ignore post-onset spans whose own work is faster than this")


class LatencyOrigin(BaseModel):
    service: str | None = Field(default=None, description="the service whose own work (self-time) degraded most versus its pre-onset baseline")
    self_latency_p95_ms: float = Field(default=0.0, description="post-onset p95 of the service's own time, excluding downstream waits")
    baseline_p95_ms: float = Field(default=0.0, description="pre-onset p95 of the same self-time; the post-minus-pre shift is what ranks")
    self_latency_max_ms: float = 0.0
    evidence: list[str] = Field(default_factory=list)


class EdgeLatencyInput(BaseModel):
    onset_second: int = Field(default=0, ge=0, description="pre/post edge-latency split at this second")
    min_shift_ms: float = Field(default=50.0, ge=0.0, description="ignore edges whose p95 shifted less than this")


class EdgeShift(BaseModel):
    caller: str
    callee: str
    pre_p95_ms: float = Field(description="p95 of the caller's client spans to this callee before onset")
    post_p95_ms: float = Field(description="p95 of the same client spans at/after onset")
    shift_ms: float = Field(description="post minus pre; how much this hop degraded")
    post_samples: int = Field(description="client spans backing the post-onset p95")


class EdgeLatencyOrigin(BaseModel):
    origin_service: str | None = Field(
        default=None, description="the callee where the latency propagation terminates"
    )
    classification: str | None = Field(
        default=None,
        description="network_edge (callers slowed toward it, its own processing flat) or service_internal (its own server spans also slowed)",
    )
    degraded_edges: list[EdgeShift] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class EmissionGapsInput(BaseModel):
    bucket_seconds: int = Field(default=30, ge=5, le=300, description="timeline bucket width for per-service span counts")


class EmissionGap(BaseModel):
    service: str
    gap_start_second: int
    gap_end_second: int
    resumed: bool = Field(description="true if the service emitted spans again after the gap (crash/restart pattern)")


class EmissionGapsOutput(BaseModel):
    gaps: list[EmissionGap] = Field(description="windows where a normally-emitting service produced zero spans, longest first")
    note: str = ""


# ---- subagent contract ----------------------------------------------------


class ServiceFinding(BaseModel):
    """An investigator subagent's structured verdict on one service.

    This is the output contract the manager reconciles; the FindingValidator hook
    checks it on SubagentStop and retries the worker on a violation.
    """

    service: str = Field(min_length=1)
    is_origin: bool = Field(description="true if this service is the fault origin, not a propagation victim")
    fault_type: str | None = Field(default=None, description="error | latency | cpu_saturation | edge | none")
    own_server_errors: int = Field(default=0, ge=0, description="the service's own erroring server spans")
    shifted_signals: list[str] = Field(default_factory=list, description="metrics that moved after onset")
    suspect_change_id: str | None = Field(default=None, description="the change on this service most likely responsible")
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


# ---- investigate (subagent orchestration) ---------------------------------


class InvestigateServiceInput(BaseModel):
    service: str = Field(min_length=1, description="the candidate service to deep-dive in an isolated subagent")


class InvestigateParallelInput(BaseModel):
    services: list[str] = Field(
        min_length=1, max_length=5,
        description="candidate services (from triage) to investigate in parallel, one isolated subagent each",
    )


class ParallelFindings(BaseModel):
    findings: list[ServiceFinding]


class InvestigateChangeInput(BaseModel):
    change_id: str = Field(min_length=1)
    service: str = Field(min_length=1, description="the service the change belongs to")
    incident_summary: str = Field(
        min_length=1,
        description=(
            "one line describing the observed fault so the assessment matches reality, not just errors, "
            "e.g. 'ad CPU saturated to ~4 cores, request latency normal' or 'payment charge spans error'"
        ),
    )


class ChangeVerdict(BaseModel):
    change_id: str
    is_culprit: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class FindingAck(BaseModel):
    accepted: bool


# ---- runbook (knowledge base) ---------------------------------------------


class RunbookSearchInput(BaseModel):
    query: str = Field(min_length=1, description="symptom keywords, e.g. 'errors', 'latency', 'cpu saturation', 'change'")
    limit: int = Field(default=5, ge=1, le=10)


class RunbookMatch(BaseModel):
    id: str
    title: str
    when: str = Field(description="when this runbook applies")


class RunbookSearchOutput(BaseModel):
    matches: list[RunbookMatch] = Field(description="matching runbooks by relevance; open one with runbook_get")


class RunbookGetInput(BaseModel):
    runbook_id: str = Field(min_length=1)


class Runbook(BaseModel):
    id: str
    title: str
    when: str
    steps: list[str] = Field(description="the diagnostic procedure, generic SRE guidance")


class RunbookGetOutput(BaseModel):
    runbook: Runbook | None = None
    note: str | None = None
