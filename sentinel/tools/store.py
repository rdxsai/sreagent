"""The executor seam: typed, public-only access over one recorded scenario.

`FixtureStore` wraps the existing public-fixture loader and exposes the query
primitives the tools need (metric series, span filters, trace fetch, log
lookup, change list). Tools call these methods and never touch the filesystem,
so swapping `FixtureStore` for a live Prometheus/Jaeger/Loki client behind the
same surface would move the agent onto live telemetry unchanged.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Protocol

from sentinel.fixtures.replay import load_public_fixture
from sentinel.fixtures.schemas import (
    ChangeEvent,
    DerivedAlert,
    LogRow,
    MetricRow,
    TimeWindow,
    TraceRow,
)

_SEVERITY_RANK = {
    "trace": 0,
    "debug": 1,
    "info": 2,
    "information": 2,
    "notice": 2,
    "warn": 3,
    "warning": 3,
    "error": 4,
    "err": 4,
    "critical": 5,
    "fatal": 5,
}


def span_kind(row: TraceRow) -> str | None:
    value = row.attributes.get("span.kind")
    return value.lower() if isinstance(value, str) else None


_kind_of = span_kind  # stable alias: find_spans shadows the name with a parameter


def _rpc_callee(text: object) -> str | None:
    """Parse a callee service from an RPC operation like 'oteldemo.PaymentService/Charge'."""
    if not isinstance(text, str) or "/" not in text:
        return None
    left = text.split("/", 1)[0]
    candidates = [seg for seg in left.split(".") if seg.lower().endswith("service")]
    seg = candidates[-1] if candidates else None
    if not seg:
        return None
    base = seg[: -len("Service")] if seg.lower().endswith("service") else seg
    if not base:
        return None
    kebab = re.sub(r"(?<!^)(?=[A-Z])", "-", base).lower()
    return kebab or None


def callee_from(children: dict[str, list[TraceRow]], row: TraceRow) -> str | None:
    server_children = [c for c in children.get(row.span_id, []) if span_kind(c) == "server"]
    if server_children:
        return server_children[0].service
    return _rpc_callee(row.attributes.get("rpc.method") or row.operation)


def filter_spans(
    rows: list[TraceRow],
    callee_fn,
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
    out: list[TraceRow] = []
    kind_want = span_kind.lower() if span_kind else None
    status_want = status.upper() if status else None
    for r in rows:
        if service is not None and r.service != service:
            continue
        if kind_want is not None and _kind_of(r) != kind_want:
            continue
        if status_want is not None and r.status.upper() != status_want:
            continue
        if start is not None and r.time < start:
            continue
        if end is not None and r.time > end:
            continue
        if operation_contains is not None and operation_contains not in r.operation:
            continue
        if rpc_callee is not None and callee_fn(r) != rpc_callee:
            continue
        out.append(r)
    out.sort(key=lambda r: (r.time, r.trace_id, r.span_id))
    return out[:limit] if limit is not None else out


class TelemetryStore(Protocol):
    def window(self) -> TimeWindow: ...
    def alerts(self) -> list[DerivedAlert]: ...
    def list_services(self) -> list[str]: ...
    def list_metric_keys(self) -> list[tuple[str, str, str]]: ...
    def metric_series(self, service: str, metric: str) -> list[MetricRow]: ...
    def all_spans(self) -> list[TraceRow]: ...
    def find_spans(self, **filters: object) -> list[TraceRow]: ...
    def get_trace(self, trace_id: str) -> list[TraceRow]: ...
    def children_of(self, span_id: str) -> list[TraceRow]: ...
    def callee_of(self, row: TraceRow) -> str | None: ...
    def search_logs(self, **filters: object) -> list[LogRow]: ...
    def logs_for_trace(self, trace_id: str) -> list[LogRow]: ...
    def list_changes(self, **filters: object) -> list[ChangeEvent]: ...


class FixtureStore:
    def __init__(self, public_dir: Path) -> None:
        self._fixture = load_public_fixture(Path(public_dir))
        # The recorder re-exports some spans multiple times (same span_id appears
        # 4-6x with byte-identical fields). A real tracing backend keys by
        # span_id, so canonicalize here: keep one row per span_id. This removes
        # the inflation in both span counts and get_trace output; the node-vs-edge
        # attribution is unaffected since it depends on which services error, not
        # on the exact count.
        self._spans: list[TraceRow] = []
        seen_spans: set[str] = set()
        for span in self._fixture.traces:
            if span.span_id not in seen_spans:
                seen_spans.add(span.span_id)
                self._spans.append(span)
        self._children: dict[str, list[TraceRow]] = defaultdict(list)
        for span in self._spans:
            if span.parent_span_id:
                self._children[span.parent_span_id].append(span)
        self._logs_by_trace: dict[str, list[LogRow]] = defaultdict(list)
        for log in self._fixture.logs:
            if log.trace_id:
                self._logs_by_trace[log.trace_id].append(log)
        self._metric_index: dict[tuple[str, str], list[MetricRow]] = defaultdict(list)
        for row in self._fixture.metrics:
            self._metric_index[(row.service, row.metric)].append(row)

    def window(self) -> TimeWindow:
        return self._fixture.manifest.window

    def alerts(self) -> list[DerivedAlert]:
        return list(self._fixture.manifest.alerts)

    def list_services(self) -> list[str]:
        services: set[str] = set()
        services.update(r.service for r in self._spans)
        services.update(r.service for r in self._fixture.metrics)
        services.update(c.service for c in self._fixture.changes)
        return sorted(services)

    def list_metric_keys(self) -> list[tuple[str, str, str]]:
        keys: dict[tuple[str, str], str] = {}
        for row in self._fixture.metrics:
            keys.setdefault((row.service, row.metric), row.unit)
        return sorted((s, m, u) for (s, m), u in keys.items())

    def metric_series(self, service: str, metric: str) -> list[MetricRow]:
        return sorted(self._metric_index.get((service, metric), []), key=lambda r: r.time)

    def all_spans(self) -> list[TraceRow]:
        return list(self._spans)

    def children_of(self, span_id: str) -> list[TraceRow]:
        return list(self._children.get(span_id, []))

    def callee_of(self, row: TraceRow) -> str | None:
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

    def get_trace(self, trace_id: str) -> list[TraceRow]:
        spans = [r for r in self._spans if r.trace_id == trace_id]
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
        min_rank = _SEVERITY_RANK.get(severity_min.lower(), 0) if severity_min else 0
        needle = contains.lower() if contains else None
        out: list[LogRow] = []
        for r in self._fixture.logs:
            if service is not None and r.service != service:
                continue
            if _SEVERITY_RANK.get(r.severity.lower(), 0) < min_rank:
                continue
            if start is not None and r.time < start:
                continue
            if end is not None and r.time > end:
                continue
            if needle is not None and needle not in r.message.lower():
                continue
            out.append(r)
        out.sort(key=lambda r: (r.time, r.service))
        return out[:limit] if limit is not None else out

    def logs_for_trace(self, trace_id: str) -> list[LogRow]:
        return sorted(self._logs_by_trace.get(trace_id, []), key=lambda r: r.time)

    def list_changes(
        self,
        *,
        service: str | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> list[ChangeEvent]:
        out: list[ChangeEvent] = []
        for c in self._fixture.changes:
            if service is not None and c.service != service:
                continue
            if start is not None and c.time < start:
                continue
            if end is not None and c.time > end:
                continue
            out.append(c)
        out.sort(key=lambda c: c.time)
        return out
