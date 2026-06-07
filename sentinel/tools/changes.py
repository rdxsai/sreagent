"""Change tools. Root-cause correlation looks backward from the trace-based
onset, asymmetrically, because a change precedes the symptoms it causes.
"""

from __future__ import annotations

from sentinel.registry import tool
from sentinel.tools.models import (
    ChangesLookbackInput,
    ChangesOutput,
    ChangesSearchInput,
)
from sentinel.tools.store import TelemetryStore


@tool(namespace="changes")
def changes_search(params: ChangesSearchInput, store: TelemetryStore) -> ChangesOutput:
    """List recent change events, optionally filtered by service or time window.

    Changes are the candidate culprits (one real plus decoys). Use changes_lookback
    to rank them against a known onset; use this for a full or service-scoped view.
    """
    return ChangesOutput(
        changes=store.list_changes(service=params.service, start=params.start, end=params.end)
    )


@tool(namespace="changes")
def changes_lookback(params: ChangesLookbackInput, store: TelemetryStore) -> ChangesOutput:
    """Return changes strictly before the onset, nearest first.

    This is the asymmetric backward correlation: a cause precedes its symptoms,
    so only changes earlier than the trace-based onset are candidates, and the
    nearest one is the strongest. Optionally scope to the implicated service.
    """
    start = None if params.lookback_seconds is None else params.onset_second - params.lookback_seconds
    candidates = [
        c
        for c in store.list_changes(service=params.service)
        if c.time < params.onset_second and (start is None or c.time >= start)
    ]
    candidates.sort(key=lambda c: c.time, reverse=True)
    return ChangesOutput(changes=candidates)
