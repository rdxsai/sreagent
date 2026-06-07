"""Correlation tools the manager runs over the investigators' evidence.

`correlate_attribute_fault` consumes structured fault observations (the typed
output an investigator gathers from traces_find) and decides node vs edge by the
span-kind rule. `correlate_timeline` orders changes against the onset.
"""

from __future__ import annotations

from statistics import fmean

from sentinel.registry import tool
from sentinel.tools.models import (
    AttributeFaultInput,
    Attribution,
    MetricToTracesInput,
    MetricToTracesOutput,
    NoArgs,
    OnsetConsensus,
    SignalAlignment,
    SignalShift,
    SignalsInput,
    TimelineEntry,
    TimelineInput,
    TimelineOutput,
)
from sentinel.tools.store import TelemetryStore
from sentinel.tools.traces import _summary

# Absolute shift thresholds, sized to fault magnitudes not low-baseline noise.
_SHIFT_THRESHOLDS = {"request_error_rate": 0.02, "latency_p95_ms": 100.0, "cpu_cores": 1.0}


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


@tool(namespace="correlate")
def correlate_signals(params: SignalsInput, store: TelemetryStore) -> SignalAlignment:
    """Align one service's error, latency, and CPU signals before vs after onset.

    Computes each metric's mean pre-onset and post-onset and flags the ones that
    moved. Use it to confirm in one call what a service's fault looks like in the
    metrics: errors for a failing dependency, latency for a slow one, CPU cores
    for a saturated one (e.g. a service whose CPU jumps but latency stays flat).
    """
    signals: list[SignalShift] = []
    for metric, threshold in _SHIFT_THRESHOLDS.items():
        rows = store.metric_series(params.service, metric)
        if not rows:
            continue
        pre = [r.value for r in rows if r.time < params.onset_second]
        post = [r.value for r in rows if r.time >= params.onset_second]
        pre_mean = fmean(pre) if pre else 0.0
        post_mean = fmean(post) if post else 0.0
        shifted = abs(post_mean - pre_mean) > threshold
        signals.append(SignalShift(metric=metric, pre_mean=pre_mean, post_mean=post_mean, shifted=shifted))
    return SignalAlignment(
        service=params.service,
        onset_second=params.onset_second,
        signals=signals,
        shifted_metrics=[s.metric for s in signals if s.shifted],
    )


@tool(namespace="correlate")
def correlate_metric_to_traces(params: MetricToTracesInput, store: TelemetryStore) -> MetricToTracesOutput:
    """Drill from a service's post-onset metric anomaly down to exemplar traces.

    Returns the trace ids of representative spans for the service at/after onset
    (erroring by default, or status=OK to contrast a healthy one), so you can open
    one with traces_get_trace and see the failing call path behind the metric.
    """
    spans = store.find_spans(service=params.service, status=params.status, start=params.onset_second)
    seen: list[str] = []
    sample = []
    for span in spans:
        if span.trace_id not in seen:
            seen.append(span.trace_id)
            sample.append(_summary(span, store))
        if len(seen) >= params.limit:
            break
    note = None if seen else f"no {params.status} spans for {params.service} at/after second {params.onset_second}"
    return MetricToTracesOutput(
        service=params.service,
        exemplar_trace_ids=seen,
        sample=sample,
        note=note,
    )


@tool(namespace="correlate")
def correlate_onset_consensus(params: NoArgs, store: TelemetryStore) -> OnsetConsensus:
    """Corroborate the incident onset across traces and logs.

    Compares the first error span time with the earliest error-level log time. When
    they agree (within a minute) the onset is solid; when they disagree the logs are
    likely dominated by background noise (load-generator timeouts that error from
    the start), so the trace onset is preferred. Anchor change correlation on the
    consensus onset this returns.
    """
    errors = [s for s in store.all_spans() if s.status.upper() == "ERROR"]
    trace_onset = min((s.time for s in errors), default=None)
    error_logs = store.search_logs(severity_min="error")
    log_onset = min((r.time for r in error_logs), default=None)
    agreement = trace_onset is not None and log_onset is not None and abs(trace_onset - log_onset) <= 60
    if agreement:
        consensus = min(trace_onset, log_onset)  # type: ignore[type-var]
        note = None
    elif trace_onset is not None:
        consensus = trace_onset
        note = "trace and log onsets differ by more than a minute; logs are likely noisy, using the trace onset"
    else:
        consensus = log_onset
        note = "no error spans; falling back to the log onset"
    return OnsetConsensus(
        trace_onset_second=trace_onset,
        first_error_log_second=log_onset,
        consensus_onset_second=consensus,
        agreement=agreement,
        note=note,
    )
