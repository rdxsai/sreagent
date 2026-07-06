"""Map NerdGraph NRQL result rows into the fixture telemetry models.

Live rows carry epoch-millisecond timestamps; the fixture contract is
window-relative integer seconds, so every mapper takes window_start_ms.
Rows missing required fields map to None: live data is allowed to be ragged
and the store simply drops what cannot be typed.
"""

from __future__ import annotations

from pydantic import ValidationError

from sentinel.fixtures.schemas import ChangeEvent, LogRow, MetricRow, TraceRow

_SPAN_ATTR_KEYS = ("span.kind", "rpc.method", "rpc.service")


def rel_seconds(ts_ms: int | float, window_start_ms: int) -> int:
    return max(0, int((int(ts_ms) - window_start_ms) // 1000))


def _first_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for inner in value.values():
            number = _first_number(inner)
            if number is not None:
                return number
    return None


def to_trace_row(d: dict, window_start_ms: int) -> TraceRow | None:
    attributes = {k: d[k] for k in _SPAN_ATTR_KEYS if d.get(k) is not None}
    status_code = d.get("otel.status_code")
    try:
        return TraceRow(
            trace_id=d.get("trace.id") or "",
            span_id=d.get("id") or "",
            parent_span_id=d.get("parent.id") or None,
            time=rel_seconds(d.get("timestamp", window_start_ms), window_start_ms),
            service=d.get("service.name") or "",
            operation=d.get("name") or "",
            duration_ms=float(d.get("duration.ms") or 0.0),
            status="ERROR" if status_code == "ERROR" else "OK",
            attributes=attributes,
        )
    except (ValidationError, TypeError, ValueError):
        return None


def to_log_row(d: dict, window_start_ms: int) -> LogRow | None:
    try:
        return LogRow(
            time=rel_seconds(d.get("timestamp", window_start_ms), window_start_ms),
            service=d.get("service.name") or "",
            severity=d.get("severity.text") or d.get("level") or "INFO",
            message=d.get("message") or "",
            trace_id=d.get("trace.id") or None,
        )
    except (ValidationError, TypeError, ValueError):
        return None


def to_change_event(d: dict, window_start_ms: int) -> ChangeEvent | None:
    touches = d.get("diffTouches") or ""
    try:
        return ChangeEvent(
            id=d.get("changeId") or "",
            time=rel_seconds(d.get("timestamp", window_start_ms), window_start_ms),
            service=d.get("service") or "",
            kind=d.get("kind") or "",
            summary=d.get("summary") or "",
            diff_touches=[t for t in str(touches).split(",") if t],
        )
    except (ValidationError, TypeError, ValueError):
        return None


def to_metric_rows(
    results: list[dict],
    service: str,
    metric: str,
    unit: str,
    window_start_ms: int,
) -> list[MetricRow]:
    rows: list[MetricRow] = []
    for entry in results:
        begin = entry.get("beginTimeSeconds")
        if begin is None:
            continue
        value = None
        for key, raw in entry.items():
            if key in ("beginTimeSeconds", "endTimeSeconds"):
                continue
            value = _first_number(raw)
            if value is not None:
                break
        if value is None:
            continue
        rows.append(
            MetricRow(
                time=rel_seconds(int(begin) * 1000, window_start_ms),
                service=service,
                metric=metric,
                value=value,
                unit=unit,
            )
        )
    return rows
