"""NRQL builders are pure string functions; assert exact query text."""

from sentinel.newrelic import nrql


def test_spans_page_selects_mapping_columns_with_limit_max():
    q = nrql.spans_page(1000, 2000)
    assert q == (
        "SELECT timestamp, trace.id, id, parent.id, duration.ms, name, "
        "service.name, span.kind, otel.status_code, rpc.method, rpc.service "
        "FROM Span SINCE 1000 UNTIL 2000 LIMIT MAX"
    )


def test_trace_spans_filters_by_trace_id_and_escapes_quotes():
    q = nrql.trace_spans("abc'123", 0, 5000)
    assert "WHERE trace.id = 'abc\\'123'" in q
    assert q.endswith("SINCE 0 UNTIL 5000 LIMIT MAX")


def test_metric_latency_p95_uses_alias_and_timeseries():
    q = nrql.metric_latency_p95("payment", 0, 60000, 10)
    assert q == (
        "SELECT percentile(duration.ms, 95) AS 'p95' FROM Span "
        "WHERE service.name = 'payment' "
        "TIMESERIES 10 seconds SINCE 0 UNTIL 60000"
    )


def test_metric_error_rate_uses_status_code_filter():
    q = nrql.metric_error_rate("checkout", 0, 60000, 10)
    assert q == (
        "SELECT filter(count(*), WHERE otel.status_code = 'ERROR') / count(*) "
        "AS 'error_rate' FROM Span WHERE service.name = 'checkout' "
        "TIMESERIES 10 seconds SINCE 0 UNTIL 60000"
    )


def test_logs_search_optional_filters():
    plain = nrql.logs_search(0, 1000, None, None)
    assert plain == (
        "SELECT timestamp, message, severity.text, level, service.name, trace.id "
        "FROM Log SINCE 0 UNTIL 1000 LIMIT MAX"
    )
    filtered = nrql.logs_search(0, 1000, "payment", "charge failed")
    assert "WHERE service.name = 'payment' AND message LIKE '%charge failed%'" in filtered


def test_logs_for_trace():
    q = nrql.logs_for_trace("t1", 0, 1000)
    assert "FROM Log WHERE trace.id = 't1'" in q


def test_list_services_uses_uniques():
    assert nrql.list_services(0, 1000) == (
        "SELECT uniques(service.name, 1000) FROM Span SINCE 0 UNTIL 1000"
    )


def test_changes_reads_sentinelchange_table():
    assert nrql.changes(5000, 9000) == (
        "SELECT timestamp, changeId, service, kind, summary, diffTouches "
        "FROM SentinelChange SINCE 5000 UNTIL 9000 LIMIT MAX"
    )
