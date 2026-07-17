"""traces_edge_latency_origin: attribute latency to the callee where propagation
terminates, not the loudest edge, and split network faults from internal ones."""

from __future__ import annotations

from sentinel.fixtures.schemas import TraceRow
from sentinel.tools import traces
from sentinel.tools.models import EdgeLatencyInput

ONSET = 100


class _EdgeStore:
    """Minimal TelemetryStore surface the edge tool touches."""

    def __init__(self, spans: list[TraceRow], callees: dict[str, str]) -> None:
        self._spans = spans
        self._callees = callees

    def all_spans(self) -> list[TraceRow]:
        return list(self._spans)

    def callee_of(self, row: TraceRow) -> str | None:
        return self._callees.get(row.span_id)


def _case(leaf_server_post_ms: float) -> _EdgeStore:
    """frontend -> mid -> leaf, with a fault on leaf after ONSET.

    Every edge INTO leaf degrades by ~200ms; frontend->mid degrades MORE (+400ms,
    mid makes two sequential calls to leaf), making it the decoy largest shift.
    leaf's own server spans run at leaf_server_post_ms post-onset: 2ms models a
    network fault (own processing flat), 302ms an internal slowdown.
    """
    spans: list[TraceRow] = []
    callees: dict[str, str] = {}
    n = 0

    def add(svc: str, kind: str, dur: float, t: int, callee: str | None = None) -> None:
        nonlocal n
        n += 1
        sid = f"s{n}"
        spans.append(
            TraceRow(
                trace_id=f"t{n}",
                span_id=sid,
                parent_span_id=None,
                time=t,
                service=svc,
                operation="op",
                duration_ms=dur,
                status="OK",
                attributes={"span.kind": kind},
            )
        )
        if callee:
            callees[sid] = callee

    for rep in range(10):
        pre_t, post_t = rep, ONSET + rep
        # pre-onset: everything fast
        add("frontend", "client", 20.0, pre_t, "mid")
        add("frontend", "client", 5.0, pre_t, "leaf")
        add("mid", "client", 5.0, pre_t, "leaf")
        add("mid", "server", 15.0, pre_t)
        add("leaf", "server", 2.0, pre_t)
        # post-onset: every edge into leaf +200ms; frontend->mid +400ms (decoy)
        add("frontend", "client", 420.0, post_t, "mid")
        add("frontend", "client", 205.0, post_t, "leaf")
        add("mid", "client", 205.0, post_t, "leaf")
        add("mid", "server", 415.0, post_t)  # victim: includes downstream waits
        add("leaf", "server", leaf_server_post_ms, post_t)
    return _EdgeStore(spans, callees)


def test_network_fault_attributed_to_terminal_callee_not_loudest_edge() -> None:
    out = traces.traces_edge_latency_origin(EdgeLatencyInput(onset_second=ONSET), _case(2.0))
    assert out.origin_service == "leaf"  # NOT mid, despite frontend->mid being +400ms
    assert out.classification == "network_edge"
    assert out.degraded_edges[0].caller == "frontend"  # decoy edge is still reported, ranked first
    assert out.degraded_edges[0].shift_ms == 400.0
    assert any("2 of 2 known caller(s) slowed toward leaf" in e for e in out.evidence)
    assert any("victim path" in e and "frontend->mid" in e for e in out.evidence)


def test_internal_slowdown_classified_when_own_server_spans_rise() -> None:
    out = traces.traces_edge_latency_origin(EdgeLatencyInput(onset_second=ONSET), _case(302.0))
    assert out.origin_service == "leaf"
    assert out.classification == "service_internal"


def test_mid_service_nic_fault_not_pushed_downstream() -> None:
    """Fault on MID's network interface: frontend->mid and mid->leaf both degrade
    (both cross mid's NIC) but frontend->leaf stays healthy. leaf explains the
    degradation equally well, so the healthy frontend->leaf caller must exonerate
    leaf and keep attribution at mid."""
    spans: list[TraceRow] = []
    callees: dict[str, str] = {}
    n = 0

    def add(svc: str, kind: str, dur: float, t: int, callee: str | None = None) -> None:
        nonlocal n
        n += 1
        sid = f"s{n}"
        spans.append(
            TraceRow(
                trace_id=f"t{n}",
                span_id=sid,
                parent_span_id=None,
                time=t,
                service=svc,
                operation="op",
                duration_ms=dur,
                status="OK",
                attributes={"span.kind": kind},
            )
        )
        if callee:
            callees[sid] = callee

    for rep in range(10):
        pre_t, post_t = rep, ONSET + rep
        add("frontend", "client", 20.0, pre_t, "mid")
        add("frontend", "client", 5.0, pre_t, "leaf")
        add("mid", "client", 5.0, pre_t, "leaf")
        add("frontend", "client", 420.0, post_t, "mid")  # into mid: degraded
        add("frontend", "client", 5.0, post_t, "leaf")  # direct to leaf: HEALTHY
        add("mid", "client", 205.0, post_t, "leaf")  # out of mid: degraded (mid's NIC)
    out = traces.traces_edge_latency_origin(
        EdgeLatencyInput(onset_second=ONSET), _EdgeStore(spans, callees)
    )
    assert out.origin_service == "mid"
    assert any("1 of 1 known caller(s) slowed toward mid" in e for e in out.evidence)
    assert any("outbound calls degraded" in e for e in out.evidence)


def test_quiet_edges_return_no_origin() -> None:
    spans = [
        TraceRow(
            trace_id=f"t{i}",
            span_id=f"s{i}",
            parent_span_id=None,
            time=t,
            service="frontend",
            operation="op",
            duration_ms=10.0,
            status="OK",
            attributes={"span.kind": "client"},
        )
        for i, t in enumerate(list(range(10)) + list(range(ONSET, ONSET + 10)))
    ]
    store = _EdgeStore(spans, {s.span_id: "leaf" for s in spans})
    out = traces.traces_edge_latency_origin(EdgeLatencyInput(onset_second=ONSET), store)
    assert out.origin_service is None
    assert out.degraded_edges == []
    assert out.evidence  # explains that no edge degraded
