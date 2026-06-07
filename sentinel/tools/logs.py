"""Log tools. Logs are secondary and noisy here: error-severity lines are
dominated by load-generator timeouts and collector backpressure, not the root
cause. Prefer reaching logs through a failing trace with logs_for_trace; severity
is case-folded and messages are hard-truncated to stay token-cheap.
"""

from __future__ import annotations

import re
from collections import defaultdict

from sentinel.fixtures.schemas import LogRow
from sentinel.registry import tool
from sentinel.tools.models import (
    ErrorClustersInput,
    ErrorClustersOutput,
    FirstErrorEntry,
    FirstErrorInput,
    FirstErrorOutput,
    LevelBucket,
    LevelHistogramInput,
    LevelHistogramOutput,
    LogCluster,
    LogLine,
    LogsForTraceInput,
    LogsOutput,
    LogsSearchInput,
)
from sentinel.tools.store import TelemetryStore

_MAX_MESSAGE_CHARS = 300


def _template(message: str) -> str:
    """Normalize a log message to a template: digits and long hex ids become '#'."""
    text = re.sub(r"\b[0-9a-f]{8,}\b", "#", message.strip())
    text = re.sub(r"\d+", "#", text)
    return text[:120]


def _line(row: LogRow) -> LogLine:
    message = row.message.strip().replace("\n", " ")
    if len(message) > _MAX_MESSAGE_CHARS:
        message = message[:_MAX_MESSAGE_CHARS] + "..."
    return LogLine(
        time=row.time,
        service=row.service,
        severity=row.severity.lower(),
        message=message,
        trace_id=row.trace_id,
    )


def _bundle(matched: list[LogRow], limit: int, steer: str) -> LogsOutput:
    total = len(matched)
    shown = matched[:limit]
    truncated = total > limit
    note = (
        f"showing the first {len(shown)} of {total} lines; {steer}" if truncated else None
    )
    return LogsOutput(
        logs=[_line(r) for r in shown],
        total_matched=total,
        returned=len(shown),
        truncated=truncated,
        note=note,
    )


@tool(namespace="logs")
def logs_search(params: LogsSearchInput, store: TelemetryStore) -> LogsOutput:
    """Search logs by service, minimum severity, message substring, or time window.

    Noisy by design: a bare severity_min='error' is dominated by load-generator
    timeouts and collector backpressure. Scope with a service and time window, or
    prefer logs_for_trace to read the lines tied to a specific failing trace.
    """
    matched = store.search_logs(
        service=params.service,
        severity_min=params.severity_min,
        contains=params.contains,
        start=params.start,
        end=params.end,
    )
    return _bundle(matched, params.limit, "add a service, time window, or message filter to narrow.")


@tool(namespace="logs")
def logs_for_trace(params: LogsForTraceInput, store: TelemetryStore) -> LogsOutput:
    """Return the log lines attached to one trace, the high-signal way to read logs.

    Take a trace_id from a failing span (traces_find / traces_error_origin) and
    read only that request's logs. Severity is case-folded; messages are
    truncated.
    """
    matched = store.logs_for_trace(params.trace_id)
    if params.severity_min:
        from sentinel.tools.store import _SEVERITY_RANK

        floor = _SEVERITY_RANK.get(params.severity_min.lower(), 0)
        matched = [r for r in matched if _SEVERITY_RANK.get(r.severity.lower(), 0) >= floor]
    return _bundle(matched, params.limit, "raise severity_min or lower the limit.")


@tool(namespace="logs")
def logs_error_clusters(params: ErrorClustersInput, store: TelemetryStore) -> ErrorClustersOutput:
    """Cluster error logs by message template to surface the dominant failure modes.

    Normalizes numbers and ids out of each error message and groups by the result,
    returning templates by frequency with an example and the services they hit. Cuts
    through the noise (load-generator timeouts, collector backpressure) to the few
    error shapes that matter. Scope with a service to focus.
    """
    rows = store.search_logs(service=params.service, severity_min="error", start=params.start)
    groups: dict[str, dict] = defaultdict(lambda: {"count": 0, "example": "", "services": set()})
    for row in rows:
        group = groups[_template(row.message)]
        group["count"] += 1
        group["services"].add(row.service)
        if not group["example"]:
            group["example"] = row.message.strip()[:_MAX_MESSAGE_CHARS]
    clusters = [
        LogCluster(template=t, count=g["count"], example=g["example"], services=sorted(g["services"]))
        for t, g in groups.items()
    ]
    clusters.sort(key=lambda c: c.count, reverse=True)
    return ErrorClustersOutput(clusters=clusters[: params.limit])


@tool(namespace="logs")
def logs_level_histogram(params: LevelHistogramInput, store: TelemetryStore) -> LevelHistogramOutput:
    """Count logs per severity level in time buckets to find when errors surged.

    Returns per-bucket counts by level over the window, so a jump in error/warn
    volume pins the onset from the log side (a cross-check on the trace-based onset).
    Scope with a service to isolate one service's surge.
    """
    rows = store.search_logs(service=params.service)
    buckets: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        bucket = (row.time // params.bucket_seconds) * params.bucket_seconds
        buckets[bucket][row.severity.lower()] += 1
    return LevelHistogramOutput(
        buckets=[LevelBucket(start=b, counts=dict(c)) for b, c in sorted(buckets.items())]
    )


@tool(namespace="logs")
def logs_first_error(params: FirstErrorInput, store: TelemetryStore) -> FirstErrorOutput:
    """Report each service's earliest error-level log, earliest first.

    A fast onset cross-check: which service started logging errors first, and when.
    Compare the earliest entry against the trace-based onset; a service erroring
    before the rest is a candidate origin (mind that load-generator/collector noise
    can error throughout).
    """
    rows = store.search_logs(severity_min=params.severity_min)
    first: dict[str, LogRow] = {}
    for row in sorted(rows, key=lambda r: r.time):
        if row.service not in first:
            first[row.service] = row
    entries = [
        FirstErrorEntry(service=s, time=r.time, severity=r.severity.lower(), message=r.message.strip()[:200])
        for s, r in first.items()
    ]
    entries.sort(key=lambda e: e.time)
    return FirstErrorOutput(first=entries)
