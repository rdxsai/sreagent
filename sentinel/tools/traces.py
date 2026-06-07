"""Trace tools: topology, onset, error origin/attribution, span search.

Attribution is trace-based on `span.kind` (the brief's non-negotiable rule).
The error frontier is the deepest erroring span with no erroring child:
a server/internal frontier means the service's own work failed (service fault);
a childless client frontier means the call left but never reached a server
(edge fault). A per-service error rate cannot make this distinction, because
client spans naming the callee pollute it.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from sentinel.fixtures.schemas import Topology, TopologyEdge
from sentinel.fixtures.schemas import TraceRow
from sentinel.registry import tool
from sentinel.tools.models import (
    ComparePrePostInput,
    ComparePrePostOutput,
    ErrorSummaryInput,
    ErrorSummaryOutput,
    FaultObservation,
    GetTraceInput,
    GetTraceOutput,
    LatencyBreakdownOutput,
    LatencyOrigin,
    LatencyOriginInput,
    NoArgs,
    Onset,
    OperationLatency,
    Origin,
    ServiceInput,
    ServiceTraceSummary,
    SlowestInput,
    SlowestOutput,
    SpanLatency,
    SpanSummary,
    SpanTreeOutput,
    TracesFindInput,
    TracesFindOutput,
    TreeNode,
)
from sentinel.tools.store import TelemetryStore, span_kind

_ERROR = "ERROR"


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


# Synchronous request-path services for latency localization. Excludes the load
# generator and proxies (their own spans are long-lived by design) and the
# async/batch consumers (which pin to the histogram ceiling), so latency_origin
# attributes to a real request-path service, not infra noise.
_APP_SERVICES = frozenset(
    {"frontend", "checkout", "cart", "currency", "product-catalog", "recommendation", "ad", "shipping", "quote", "payment"}
)


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
def traces_latency_origin(params: LatencyOriginInput, store: TelemetryStore) -> LatencyOrigin:
    """Locate where latency originates: the service whose own work is slowest.

    The latency counterpart to traces_error_origin. For each span it computes self
    time (its duration minus the slowest child it waited on), so a service that is
    slow only because a downstream is slow does not score; the service with the
    highest own self-time is the latency origin (a GC pause, a slow handler). Use
    this for slowdown incidents the error tools cannot localize.
    """
    by_service: dict[str, list[float]] = defaultdict(list)
    for span in store.find_spans(start=params.onset_second):
        if span.service not in _APP_SERVICES:
            continue  # skip load generator, proxies, and async/batch consumers
        child_max = max((c.duration_ms for c in store.children_of(span.span_id)), default=0.0)
        self_time = max(0.0, span.duration_ms - child_max)
        if self_time >= params.min_self_ms:
            by_service[span.service].append(self_time)
    ranked = sorted(
        ((svc, _p95(vals), max(vals)) for svc, vals in by_service.items()),
        key=lambda x: x[1],
        reverse=True,
    )
    if not ranked:
        return LatencyOrigin(evidence=[f"no spans with self-time >= {params.min_self_ms}ms after onset"])
    svc, p95_self, max_self = ranked[0]
    runners_up = ", ".join(f"{s}:{p:.0f}ms" for s, p, _m in ranked[1:4])
    return LatencyOrigin(
        service=svc,
        self_latency_p95_ms=p95_self,
        self_latency_max_ms=max_self,
        evidence=[
            f"{svc} has the highest own self-time (p95 {p95_self:.0f}ms, max {max_self:.0f}ms), not just waiting downstream",
            f"next: {runners_up}" if runners_up else "no other service has meaningful self-time",
        ],
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


@tool(namespace="traces")
def traces_slowest_operations(params: SlowestInput, store: TelemetryStore) -> SlowestOutput:
    """Rank operations by p95 latency to find the latency hotspot.

    Groups spans by (service, operation, kind) and returns the slowest by p95 with
    counts. Scope with start (>= onset) to focus post-onset, and status to compare
    error vs ok latency. Use it to locate a slow service (a GC pause, a slow
    dependency) the way traces_error_origin locates a failing one.
    """
    spans = store.find_spans(status=params.status, start=params.start)
    groups: dict[tuple[str, str, str | None], list[float]] = defaultdict(list)
    for span in spans:
        groups[(span.service, span.operation, span_kind(span))].append(span.duration_ms)
    operations = [
        OperationLatency(
            service=svc, operation=op, span_kind=kind, count=len(durs), p95_ms=_p95(durs), max_ms=max(durs)
        )
        for (svc, op, kind), durs in groups.items()
    ]
    operations.sort(key=lambda o: o.p95_ms, reverse=True)
    return SlowestOutput(operations=operations[: params.limit])


@tool(namespace="traces")
def traces_error_summary(params: ErrorSummaryInput, store: TelemetryStore) -> ErrorSummaryOutput:
    """Summarize error spans per (service, server/client) as fault observations.

    Returns one observation per erroring service-and-kind (client observations name
    the callee), ranked by count. This is the structured input correlate_attribute_fault
    consumes to decide node vs edge, so chain the two: error_summary -> attribute_fault.
    """
    counts: dict[tuple[str, str, str | None], int] = defaultdict(int)
    for span in store.find_spans(status=_ERROR, start=params.start):
        kind = span_kind(span)
        if kind == "server":
            counts[(span.service, "server", None)] += 1
        elif kind == "client":
            counts[(span.service, "client", store.callee_of(span))] += 1
    observations = [
        FaultObservation(service=svc, role=role, error_count=n, callee=callee)  # type: ignore[arg-type]
        for (svc, role, callee), n in counts.items()
    ]
    observations.sort(key=lambda o: o.error_count, reverse=True)
    return ErrorSummaryOutput(observations=observations)


@tool(namespace="traces")
def traces_span_tree(params: GetTraceInput, store: TelemetryStore) -> SpanTreeOutput:
    """Render one trace as a depth-first call tree (depth = nesting level).

    Unlike traces_get_trace (a flat list), this shows the caller -> callee hierarchy
    so you can see where in the call tree the error or slowdown sits. Use a trace_id
    from traces_find / traces_error_origin / correlate_metric_to_traces.
    """
    spans = store.get_trace(params.trace_id)
    ids = {s.span_id for s in spans}
    children: dict[str, list[TraceRow]] = defaultdict(list)
    for span in spans:
        if span.parent_span_id in ids:
            children[span.parent_span_id].append(span)
    roots = [s for s in spans if not s.parent_span_id or s.parent_span_id not in ids]
    nodes: list[TreeNode] = []

    def walk(span: TraceRow, depth: int) -> None:
        nodes.append(
            TreeNode(
                depth=depth, service=span.service, operation=span.operation,
                span_kind=span_kind(span), status=span.status, duration_ms=span.duration_ms,
            )
        )
        for child in sorted(children.get(span.span_id, []), key=lambda x: (x.time, x.span_id)):
            walk(child, depth + 1)

    for root in sorted(roots, key=lambda x: (x.time, x.span_id)):
        walk(root, 0)
    return SpanTreeOutput(nodes=nodes, count=len(nodes))


@tool(namespace="traces")
def traces_latency_breakdown(params: GetTraceInput, store: TelemetryStore) -> LatencyBreakdownOutput:
    """Rank a trace's spans by duration to find which one dominates its latency.

    Returns spans slowest first with each one's share of the root span's duration,
    so on a slow request you can see whether the time is spent in the service itself
    or in a downstream call.
    """
    spans = store.get_trace(params.trace_id)
    if not spans:
        return LatencyBreakdownOutput(spans=[], root_duration_ms=0.0)
    ids = {s.span_id for s in spans}
    roots = [s for s in spans if not s.parent_span_id or s.parent_span_id not in ids]
    root_dur = max((r.duration_ms for r in roots), default=max(s.duration_ms for s in spans))
    ranked = sorted(spans, key=lambda s: s.duration_ms, reverse=True)
    return LatencyBreakdownOutput(
        spans=[
            SpanLatency(
                service=s.service, operation=s.operation, duration_ms=s.duration_ms,
                pct_of_root=(s.duration_ms / root_dur if root_dur else 0.0),
            )
            for s in ranked
        ],
        root_duration_ms=root_dur,
    )


@tool(namespace="traces")
def traces_compare_pre_post(params: ComparePrePostInput, store: TelemetryStore) -> ComparePrePostOutput:
    """Find a healthy pre-onset trace and a failing post-onset trace of the same operation.

    Returns one OK trace_id from before onset and one ERROR trace_id from at/after
    onset for operations matching the substring (e.g. 'Charge', 'PlaceOrder'). Open
    both with traces_span_tree to see exactly what the fault changed in the call path.
    """
    healthy = store.find_spans(
        operation_contains=params.operation_contains, status="OK", end=params.onset_second - 1
    )
    failing = store.find_spans(
        operation_contains=params.operation_contains, status=_ERROR, start=params.onset_second
    )
    h = healthy[0].trace_id if healthy else None
    f = failing[0].trace_id if failing else None
    note = None
    if h is None:
        note = "no healthy pre-onset trace for that operation"
    elif f is None:
        note = "no failing post-onset trace for that operation"
    return ComparePrePostOutput(healthy_trace_id=h, failing_trace_id=f, note=note)


@tool(namespace="traces")
def traces_service_summary(params: ServiceInput, store: TelemetryStore) -> ServiceTraceSummary:
    """Summarize one service's traffic from the spans: count, errors, error rate, p95.

    A trace-derived RED view (the metrics tools give the metric-derived one). The
    server_error_spans count is the own-fault signal: a service with server errors
    failed its own work, versus one whose errors are only client spans to a failing
    dependency.
    """
    spans = [s for s in store.all_spans() if s.service == params.service]
    total = len(spans)
    errors = [s for s in spans if s.status.upper() == _ERROR]
    server_errors = [s for s in errors if span_kind(s) == "server"]
    return ServiceTraceSummary(
        service=params.service,
        total_spans=total,
        error_spans=len(errors),
        error_rate=(len(errors) / total if total else 0.0),
        p95_ms=_p95([s.duration_ms for s in spans]),
        server_error_spans=len(server_errors),
    )
