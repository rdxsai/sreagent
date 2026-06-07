"""FixtureStore: typed, public-only access over a recorded scenario."""

from __future__ import annotations

from pathlib import Path

from sentinel.fixtures.replay import load_public_fixture
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
FAILURE = ROOT / "fixtures" / "payment_failure_001" / "public"
UNREACHABLE = ROOT / "fixtures" / "payment_unreachable_001" / "public"


def _store(public_dir: Path) -> FixtureStore:
    return FixtureStore(public_dir)


def test_window_and_alerts() -> None:
    store = _store(FAILURE)
    assert store.window().start == 0
    assert store.window().end == 600
    names = {a.alertname for a in store.alerts()}
    assert "UserFacingDegradation" in names


def test_services_and_metric_keys() -> None:
    store = _store(FAILURE)
    services = set(store.list_services())
    assert {"payment", "checkout", "frontend"} <= services
    keys = {(s, m) for s, m, _unit in store.list_metric_keys()}
    assert ("payment", "request_error_rate") in keys
    assert ("checkout", "latency_p95_ms") in keys


def test_metric_series_sorted_and_rises_after_onset() -> None:
    store = _store(FAILURE)
    series = store.metric_series("payment", "request_error_rate")
    assert series
    assert all(r.service == "payment" and r.metric == "request_error_rate" for r in series)
    assert [r.time for r in series] == sorted(r.time for r in series)
    assert max(r.value for r in series) > 0.0  # errors appear after the fault


def test_find_spans_attribution_signature_failure() -> None:
    store = _store(FAILURE)
    payment_server_err = store.find_spans(
        service="payment", span_kind="server", status="ERROR"
    )
    assert len(payment_server_err) > 0  # service fault: payment's own SERVER spans error
    checkout_to_payment_err = store.find_spans(
        service="checkout", span_kind="client", status="ERROR", rpc_callee="payment"
    )
    assert len(checkout_to_payment_err) > 0


def test_find_spans_attribution_signature_unreachable() -> None:
    store = _store(UNREACHABLE)
    payment_server_err = store.find_spans(
        service="payment", span_kind="server", status="ERROR"
    )
    assert len(payment_server_err) == 0  # edge fault: payment is never reached, SERVER clean
    checkout_to_payment_err = store.find_spans(
        service="checkout", span_kind="client", status="ERROR", rpc_callee="payment"
    )
    assert len(checkout_to_payment_err) > 0


def test_get_trace_returns_all_spans_of_one_trace() -> None:
    store = _store(FAILURE)
    err = store.find_spans(service="payment", span_kind="server", status="ERROR")[0]
    spans = store.get_trace(err.trace_id)
    assert spans
    assert all(s.trace_id == err.trace_id for s in spans)
    assert any(s.span_id == err.span_id for s in spans)


def test_logs_for_trace_filters_by_trace_id() -> None:
    store = _store(FAILURE)
    fixture = load_public_fixture(FAILURE)
    trace_id = next(row.trace_id for row in fixture.logs if row.trace_id)
    logs = store.logs_for_trace(trace_id)
    assert logs
    assert all(row.trace_id == trace_id for row in logs)


def test_list_changes_includes_culprit_id() -> None:
    store = _store(FAILURE)
    ids = {c.id for c in store.list_changes()}
    assert {"chg_0001", "chg_0002", "chg_0003"} <= ids


def test_spans_are_deduplicated_by_span_id() -> None:
    # the recorder re-exports some spans; the store canonicalizes by span_id.
    store = _store(FAILURE)
    spans = store.all_spans()
    span_ids = [s.span_id for s in spans]
    assert len(span_ids) == len(set(span_ids))


def test_get_trace_has_no_duplicate_spans() -> None:
    store = _store(FAILURE)
    err = store.find_spans(service="payment", span_kind="server", status="ERROR")[0]
    spans = store.get_trace(err.trace_id)
    ids = [s.span_id for s in spans]
    assert len(ids) == len(set(ids))


def test_callee_of_resolves_payment_for_edge_fault() -> None:
    # In the unreachable fault the failed client span has no child server span,
    # so the callee must come from the RPC operation name.
    store = _store(UNREACHABLE)
    err = store.find_spans(service="checkout", span_kind="client", status="ERROR")
    payment_calls = [s for s in err if store.callee_of(s) == "payment"]
    assert len(payment_calls) > 0
