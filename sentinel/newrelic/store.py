"""Live TelemetryStore over New Relic: the hybrid query planner.

Aggregate-shaped methods compile to NRQL per call (metric series, log search,
trace fetch, service list, change list). Join-shaped methods (all_spans,
children_of, callee_of, find_spans) lazily hydrate a local span index once:
NRQL has no joins and caps results at 5,000 rows, so the store slices the
incident window into chunks, splits any full chunk, dedups by span_id, and
then serves the same in-memory shapes FixtureStore builds.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import structlog

from sentinel.fixtures.schemas import (
    ChangeEvent,
    DerivedAlert,
    LogRow,
    MetricRow,
    TimeWindow,
    TraceRow,
)
from sentinel.newrelic import mapping, nrql
from sentinel.tools.store import _SEVERITY_RANK, callee_from, filter_spans

log = structlog.get_logger("sentinel.newrelic")

_INGEST_LAG_MS = 30_000
_CHANGE_LOOKBACK_MS = 3_600_000
_MIN_CHUNK_MS = 2_000
_PAGE_LIMIT = 5_000

_METRIC_BUILDERS = {
    "latency_p95_ms": nrql.metric_latency_p95,
    "request_error_rate": nrql.metric_error_rate,
}


class NewRelicStore:
    METRIC_UNITS = {"latency_p95_ms": "ms", "request_error_rate": "ratio"}

    def __init__(
        self,
        client,
        *,
        window_start_ms: int,
        window_end_ms: int,
        alerts: list[DerivedAlert] | None = None,
        bucket_seconds: int = 10,
        chunk_ms: int = 30_000,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._client = client
        self._start_ms = window_start_ms
        now = (now_ms or (lambda: int(time.time() * 1000)))()
        self._end_ms = min(window_end_ms, now - _INGEST_LAG_MS)
        self._window_end_s = max(1, (window_end_ms - window_start_ms) // 1000)
        self._alerts = list(alerts or [])
        self._bucket_s = bucket_seconds
        self._chunk_ms = chunk_ms
        self._lock = threading.Lock()
        self._spans: list[TraceRow] | None = None
        self._children: dict[str, list[TraceRow]] = {}

    # -- context ---------------------------------------------------------------

    def window(self) -> TimeWindow:
        return TimeWindow(start=0, end=self._window_end_s)

    def alerts(self) -> list[DerivedAlert]:
        return list(self._alerts)

    # -- live aggregate queries --------------------------------------------------

    def list_services(self) -> list[str]:
        results = self._client.nrql(nrql.list_services(self._start_ms, self._end_ms))
        names: list[str] = []
        for entry in results:
            value = entry.get("uniques.service.name")
            if isinstance(value, list):
                names.extend(str(v) for v in value)
        return sorted(set(names))

    def list_metric_keys(self) -> list[tuple[str, str, str]]:
        services = self.list_services()
        return sorted(
            (service, metric, unit)
            for service in services
            for metric, unit in self.METRIC_UNITS.items()
        )

    def metric_series(self, service: str, metric: str) -> list[MetricRow]:
        builder = _METRIC_BUILDERS.get(metric)
        if builder is None:
            return []
        results = self._client.nrql(builder(service, self._start_ms, self._end_ms, self._bucket_s))
        return mapping.to_metric_rows(results, service, metric, self.METRIC_UNITS[metric], self._start_ms)

    def get_trace(self, trace_id: str) -> list[TraceRow]:
        results = self._client.nrql(nrql.trace_spans(trace_id, self._start_ms, self._end_ms))
        spans = [r for r in (mapping.to_trace_row(d, self._start_ms) for d in results) if r]
        spans.sort(key=lambda r: (r.time, r.span_id))
        return spans

    def search_logs(
        self,
        *,
        service: str | None = None,
        severity_min: str | None = None,
        contains: str | None = None,
        start: int | None = None,
        end: int | None = None,
        limit: int | None = None,
    ) -> list[LogRow]:
        query = nrql.logs_search(self._start_ms, self._end_ms, service, contains)
        rows = [r for r in (mapping.to_log_row(d, self._start_ms) for d in self._client.nrql(query)) if r]
        min_rank = _SEVERITY_RANK.get(severity_min.lower(), 0) if severity_min else 0
        out = [
            r
            for r in rows
            if _SEVERITY_RANK.get(r.severity.lower(), 0) >= min_rank
            and (start is None or r.time >= start)
            and (end is None or r.time <= end)
        ]
        out.sort(key=lambda r: (r.time, r.service))
        return out[:limit] if limit is not None else out

    def logs_for_trace(self, trace_id: str) -> list[LogRow]:
        results = self._client.nrql(nrql.logs_for_trace(trace_id, self._start_ms, self._end_ms))
        rows = [r for r in (mapping.to_log_row(d, self._start_ms) for d in results) if r]
        return sorted(rows, key=lambda r: r.time)

    def list_changes(
        self,
        *,
        service: str | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> list[ChangeEvent]:
        query = nrql.changes(self._start_ms - _CHANGE_LOOKBACK_MS, self._end_ms)
        events = [e for e in (mapping.to_change_event(d, self._start_ms) for d in self._client.nrql(query)) if e]
        out = [
            e
            for e in events
            if (service is None or e.service == service)
            and (start is None or e.time >= start)
            and (end is None or e.time <= end)
        ]
        out.sort(key=lambda e: e.time)
        return out

    # -- hydrated span index -------------------------------------------------------

    def all_spans(self) -> list[TraceRow]:
        self._hydrate()
        assert self._spans is not None
        return list(self._spans)

    def children_of(self, span_id: str) -> list[TraceRow]:
        self._hydrate()
        return list(self._children.get(span_id, []))

    def callee_of(self, row: TraceRow) -> str | None:
        self._hydrate()
        return callee_from(self._children, row)

    def find_spans(
        self,
        *,
        service: str | None = None,
        span_kind: str | None = None,  # noqa: A002 - matches OTel terminology
        status: str | None = None,
        rpc_callee: str | None = None,
        operation_contains: str | None = None,
        start: int | None = None,
        end: int | None = None,
        limit: int | None = None,
    ) -> list[TraceRow]:
        self._hydrate()
        assert self._spans is not None
        return filter_spans(
            self._spans,
            self.callee_of,
            service=service,
            span_kind=span_kind,
            status=status,
            rpc_callee=rpc_callee,
            operation_contains=operation_contains,
            start=start,
            end=end,
            limit=limit,
        )

    def _hydrate(self) -> None:
        with self._lock:
            if self._spans is not None:
                return
            raw: list[dict] = []
            cursor = self._start_ms
            while cursor < self._end_ms:
                upper = min(cursor + self._chunk_ms, self._end_ms)
                raw.extend(self._fetch_chunk(cursor, upper))
                cursor = upper
            spans: list[TraceRow] = []
            seen: set[str] = set()
            for entry in raw:
                row = mapping.to_trace_row(entry, self._start_ms)
                if row and row.span_id not in seen:
                    seen.add(row.span_id)
                    spans.append(row)
            children: dict[str, list[TraceRow]] = {}
            for row in spans:
                if row.parent_span_id:
                    children.setdefault(row.parent_span_id, []).append(row)
            self._spans = spans
            self._children = children
            log.info("nr_hydration_complete", spans=len(spans), pages=len(raw) // _PAGE_LIMIT + 1)

    def _fetch_chunk(self, start_ms: int, end_ms: int) -> list[dict]:
        results = self._client.nrql(nrql.spans_page(start_ms, end_ms))
        if len(results) >= _PAGE_LIMIT and end_ms - start_ms > _MIN_CHUNK_MS:
            middle = (start_ms + end_ms) // 2
            return self._fetch_chunk(start_ms, middle) + self._fetch_chunk(middle, end_ms)
        if len(results) >= _PAGE_LIMIT:
            log.warning("nr_span_chunk_truncated", start_ms=start_ms, end_ms=end_ms)
        return results
