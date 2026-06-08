"""Golden tests for the traces tools, anchored on the two recorded faults."""

from __future__ import annotations

from pathlib import Path

from sentinel.tools import traces
from sentinel.tools.models import GetTraceInput, NoArgs, TracesFindInput
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
FAILURE = FixtureStore(ROOT / "fixtures" / "payment_failure_001" / "public")
UNREACHABLE = FixtureStore(ROOT / "fixtures" / "payment_unreachable_001" / "public")


def test_topology_has_core_edges() -> None:
    topo = traces.traces_build_topology(NoArgs(), FAILURE)
    edges = {(e.caller, e.callee) for e in topo.edges}
    assert ("checkout", "payment") in edges
    assert ("frontend", "checkout") in edges
    assert {"payment", "checkout", "frontend"} <= set(topo.services)


def test_first_error_time_leads_the_metric_alert() -> None:
    onset = traces.traces_first_error_time(NoArgs(), FAILURE)
    assert onset.found
    # injection at second 300; the first error span lands after it, within the 600s window.
    assert onset.second is not None and 300 < onset.second < 600


def test_error_origin_service_fault() -> None:
    origin = traces.traces_error_origin(NoArgs(), FAILURE)
    assert origin.classification == "service"
    assert origin.service == "payment"


def test_error_origin_edge_fault() -> None:
    origin = traces.traces_error_origin(NoArgs(), UNREACHABLE)
    assert origin.classification == "edge"
    assert origin.caller == "checkout"
    assert origin.callee == "payment"


def test_traces_find_callee_filter_counts() -> None:
    q = TracesFindInput(service="checkout", span_kind="client", status="ERROR", callee="payment")
    # Both faults produce checkout->payment client errors; exact counts vary by recording.
    assert traces.traces_find(q, FAILURE).total_matched > 0
    assert traces.traces_find(q, UNREACHABLE).total_matched > 0


def test_traces_find_truncates_with_note() -> None:
    q = TracesFindInput(status="ERROR", limit=10)
    out = traces.traces_find(q, FAILURE)
    assert out.returned == 10
    assert out.truncated is True
    assert out.total_matched > 10
    assert out.note


def test_get_trace_returns_summaries() -> None:
    err = FAILURE.find_spans(service="payment", span_kind="server", status="ERROR")[0]
    out = traces.traces_get_trace(GetTraceInput(trace_id=err.trace_id), FAILURE)
    assert out.count == len(out.spans)
    assert all(s.trace_id == err.trace_id for s in out.spans)
