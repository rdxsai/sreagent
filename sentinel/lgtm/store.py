"""Live TelemetryStore over a local LGTM stack: Prometheus (PromQL), Loki (LogQL),
Tempo (TraceQL + trace-by-id).

Aggregate-shaped methods query each store per call (metric series, log search,
service/metric lists). Join-shaped methods (all_spans, children_of, callee_of,
find_spans) lazily hydrate a local span index once, because Tempo search returns
matched traces, not a joinable span table: the store enumerates trace ids with a
TraceQL matcher over the pre- and post-onset windows, fetches each trace by id for
complete spans (parent links included), dedups by span_id, and then serves the
same in-memory shapes FixtureStore and NewRelicStore build.

Timestamps in the stores are absolute (the feeder rebased them to be recent); the
store converts them to seconds-since-window-start, the convention the tools expect.
"""

from __future__ import annotations

import base64
import binascii
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import structlog

from sentinel.fixtures.schemas import (
    ChangeEvent,
    DerivedAlert,
    LogRow,
    MetricRow,
    TimeWindow,
    TraceRow,
)
from sentinel.newrelic.mapping import rel_seconds
from sentinel.tools.store import _SEVERITY_RANK, callee_from, filter_spans

log = structlog.get_logger("sentinel.lgtm")

_METRIC_UNITS = {
    "cpu_utilization": "ratio",
    "memory_mb": "MB",
    "latency_p95_ms": "ms",
    "latency_p50_ms": "ms",
    "request_error_rate": "ratio",
}
# Tempo's tight-window search flakes at the trailing edge and its {} matcher
# returns nothing, so enumerate with a real matcher over a widened window.
_SEARCH_WIDEN_S = 600
_ENUM_MATCHER = '{ span.span.kind =~ ".+" }'


def _hexid(value: str) -> str:
    """Tempo's /api/traces returns OTLP-JSON ids base64-encoded; our data is hex."""
    try:
        return binascii.hexlify(base64.b64decode(value)).decode()
    except (binascii.Error, ValueError):
        return value


def _attr(attrs: list[dict], key: str) -> str | None:
    for a in attrs or []:
        if a.get("key") == key:
            v = a.get("value", {})
            return v.get("stringValue") or v.get("intValue") or v.get("boolValue")
    return None


