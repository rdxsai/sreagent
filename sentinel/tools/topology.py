"""Topology tools: reason over the service dependency graph.

These build on the caller -> callee edges derived from client spans (the same
pairing traces_build_topology uses). They answer the graph questions an SRE asks
while scoping an incident: who depends on a service, how the user reaches it,
which subgraph started erroring, and where error propagation terminates. Use
traces_build_topology first for the whole map; use these to drill into one node.
"""

from __future__ import annotations

from collections import defaultdict, deque

from sentinel.fixtures.schemas import TopologyEdge
from sentinel.registry import tool
from sentinel.tools.models import (
    BlastRadius,
    CriticalPath,
    CriticalPathInput,
    Dependencies,
    NoArgs,
    OnsetWindowInput,
    OriginPathOutput,
    ServiceInput,
    TopologyDelta,
)
from sentinel.tools.store import TelemetryStore, span_kind

_ERROR = "ERROR"


def _edges(
    store: TelemetryStore,
    *,
    error_only: bool = False,
    start: int | None = None,
    end: int | None = None,
) -> set[tuple[str, str]]:
    """Derive caller -> callee edges from client spans, optionally error-only / windowed."""
    edges: set[tuple[str, str]] = set()
    for span in store.all_spans():
        if span_kind(span) != "client":
            continue
        if error_only and span.status.upper() != _ERROR:
            continue
        if start is not None and span.time < start:
            continue
        if end is not None and span.time >= end:
            continue
        callee = store.callee_of(span)
        if callee and callee != span.service:
            edges.add((span.service, callee))
    return edges


def _reachable(edges: set[tuple[str, str]], start: str, *, reverse: bool) -> set[str]:
    adj: dict[str, set[str]] = defaultdict(set)
    for caller, callee in edges:
        (adj[callee].add(caller) if reverse else adj[caller].add(callee))
    seen: set[str] = set()
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for nxt in adj[node]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    seen.discard(start)
    return seen


def _shortest_path(edges: set[tuple[str, str]], src: str, dst: str) -> list[str]:
    adj: dict[str, set[str]] = defaultdict(set)
    for caller, callee in edges:
        adj[caller].add(callee)
    prev: dict[str, str | None] = {src: None}
    queue = deque([src])
    while queue:
        node = queue.popleft()
        if node == dst:
            break
        for nxt in sorted(adj[node]):
            if nxt not in prev:
                prev[nxt] = node
                queue.append(nxt)
    if dst not in prev:
        return []
    path: list[str] = []
    cur: str | None = dst
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    return list(reversed(path))


@tool(namespace="topology")
def topology_dependencies(params: ServiceInput, store: TelemetryStore) -> Dependencies:
    """List a service's direct callers and callees (one hop each way).

    Use this to orient locally around a suspect: callers are who would see its
    failures, callees are its immediate dependencies (possible upstream causes).
    For the full transitive impact use topology_blast_radius instead.
    """
    edges = _edges(store)
    callers = sorted({caller for caller, callee in edges if callee == params.service})
    callees = sorted({callee for caller, callee in edges if caller == params.service})
    return Dependencies(service=params.service, callers=callers, callees=callees)


@tool(namespace="topology")
def topology_blast_radius(params: ServiceInput, store: TelemetryStore) -> BlastRadius:
    """Compute a service's transitive blast radius: everything it can affect or be affected by.

    `upstream` is every service that transitively calls it (who is impacted if it
    degrades); `downstream` is every service it transitively depends on (where an
    upstream cause could originate). Use this to scope which services a hypothesis
    can plausibly involve before investigating each.
    """
    edges = _edges(store)
    return BlastRadius(
        service=params.service,
        upstream=sorted(_reachable(edges, params.service, reverse=True)),
        downstream=sorted(_reachable(edges, params.service, reverse=False)),
    )


@tool(namespace="topology")
def topology_critical_path(params: CriticalPathInput, store: TelemetryStore) -> CriticalPath:
    """Find the call path from the user-facing entry point to a target service.

    Returns the shortest caller -> callee chain from `source` (default frontend)
    to `target`, e.g. frontend -> checkout -> payment. Use it to understand how a
    user request reaches a backend service and which hops a failure traverses.
    """
    edges = _edges(store)
    path = _shortest_path(edges, params.source, params.target)
    return CriticalPath(source=params.source, target=params.target, path=path, found=bool(path))


@tool(namespace="topology")
def topology_locate_origin(params: NoArgs, store: TelemetryStore) -> OriginPathOutput:
    """Localize the fault by following the error edges to where propagation terminates.

    Builds the subgraph of caller -> callee edges that are erroring and walks to
    the terminal callee (the deepest one that is not itself a caller of another
    error edge). If that terminal service's own server spans also error, it is a
    service fault there; if not, the call never landed and it is an edge fault on
    the last hop. This is the graph-level companion to traces_error_origin; use
    both to corroborate node-vs-edge attribution.
    """
    err_edges = _edges(store, error_only=True)
    if not err_edges:
        return OriginPathOutput(
            classification="unknown", terminal_has_server_error=False, evidence=["no error edges found"]
        )
    callers = {caller for caller, _ in err_edges}
    callees = {callee for _, callee in err_edges}
    terminals = sorted(callees - callers) or sorted(callees)
    terminal = terminals[0]
    server_error_services = {
        s.service for s in store.all_spans() if s.status.upper() == _ERROR and span_kind(s) == "server"
    }
    has_server = terminal in server_error_services
    entries = sorted(callers - callees)
    path = _shortest_path(err_edges, entries[0], terminal) if entries else [terminal]
    classification = "service" if has_server else "edge"
    evidence = [
        f"error edges: {sorted(err_edges)}",
        f"terminal callee: {terminal} (own server spans error: {has_server})",
    ]
    return OriginPathOutput(
        classification=classification,
        origin_service=terminal if has_server else None,
        path=path,
        terminal_has_server_error=has_server,
        evidence=evidence,
    )


@tool(namespace="topology")
def topology_compare(params: OnsetWindowInput, store: TelemetryStore) -> TopologyDelta:
    """Show which dependency edges began erroring only after onset.

    Compares error edges before vs after `onset_second`; the new ones are the
    edges the fault introduced, which trace the propagation path through the
    graph. Anchor `onset_second` on traces_first_error_time.
    """
    before = _edges(store, error_only=True, end=params.onset_second)
    after = _edges(store, error_only=True, start=params.onset_second)
    new = sorted(after - before)
    note = None if new else "no new error edges after onset; the fault may predate the window or not be edge-visible"
    return TopologyDelta(
        new_error_edges=[TopologyEdge(caller=c, callee=e) for c, e in new],
        note=note,
    )
