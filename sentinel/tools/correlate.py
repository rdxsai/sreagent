"""Correlation tools the manager runs over the investigators' evidence.

`correlate_attribute_fault` consumes structured fault observations (the typed
output an investigator gathers from traces_find) and decides node vs edge by the
span-kind rule. `correlate_timeline` orders changes against the onset.
"""

from __future__ import annotations

from sentinel.registry import tool
from sentinel.tools.models import (
    AttributeFaultInput,
    Attribution,
    TimelineEntry,
    TimelineInput,
    TimelineOutput,
)
from sentinel.tools.store import TelemetryStore


@tool(namespace="correlate")
def correlate_attribute_fault(params: AttributeFaultInput, store: TelemetryStore) -> Attribution:
    """Decide node-vs-edge from fault observations using the span-kind rule.

    Feed it per-candidate observations: server-span error counts for suspected
    services and client-span error counts (with callee) for suspected edges. If
    any callee's own server spans error, it is a service fault at that callee; if
    a caller's client spans error but the callee's server spans are clean, it is
    an edge fault. This consumes the structured findings the investigators return.
    """
    server_errors = {
        o.service: o.error_count for o in params.observations if o.role == "server" and o.error_count > 0
    }
    if server_errors:
        service = max(server_errors, key=lambda s: server_errors[s])
        return Attribution(
            kind="service",
            service=service,
            confidence=0.9,
            rationale=f"{service} server spans error ({server_errors[service]}): the service's own work failed",
        )
    client_errors = [
        o
        for o in params.observations
        if o.role == "client" and o.error_count > 0 and (o.callee or "") not in server_errors
    ]
    if client_errors:
        top = max(client_errors, key=lambda o: o.error_count)
        return Attribution(
            kind="edge",
            caller=top.service,
            callee=top.callee,
            confidence=0.8,
            rationale=(
                f"{top.service} client spans to {top.callee} error ({top.error_count}) "
                f"while {top.callee} server spans are clean: an edge fault"
            ),
        )
    return Attribution(
        kind="unknown",
        confidence=0.0,
        rationale="no erroring observations to attribute",
    )


@tool(namespace="correlate")
def correlate_timeline(params: TimelineInput, store: TelemetryStore) -> TimelineOutput:
    """Order change events against the onset and flag which precede it.

    Builds a single ordered timeline of changes plus the onset marker, and lists
    the change ids that occurred before the onset (the causal candidates).
    """
    entries = [
        TimelineEntry(second=c.time, kind="change", detail=f"{c.id} on {c.service}: {c.summary}")
        for c in params.changes
    ]
    entries.append(TimelineEntry(second=params.onset_second, kind="onset", detail="first error span"))
    entries.sort(key=lambda e: (e.second, 0 if e.kind == "change" else 1))
    before = sorted(
        (c for c in params.changes if c.time < params.onset_second), key=lambda c: c.time
    )
    return TimelineOutput(entries=entries, changes_before_onset=[c.id for c in before])
