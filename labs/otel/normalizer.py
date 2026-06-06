"""Normalize backend-specific telemetry into Sentinel public fixture schemas."""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Any

from labs.otel.redactor import BANNED_PUBLIC_TOKENS
from sentinel.fixtures.schemas import LogRow, MetricRow, TraceRow


def normalize_raw_capture(raw_dir: Path, public_dir: Path) -> None:
    """Convert raw captures to public JSON and JSONL fixture files."""

    raise NotImplementedError("raw capture normalization is not wired yet")


def normalize_prometheus_matrix(
    result: dict[str, Any],
    *,
    metric_name: str,
    unit: str,
    window_start_epoch_seconds: float,
    service_label: str = "service_name",
) -> list[MetricRow]:
    """Convert a Prometheus matrix result to normalized metric rows."""

    rows: list[MetricRow] = []
    for series in result.get("result", []):
        if not isinstance(series, dict):
            continue
        labels = series.get("metric", {})
        values = series.get("values", [])
        if not isinstance(labels, dict) or not isinstance(values, list):
            continue
        service = labels.get(service_label) or labels.get("service") or labels.get("job")
        if not service:
            continue
        attributes = _public_attributes(labels, exclude={service_label, "service", "job", "__name__"})
        for point in values:
            if not isinstance(point, list | tuple) or len(point) != 2:
                continue
            timestamp, raw_value = point
            value = float(raw_value)
            if not isfinite(value):
                continue
            rows.append(
                MetricRow(
                    time=_relative_second(timestamp, window_start_epoch_seconds),
                    service=str(service),
                    metric=metric_name,
                    value=value,
                    unit=unit,
                    attributes=attributes,
                )
            )
    return rows


def normalize_jaeger_traces(
    traces: list[dict[str, Any]],
    *,
    window_start_epoch_seconds: float,
) -> list[TraceRow]:
    """Convert Jaeger trace payloads to normalized span rows."""

    rows: list[TraceRow] = []
    for trace in traces:
        processes = trace.get("processes", {})
        spans = trace.get("spans", [])
        if not isinstance(processes, dict) or not isinstance(spans, list):
            continue
        for span in spans:
            if not isinstance(span, dict):
                continue
            tags = _tag_map(span.get("tags", []))
            process = processes.get(span.get("processID"), {})
            service = process.get("serviceName") if isinstance(process, dict) else None
            if not service:
                continue
            rows.append(
                TraceRow(
                    trace_id=str(span.get("traceID", "")),
                    span_id=str(span.get("spanID", "")),
                    parent_span_id=_parent_span_id(span),
                    time=_relative_second(
                        _microseconds_to_seconds(span.get("startTime")),
                        window_start_epoch_seconds,
                    ),
                    service=str(service),
                    operation=str(span.get("operationName") or "unknown"),
                    duration_ms=float(span.get("duration", 0)) / 1000,
                    status=_span_status(tags),
                    attributes=_public_attributes(tags),
                )
            )
    return [row for row in rows if row.trace_id and row.span_id]


def normalize_opensearch_logs(
    hits: list[dict[str, Any]],
    *,
    window_start_epoch_seconds: float,
) -> list[LogRow]:
    """Convert OpenSearch log hits to normalized log rows."""

    rows: list[LogRow] = []
    for hit in hits:
        source = hit.get("_source", {})
        if not isinstance(source, dict):
            continue
        message = source.get("body")
        service = _log_service(source)
        timestamp = source.get("time") or source.get("timestamp") or source.get("observedTimestamp")
        if not message or not service or not timestamp:
            continue
        if _contains_banned_public_token(message):
            continue
        rows.append(
            LogRow(
                time=_relative_second(_timestamp_seconds(timestamp), window_start_epoch_seconds),
                service=service,
                severity=_log_severity(source),
                message=str(message),
                attributes=_log_attributes(source),
                trace_id=_optional_string(source.get("traceId") or source.get("trace_id")),
            )
        )
    return rows


def _log_service(source: dict[str, Any]) -> str | None:
    resource = source.get("resource")
    if isinstance(resource, dict):
        service = resource.get("service.name") or resource.get("service_name")
        if service:
            return str(service)
    attributes = source.get("attributes")
    if isinstance(attributes, dict):
        service = attributes.get("service.name") or attributes.get("service_name")
        if service:
            return str(service)
    return None


def _log_attributes(source: dict[str, Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for key in ("attributes", "resource"):
        value = source.get(key)
        if isinstance(value, dict):
            for attr_key, attr_value in value.items():
                if _is_public_attribute(attr_key, attr_value):
                    attributes[str(attr_key)] = attr_value
    return attributes


def _tag_map(tags: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if not isinstance(tags, list):
        return values
    for tag in tags:
        if (
            isinstance(tag, dict)
            and "key" in tag
            and _is_public_attribute(str(tag["key"]), tag.get("value"))
        ):
            values[str(tag["key"])] = tag.get("value")
    return values


def _parent_span_id(span: dict[str, Any]) -> str | None:
    references = span.get("references", [])
    if not isinstance(references, list):
        return None
    for reference in references:
        if isinstance(reference, dict) and reference.get("refType") == "CHILD_OF":
            return _optional_string(reference.get("spanID"))
    return None


def _span_status(tags: dict[str, Any]) -> str:
    if tags.get("error") is True:
        return "ERROR"
    grpc_status = tags.get("rpc.grpc.status_code")
    if grpc_status is not None and str(grpc_status) != "0":
        return "ERROR"
    http_status = tags.get("http.response.status_code") or tags.get("http.status_code")
    if http_status is not None and int(http_status) >= 500:
        return "ERROR"
    status = tags.get("otel.status_code")
    if str(status).upper() == "ERROR":
        return "ERROR"
    return "OK"


def _log_severity(source: dict[str, Any]) -> str:
    severity_text = source.get("severityText")
    if severity_text:
        return str(severity_text)
    severity = source.get("severity")
    if isinstance(severity, dict):
        text = severity.get("text")
        if text:
            return str(text)
    if severity:
        return str(severity)
    return "INFO"


def _public_attributes(
    attributes: dict[str, Any],
    *,
    exclude: set[str] | None = None,
) -> dict[str, Any]:
    excluded = exclude or set()
    return {
        str(key): value
        for key, value in attributes.items()
        if str(key) not in excluded and _is_public_attribute(str(key), value)
    }


def _is_public_attribute(key: str, value: Any = None) -> bool:
    return not _contains_banned_public_token(key) and not _contains_banned_public_token(value)


def _contains_banned_public_token(value: Any) -> bool:
    if value is None:
        return False
    text = str(value)
    return any(token in text for token in BANNED_PUBLIC_TOKENS)


def _relative_second(timestamp_seconds: Any, window_start_epoch_seconds: float) -> int:
    return max(0, int(round(float(timestamp_seconds) - window_start_epoch_seconds)))


def _microseconds_to_seconds(value: Any) -> float:
    return float(value) / 1_000_000


def _timestamp_seconds(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    text = str(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    text = _truncate_fractional_seconds(text)
    return datetime.fromisoformat(text).timestamp()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _truncate_fractional_seconds(text: str) -> str:
    if "." not in text:
        return text
    prefix, suffix = text.split(".", 1)
    offset = ""
    fraction = suffix
    for marker in ("+", "-"):
        if marker in suffix:
            fraction, _, tail = suffix.partition(marker)
            offset = f"{marker}{tail}"
            break
    return f"{prefix}.{fraction[:6]}{offset}"
