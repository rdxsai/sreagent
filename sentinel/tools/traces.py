"""Trace tools: topology, onset, error origin/attribution, span search.

Attribution is trace-based on `span.kind` (the brief's non-negotiable rule).
The error frontier is the deepest erroring span with no erroring child:
a server/internal frontier means the service's own work failed (service fault);
a childless client frontier means the call left but never reached a server
(edge fault). A per-service error rate cannot make this distinction, because
client spans naming the callee pollute it.
"""

from __future__ import annotations

from collections import Counter

from sentinel.fixtures.schemas import Topology, TopologyEdge
from sentinel.fixtures.schemas import TraceRow
from sentinel.registry import tool
from sentinel.tools.models import (
    GetTraceInput,
    GetTraceOutput,
    NoArgs,
    Onset,
    Origin,
    SpanSummary,
    TracesFindInput,
    TracesFindOutput,
)
from sentinel.tools.store import TelemetryStore, span_kind

_ERROR = "ERROR"


def _is_error(row: TraceRow) -> bool:
    return row.status.upper() == _ERROR


def _summary(row: TraceRow, store: TelemetryStore) -> SpanSummary:
    kind = span_kind(row)
    return SpanSummary(
        trace_id=row.trace_id,
        span_id=row.span_id,
        parent_span_id=row.parent_span_id,
        service=row.service,
        operation=row.operation,
        span_kind=kind,
        status=row.status,
        duration_ms=row.duration_ms,
        time=row.time,
        callee=store.callee_of(row) if kind == "client" else None,
    )


@tool(namespace="traces")
def traces_build_topology(params: NoArgs, store: TelemetryStore) -> Topology:
    """Build the service dependency graph from the recorded spans.

    Derives caller -> callee edges by pairing each client span with the server
    span it calls (and falling back to the RPC operation name when the call
    failed before a server span was created). Use this first to orient: it tells
    you which services depend on which, so you can scope hypotheses to the blast
    radius around a failure.
    """
    services = set(store.list_services())
    edges: set[tuple[str, str]] = set()
    for span in store.all_spans():
        if span_kind(span) != "client":
            continue
        callee = store.callee_of(span)
        if callee and callee != span.service:
            edges.add((span.service, callee))
            services.add(callee)
    return Topology(
        services=sorted(services),
        edges=[TopologyEdge(caller=c, callee=e) for c, e in sorted(edges)],
    )


@tool(namespace="traces")
def traces_first_error_time(params: NoArgs, store: TelemetryStore) -> Onset:
    """Find the true onset: the time of the first error span.

    This leads the metric alert, which lags real onset by the rate window. Use
    this onset (not the alert time) as the anchor for change correlation.
    """
    errors = [s for s in store.all_spans() if _is_error(s)]
    if not errors:
        return Onset(found=False)
    first = min(errors, key=lambda s: (s.time, s.span_id))
    return Onset(
        found=True,
        second=first.time,
        service=first.service,
        span_kind=span_kind(first),
        trace_id=first.trace_id,
    )


@tool(namespace="traces")
def traces_error_origin(params: NoArgs, store: TelemetryStore) -> Origin:
    """Locate where the failure originates and classify it as a service or edge fault.

    Walks the error spans to the deepest one with no erroring child. A
    server/internal frontier means the callee's own work failed (service fault).
    A childless client frontier means the call never reached a server (edge
    fault from caller to callee). This is the trace-based attribution the
    benchmark requires; do not infer node vs edge from an error-rate metric.
    """
    spans = store.all_spans()
    by_id = {s.span_id: s for s in spans}
    error_ids = {s.span_id for s in spans if _is_error(s)}
    if not error_ids:
        return Origin(classification="unknown", evidence=["no error spans found"])

    server_error_services = {
        s.service for s in spans if _is_error(s) and span_kind(s) == "server"
    }

    def has_error_child(span_id: str) -> bool:
        return any(c.span_id in error_ids for c in store.children_of(span_id))

    def depth(span: TraceRow) -> int:
        d, cur = 0, span
        seen: set[str] = set()
        while cur.parent_span_id and cur.parent_span_id in by_id and cur.span_id not in seen:
            seen.add(cur.span_id)
            cur = by_id[cur.parent_span_id]
            d += 1
        return d

    leaves = [s for s in spans if s.span_id in error_ids and not has_error_child(s.span_id)]
    if not leaves:  # every error has an error child (cycle-safe fallback)
        leaves = [s for s in spans if s.span_id in error_ids]
    max_depth = max(depth(s) for s in leaves)
    deepest = [s for s in leaves if depth(s) == max_depth]

    votes: Counter[tuple[str, str, str, str]] = Counter()
    for s in deepest:
        kind = span_kind(s) or "unknown"
        if kind in ("server", "internal"):
            votes[("service", s.service, "", "")] += 1
        elif kind == "client":
            callee = store.callee_of(s) or "unknown"
            if callee in server_error_services:
                votes[("service", callee, "", "")] += 1
            else:
                votes[("edge", "", s.service, callee)] += 1
        else:
            votes[("service", s.service, "", "")] += 1

    (kind, service, caller, callee), _ = votes.most_common(1)[0]
    first_error = min((s for s in spans if _is_error(s)), key=lambda s: (s.time, s.span_id))
    evidence = [
        f"deepest error frontier: {len(deepest)} span(s) at depth {max_depth}",
        f"services with erroring server spans: {sorted(server_error_services) or 'none'}",
    ]
    return Origin(
        classification=kind,  # type: ignore[arg-type]
        service=service or None,
        caller=caller or None,
        callee=callee or None,
        span_kind=span_kind(deepest[0]),
        first_error_second=first_error.time,
        trace_id=deepest[0].trace_id,
        evidence=evidence,
    )


@tool(namespace="traces")
def traces_find(params: TracesFindInput, store: TelemetryStore) -> TracesFindOutput:
    """Search spans by service, span kind, status, callee, operation, or time window.

    The attribution workhorse. To test a service fault, query the suspect's
    server spans with status=ERROR. To test an edge fault, query the caller's
    client spans with status=ERROR and callee=<suspect downstream>. Results are
    capped by `limit`; if truncated, add filters rather than reading everything.
    """
    matched = store.find_spans(
        service=params.service,
        span_kind=params.span_kind,
        status=params.status,
        rpc_callee=params.callee,
        operation_contains=params.operation_contains,
        start=params.start,
        end=params.end,
    )
    total = len(matched)
    shown = matched[: params.limit]
    truncated = total > params.limit
    note = (
        f"showing the first {len(shown)} of {total} matches; add filters (service, "
        f"span_kind, status, callee, time window) to narrow."
        if truncated
        else None
    )
    return TracesFindOutput(
        spans=[_summary(r, store) for r in shown],
        total_matched=total,
        returned=len(shown),
        truncated=truncated,
        note=note,
    )


@tool(namespace="traces")
def traces_get_trace(params: GetTraceInput, store: TelemetryStore) -> GetTraceOutput:
    """Fetch every span of one trace, ordered, to inspect a single failing call path.

    Use a trace_id surfaced by traces_find or traces_error_origin to see the
    full caller -> callee chain and where the error appears along it.
    """
    spans = store.get_trace(params.trace_id)
    summaries = [_summary(r, store) for r in spans]
    return GetTraceOutput(spans=summaries, count=len(summaries))
