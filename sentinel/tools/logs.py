"""Log tools. Logs are secondary and noisy here: error-severity lines are
dominated by load-generator timeouts and collector backpressure, not the root
cause. Prefer reaching logs through a failing trace with logs_for_trace; severity
is case-folded and messages are hard-truncated to stay token-cheap.
"""

from __future__ import annotations

from sentinel.fixtures.schemas import LogRow
from sentinel.registry import tool
from sentinel.tools.models import (
    LogLine,
    LogsForTraceInput,
    LogsOutput,
    LogsSearchInput,
)
from sentinel.tools.store import TelemetryStore

_MAX_MESSAGE_CHARS = 300


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
