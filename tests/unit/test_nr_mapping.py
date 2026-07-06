"""NerdGraph result rows map into the fixture Pydantic models with
window-relative times; malformed rows map to None instead of raising."""

from sentinel.newrelic.mapping import (
    rel_seconds,
    to_change_event,
    to_log_row,
    to_metric_rows,
    to_trace_row,
)

W = 1_751_700_000_000  # window start, epoch ms


def test_rel_seconds_clamps_to_zero():
    assert rel_seconds(W + 12_345, W) == 12
    assert rel_seconds(W - 5_000, W) == 0


def test_to_trace_row_maps_columns_and_status():
    row = to_trace_row(
        {
            "timestamp": W + 10_000,
            "trace.id": "t1",
            "id": "s1",
            "parent.id": "p1",
            "duration.ms": 42.5,
            "name": "POST /charge",
            "service.name": "payment",
            "span.kind": "server",
            "otel.status_code": "ERROR",
            "rpc.method": "oteldemo.PaymentService/Charge",
        },
        W,
    )
    assert row is not None
    assert (row.trace_id, row.span_id, row.parent_span_id) == ("t1", "s1", "p1")
    assert (row.time, row.service, row.operation) == (10, "payment", "POST /charge")
    assert row.duration_ms == 42.5
    assert row.status == "ERROR"
    assert row.attributes["span.kind"] == "server"
    assert row.attributes["rpc.method"] == "oteldemo.PaymentService/Charge"


def test_to_trace_row_defaults_ok_status_and_rejects_missing_ids():
    ok = to_trace_row(
        {"timestamp": W, "trace.id": "t", "id": "s", "name": "n", "service.name": "svc"}, W
    )
    assert ok is not None and ok.status == "OK" and ok.duration_ms == 0.0
    assert to_trace_row({"timestamp": W, "name": "n", "service.name": "svc"}, W) is None


def test_to_log_row_severity_fallback_chain():
    from_text = to_log_row(
        {"timestamp": W, "message": "m", "severity.text": "WARN", "service.name": "cart"}, W
    )
    assert from_text is not None and from_text.severity == "WARN"
    from_level = to_log_row(
        {"timestamp": W, "message": "m", "level": "error", "service.name": "cart"}, W
    )
    assert from_level is not None and from_level.severity == "error"
    assert to_log_row({"timestamp": W, "message": "", "service.name": "cart"}, W) is None


def test_to_change_event_splits_diff_touches():
    event = to_change_event(
        {
            "timestamp": W + 60_000,
            "changeId": "chg_0003",
            "service": "payment",
            "kind": "runtime_config_change",
            "summary": "payment config changed",
            "diffTouches": "charge_handler,payment_provider",
        },
        W,
    )
    assert event is not None
    assert event.id == "chg_0003" and event.time == 60
    assert event.diff_touches == ["charge_handler", "payment_provider"]


def test_to_metric_rows_parses_timeseries_with_alias_and_nested_values():
    results = [
        {"beginTimeSeconds": W // 1000, "endTimeSeconds": W // 1000 + 10, "p95": 120.5},
        {"beginTimeSeconds": W // 1000 + 10, "endTimeSeconds": W // 1000 + 20, "p95": {"95": 130.0}},
        {"beginTimeSeconds": W // 1000 + 20, "endTimeSeconds": W // 1000 + 30, "p95": None},
    ]
    rows = to_metric_rows(results, "payment", "latency_p95_ms", "ms", W)
    assert [(r.time, r.value) for r in rows] == [(0, 120.5), (10, 130.0)]
    assert all(r.service == "payment" and r.metric == "latency_p95_ms" and r.unit == "ms" for r in rows)
