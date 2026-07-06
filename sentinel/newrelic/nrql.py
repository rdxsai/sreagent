"""NRQL builders for the live store. Pure functions in, query strings out.

All timestamps are epoch milliseconds; NRQL SINCE/UNTIL accept them directly.
LIMIT MAX caps results at 5,000 rows and NRQL has no ORDER BY, so callers
paginate by slicing the time window (see NewRelicStore hydration).
"""

from __future__ import annotations

SPAN_COLUMNS = (
    "timestamp, trace.id, id, parent.id, duration.ms, name, "
    "service.name, span.kind, otel.status_code, rpc.method, rpc.service"
)
LOG_COLUMNS = "timestamp, message, severity.text, level, service.name, trace.id"
CHANGE_COLUMNS = "timestamp, changeId, service, kind, summary, diffTouches"


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def spans_page(start_ms: int, end_ms: int) -> str:
    return f"SELECT {SPAN_COLUMNS} FROM Span SINCE {start_ms} UNTIL {end_ms} LIMIT MAX"


def trace_spans(trace_id: str, start_ms: int, end_ms: int) -> str:
    return (
        f"SELECT {SPAN_COLUMNS} FROM Span WHERE trace.id = '{_q(trace_id)}' "
        f"SINCE {start_ms} UNTIL {end_ms} LIMIT MAX"
    )


def metric_latency_p95(service: str, start_ms: int, end_ms: int, bucket_s: int) -> str:
    return (
        "SELECT percentile(duration.ms, 95) AS 'p95' FROM Span "
        f"WHERE service.name = '{_q(service)}' "
        f"TIMESERIES {bucket_s} seconds SINCE {start_ms} UNTIL {end_ms}"
    )


def metric_error_rate(service: str, start_ms: int, end_ms: int, bucket_s: int) -> str:
    return (
        "SELECT filter(count(*), WHERE otel.status_code = 'ERROR') / count(*) "
        f"AS 'error_rate' FROM Span WHERE service.name = '{_q(service)}' "
        f"TIMESERIES {bucket_s} seconds SINCE {start_ms} UNTIL {end_ms}"
    )


def metric_memory_mb(service: str, start_ms: int, end_ms: int, bucket_s: int) -> str:
    return (
        "SELECT max(container.memory.usage.total) / 1e6 AS 'memory_mb' FROM Metric "
        f"WHERE container.name = '{_q(service)}' "
        f"TIMESERIES {bucket_s} seconds SINCE {start_ms} UNTIL {end_ms}"
    )


def logs_search(start_ms: int, end_ms: int, service: str | None, contains: str | None) -> str:
    conditions = []
    if service is not None:
        conditions.append(f"service.name = '{_q(service)}'")
    if contains is not None:
        conditions.append(f"message LIKE '%{_q(contains)}%'")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return f"SELECT {LOG_COLUMNS} FROM Log{where} SINCE {start_ms} UNTIL {end_ms} LIMIT MAX"


def logs_for_trace(trace_id: str, start_ms: int, end_ms: int) -> str:
    return (
        f"SELECT {LOG_COLUMNS} FROM Log WHERE trace.id = '{_q(trace_id)}' "
        f"SINCE {start_ms} UNTIL {end_ms} LIMIT MAX"
    )


def list_services(start_ms: int, end_ms: int) -> str:
    return f"SELECT uniques(service.name, 1000) FROM Span SINCE {start_ms} UNTIL {end_ms}"


def changes(start_ms: int, end_ms: int) -> str:
    return f"SELECT {CHANGE_COLUMNS} FROM SentinelChange SINCE {start_ms} UNTIL {end_ms} LIMIT MAX"
