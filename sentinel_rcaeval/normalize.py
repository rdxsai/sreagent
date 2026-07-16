"""Window, re-base, and normalize RCAEval telemetry into Sentinel row schemas."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

import structlog

from sentinel.fixtures.schemas import LogRow, MetricRow, TraceRow

log = structlog.get_logger("sentinel_rcaeval")


@dataclass(frozen=True)
class Window:
    inject_time: int
    start_abs: int
    end_abs: int

    @property
    def span_seconds(self) -> int:
        return self.end_abs - self.start_abs

    @property
    def onset_second(self) -> int:
        return self.inject_time - self.start_abs


def make_window(inject_time: int, pre: int = 180, post: int = 300) -> Window:
    return Window(inject_time=inject_time, start_abs=inject_time - pre, end_abs=inject_time + post)


def rebase(t_abs: int, window: Window) -> int:
    return t_abs - window.start_abs


def in_window(t_abs: int, window: Window) -> bool:
    return window.start_abs <= t_abs <= window.end_abs


# Match a known metric suffix at the end of an RCAEval column; the prefix is the
# service. Ordered longest/most-specific first. Anything unmatched falls back to
# a last-underscore split with a neutral "count" unit.
_SUFFIX_MAP: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"_(error[_-]?rate|errors?|error[_-]?ratio)$", re.IGNORECASE), "request_error_rate", "ratio"),
    (re.compile(r"_(latency|duration|lat|resp(?:onse)?[_-]?time)(?:_p?\d+)?$", re.IGNORECASE), "latency_p95_ms", "ms"),
    (re.compile(r"_(cpu)(?:_\w+)?$", re.IGNORECASE), "cpu_utilization", "ratio"),
    (re.compile(r"_(mem|memory)(?:_\w+)?$", re.IGNORECASE), "memory_mb", "MB"),
]


def canonical_column(column: str) -> tuple[str, str, str]:
    for pat, metric, unit in _SUFFIX_MAP:
        m = pat.search(column)
        if m and column[: m.start()]:
            return column[: m.start()], metric, unit
    if "_" in column:
        service, metric = column.rsplit("_", 1)
        return service, metric, "count"
    return column, "value", "count"


@dataclass(frozen=True)
class MetricFrame:
    times: list[int]
    columns: dict[str, list[float]]


def load_metric_frame(path: Path) -> MetricFrame:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        times = [int(float(t)) for t in data["time"]]
        columns = {k: [float(x) for x in v] for k, v in data.items() if k != "time"}
        return MetricFrame(times=times, columns=columns)
    if isinstance(data, list):
        times = [int(float(r["time"])) for r in data]
        keys = [k for k in data[0] if k != "time"] if data else []
        columns = {k: [float(r[k]) for r in data] for k in keys}
        return MetricFrame(times=times, columns=columns)
    raise ValueError(f"unrecognized metrics.json shape in {path}")


def melt_metrics(frame: MetricFrame, window: Window) -> list[MetricRow]:
    rows: list[MetricRow] = []
    for column, values in frame.columns.items():
        service, metric, unit = canonical_column(column)
        for t_abs, value in zip(frame.times, values):
            if in_window(t_abs, window):
                rows.append(
                    MetricRow(time=rebase(t_abs, window), service=service, metric=metric, value=value, unit=unit)
                )
    return rows


def _pick(row: dict[str, str], *aliases: str) -> str:
    for a in aliases:
        if a in row and row[a] not in (None, ""):
            return row[a]
    return ""


def _downsample(rows: list, cap: int, priority) -> list:
    if len(rows) <= cap:
        return rows
    keep = [r for r in rows if priority(r)]
    if len(keep) >= cap:
        return keep[:cap]
    rest = [r for r in rows if not priority(r)]
    step = len(rest) / (cap - len(keep))
    sampled = [rest[min(len(rest) - 1, int(i * step))] for i in range(cap - len(keep))]
    kept_ids = {id(r) for r in keep} | {id(r) for r in sampled}
    return [r for r in rows if id(r) in kept_ids]


def map_logs(path: Path, window: Window, cap: int = 20000) -> list[LogRow]:
    out: list[LogRow] = []
    skipped = 0
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw_time = _pick(row, "timestamp", "time")
            if not raw_time:
                skipped += 1
                continue
            t_abs = int(float(raw_time))
            if not in_window(t_abs, window):
                continue
            out.append(
                LogRow(
                    time=rebase(t_abs, window),
                    service=_pick(row, "service", "pod", "container") or "unknown",
                    severity=_pick(row, "level", "severity") or "info",
                    message=_pick(row, "message", "log", "body") or "(no message)",
                    trace_id=_pick(row, "trace_id", "traceId") or None,
                )
            )
    if skipped:
        log.info("rcaeval.map_logs.skipped_rows", path=str(path), count=skipped)
    return _downsample(out, cap, lambda r: r.severity.upper() in {"ERROR", "ERR", "CRITICAL", "FATAL", "WARN", "WARNING"})


def map_traces(path: Path, window: Window, cap: int = 20000) -> list[TraceRow]:
    out: list[TraceRow] = []
    skipped = 0
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw_time = _pick(row, "start_time", "timestamp", "startTime")
            trace_id = _pick(row, "trace_id", "traceId")
            span_id = _pick(row, "span_id", "spanId")
            if not raw_time or not trace_id or not span_id:
                skipped += 1
                continue
            t_abs = int(float(raw_time))
            if not in_window(t_abs, window):
                continue
            out.append(
                TraceRow(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=_pick(row, "parent_id", "parentSpanId", "parent_span_id") or None,
                    time=rebase(t_abs, window),
                    service=_pick(row, "service", "serviceName") or "unknown",
                    operation=_pick(row, "operation", "operationName", "name") or "unknown",
                    duration_ms=float(_pick(row, "duration_ms", "duration", "durationMs") or 0.0),
                    status=_pick(row, "status", "status_code", "statusCode") or "UNSET",
                )
            )
    if skipped:
        log.info("rcaeval.map_traces.skipped_rows", path=str(path), count=skipped)
    return _downsample(out, cap, lambda r: r.status.upper() not in {"OK", "UNSET", "0", "200"})