class LgtmStore:
    def __init__(
        self,
        *,
        prometheus_url: str,
        loki_url: str,
        tempo_url: str,
        window_start_ms: int,
        window_end_ms: int,
        onset_second: int,
        alerts: list[DerivedAlert] | None = None,
        step_seconds: int = 15,
        trace_cap: int = 1000,
        fetch_workers: int = 16,
        now_s: int | None = None,
    ) -> None:
        self._prom = prometheus_url.rstrip("/")
        self._loki = loki_url.rstrip("/")
        self._tempo = tempo_url.rstrip("/")
        self._start_ms = window_start_ms
        self._end_ms = window_end_ms
        self._start_s = window_start_ms // 1000
        self._end_s = window_end_ms // 1000 + 1
        self._onset_s = onset_second
        self._window_end_s = max(1, (window_end_ms - window_start_ms) // 1000)
        self._alerts = list(alerts or [])
        self._step = step_seconds
        self._trace_cap = trace_cap
        self._workers = fetch_workers
        self._now_s = now_s
        self._http = httpx.Client(timeout=60)
        self._lock = threading.Lock()
        self._spans: list[TraceRow] | None = None
        self._children: dict[str, list[TraceRow]] = {}
        self.stats: dict[str, int] = {"traces_fetched": 0, "hydrated_spans": 0}

    def _get(self, url: str, params: dict) -> dict:
        r = self._http.get(url, params=params)
        r.raise_for_status()
        return r.json()

    # -- context ---------------------------------------------------------------

    def window(self) -> TimeWindow:
        return TimeWindow(start=0, end=self._window_end_s)

    def alerts(self) -> list[DerivedAlert]:
        return list(self._alerts)

    # -- Prometheus (metrics) ---------------------------------------------------

    def list_services(self) -> list[str]:
        d = self._get(f"{self._prom}/api/v1/label/job/values", {})
        return sorted(str(s) for s in d.get("data", []))

    def list_metric_keys(self) -> list[tuple[str, str, str]]:
        d = self._get(
            f"{self._prom}/api/v1/series",
            {"match[]": '{__name__=~".+"}', "start": self._start_s, "end": self._end_s},
        )
        keys: set[tuple[str, str, str]] = set()
        for s in d.get("data", []):
            metric, service = s.get("__name__"), s.get("job")
            if metric and service:
                keys.add((service, metric, _METRIC_UNITS.get(metric, "count")))
        return sorted(keys)

    def metric_series(self, service: str, metric: str) -> list[MetricRow]:
        d = self._get(
            f"{self._prom}/api/v1/query_range",
            {"query": f'{metric}{{job="{service}"}}',
             "start": self._start_s, "end": self._end_s, "step": self._step},
        )
        unit = _METRIC_UNITS.get(metric, "count")
        rows: list[MetricRow] = []
        for series in d.get("data", {}).get("result", []):
            for ts, val in series.get("values", []):
                try:
                    value = float(val)
                except ValueError:
                    continue
                rows.append(MetricRow(
                    time=rel_seconds(float(ts) * 1000, self._start_ms),
                    service=service, metric=metric, value=value, unit=unit,
                ))
        rows.sort(key=lambda r: r.time)
        return rows

    # -- Loki (logs) ------------------------------------------------------------

    def search_logs(
        self, *, service: str | None = None, severity_min: str | None = None,
        contains: str | None = None, start: int | None = None,
        end: int | None = None, limit: int | None = None,
    ) -> list[LogRow]:
        sel = f'{{service_name="{service}"}}' if service else '{service_name=~".+"}'
        if contains:
            sel += f' |= "{contains}"'
        d = self._get(
            f"{self._loki}/loki/api/v1/query_range",
            {"query": sel, "start": self._start_ms * 10**6, "end": self._end_ms * 10**6,
             "limit": limit or 1000, "direction": "forward"},
        )
        min_rank = _SEVERITY_RANK.get(severity_min.lower(), 0) if severity_min else 0
        out: list[LogRow] = []
        for stream in d.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            svc = labels.get("service_name", "unknown")
            sev = labels.get("severity_text") or labels.get("detected_level") or labels.get("level") or "info"
            if _SEVERITY_RANK.get(sev.lower(), 2) < min_rank:
                continue
            for entry in stream.get("values", []):
                ts_ns, line = entry[0], entry[1]
                t = rel_seconds(int(ts_ns) // 10**6, self._start_ms)
                if start is not None and t < start:
                    continue
                if end is not None and t > end:
                    continue
                out.append(LogRow(time=t, service=svc, severity=sev, message=line, trace_id=None))
        out.sort(key=lambda r: (r.time, r.service))
        return out[:limit] if limit is not None else out

    def logs_for_trace(self, trace_id: str) -> list[LogRow]:
        d = self._get(
            f"{self._loki}/loki/api/v1/query_range",
            {"query": f'{{service_name=~".+"}} | trace_id="{trace_id}"',
             "start": self._start_ms * 10**6, "end": self._end_ms * 10**6, "limit": 1000},
        )
        out: list[LogRow] = []
        for stream in d.get("data", {}).get("result", []):
            svc = stream.get("stream", {}).get("service_name", "unknown")
            for ts_ns, line in stream.get("values", []):
                out.append(LogRow(time=rel_seconds(int(ts_ns) // 10**6, self._start_ms),
                                  service=svc, severity="info", message=line, trace_id=trace_id))
        return sorted(out, key=lambda r: r.time)

    # -- changes ----------------------------------------------------------------

    def list_changes(self, *, service=None, start=None, end=None) -> list[ChangeEvent]:
        return []  # RCAEval scenarios carry no change events

    # -- Tempo (traces): hydrated span index ------------------------------------

    def get_trace(self, trace_id: str) -> list[TraceRow]:
        try:
            d = self._get(f"{self._tempo}/api/traces/{trace_id}", {})
        except httpx.HTTPStatusError:
            return []
        spans = self._parse_trace(d)
        spans.sort(key=lambda r: (r.time, r.span_id))
        return spans

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
        self, *, service=None, span_kind=None, status=None, rpc_callee=None,  # noqa: A002
        operation_contains=None, start=None, end=None, limit=None,
    ) -> list[TraceRow]:
        self._hydrate()
        assert self._spans is not None
        return filter_spans(
            self._spans, self.callee_of, service=service, span_kind=span_kind,
            status=status, rpc_callee=rpc_callee, operation_contains=operation_contains,
            start=start, end=end, limit=limit,
        )

    def _enum_trace_ids(self) -> list[str]:
        """Enumerate trace ids over the window, DETERMINISTICALLY. Tempo only returns
        results when the search end is ~wall-clock now (a past end returns nothing), and
        its return order is not stable, so for reproducibility we over-fetch a superset,
        sort the ids, and keep the first trace_cap. Trace ids are uncorrelated with time,
        so the lexicographic prefix samples the whole window evenly. Same scenario + same
        now_s yields the same span set; model sampling still varies, the telemetry does not."""
        now_s = self._now_s or int(time.time())
        d = self._get(f"{self._tempo}/api/search",
                      {"q": _ENUM_MATCHER, "start": self._start_s - _SEARCH_WIDEN_S,
                       "end": now_s, "limit": max(self._trace_cap * 4, 6000)})
        ids = sorted(t["traceID"] for t in d.get("traces", []) if t.get("traceID"))
        return ids[: self._trace_cap]

    def _parse_trace(self, doc: dict) -> list[TraceRow]:
        out: list[TraceRow] = []
        for batch in doc.get("batches", []):
            svc = _attr(batch.get("resource", {}).get("attributes", []), "service.name") or "unknown"
            for scope in batch.get("scopeSpans", []):
                for sp in scope.get("spans", []):
                    start_ns = int(sp.get("startTimeUnixNano", 0))
                    end_ns = int(sp.get("endTimeUnixNano", start_ns))
                    code = sp.get("status", {}).get("code")
                    is_err = code == 2 or code == "STATUS_CODE_ERROR"
                    kind = _attr(sp.get("attributes", []), "span.kind") or "internal"
                    parent = sp.get("parentSpanId")
                    out.append(TraceRow(
                        trace_id=_hexid(sp.get("traceId", "")),
                        span_id=_hexid(sp.get("spanId", "")),
                        parent_span_id=_hexid(parent) if parent else None,
                        time=rel_seconds(start_ns / 1e6, self._start_ms),
                        service=svc,
                        operation=sp.get("name", "unknown"),
                        duration_ms=max(0.0, (end_ns - start_ns) / 1e6),
                        status="ERROR" if is_err else "OK",
                        attributes={"span.kind": kind},
                    ))
        return out

    def _hydrate(self) -> None:
        with self._lock:
            if self._spans is not None:
                return
            trace_ids = self._enum_trace_ids()
            spans: list[TraceRow] = []
            seen: set[str] = set()
            with ThreadPoolExecutor(max_workers=self._workers) as pool:
                for trace in pool.map(self.get_trace, trace_ids):
                    self.stats["traces_fetched"] += 1
                    for row in trace:
                        if row.span_id and row.span_id not in seen:
                            seen.add(row.span_id)
                            spans.append(row)
            children: dict[str, list[TraceRow]] = {}
            for row in spans:
                if row.parent_span_id:
                    children.setdefault(row.parent_span_id, []).append(row)
            self._spans = spans
            self._children = children
            self.stats["hydrated_spans"] = len(spans)
            log.info("lgtm_hydration_complete", traces=len(trace_ids), spans=len(spans))
