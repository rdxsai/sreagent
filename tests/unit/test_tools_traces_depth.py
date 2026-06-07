"""Golden tests for the traces depth tools."""

from __future__ import annotations

from pathlib import Path

from sentinel.tools import correlate, traces
from sentinel.tools.models import (
    AttributeFaultInput,
    ComparePrePostInput,
    ErrorSummaryInput,
    GetTraceInput,
    NoArgs,
    SlowestInput,
)
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
FAILURE = FixtureStore(ROOT / "fixtures" / "payment_failure_001" / "public")


def _payment_error_trace() -> str:
    return FAILURE.find_spans(service="payment", span_kind="server", status="ERROR")[0].trace_id


def test_slowest_operations_ranked_desc() -> None:
    out = traces.traces_slowest_operations(SlowestInput(limit=10), FAILURE)
    assert out.operations
    p95s = [o.p95_ms for o in out.operations]
    assert p95s == sorted(p95s, reverse=True)


def test_error_summary_observations_feed_attribute_fault() -> None:
    summ = traces.traces_error_summary(ErrorSummaryInput(), FAILURE)
    obs = summ.observations
    # payment's own server spans error; checkout's client spans to payment error
    assert any(o.service == "payment" and o.role == "server" and o.error_count > 0 for o in obs)
    assert any(
        o.service == "checkout" and o.role == "client" and o.callee == "payment" and o.error_count > 0
        for o in obs
    )
    # the observations are the structured input correlate_attribute_fault consumes
    attr = correlate.correlate_attribute_fault(AttributeFaultInput(observations=obs), FAILURE)
    assert attr.kind == "service"


def test_span_tree_has_root_and_depth() -> None:
    out = traces.traces_span_tree(GetTraceInput(trace_id=_payment_error_trace()), FAILURE)
    assert out.count > 0
    assert out.nodes[0].depth == 0  # depth-first order starts at a root
    assert max(n.depth for n in out.nodes) >= 1  # the trace has nesting


def test_latency_breakdown_ranked_with_root() -> None:
    out = traces.traces_latency_breakdown(GetTraceInput(trace_id=_payment_error_trace()), FAILURE)
    assert out.spans
    durs = [s.duration_ms for s in out.spans]
    assert durs == sorted(durs, reverse=True)
    assert out.root_duration_ms > 0


def test_compare_pre_post_finds_failing_charge_trace() -> None:
    onset = traces.traces_first_error_time(NoArgs(), FAILURE).second
    assert onset is not None
    out = traces.traces_compare_pre_post(
        ComparePrePostInput(operation_contains="Charge", onset_second=onset), FAILURE
    )
    assert out.failing_trace_id is not None  # payment charges fail after onset
