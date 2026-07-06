"""Hypothesis tools: weigh and eliminate root-cause candidates.

These turn a candidate (a service or edge hypothesis, or a suspect change) into
evidence the agent can act on, by querying traces/metrics/changes the way an
investigator would. They consume the typed Hypothesis other steps produce and
feed the report's culprit and ruled-out sets.
"""

from __future__ import annotations

from statistics import fmean

from sentinel.registry import tool
from sentinel.tools.metrics import resource_metrics, significant_shift
from sentinel.tools.models import (
    EvidenceReport,
    GatherEvidenceInput,
    RuleOutInput,
    RuleOutVerdict,
)
from sentinel.tools.store import TelemetryStore

_ERROR = "ERROR"
# Absolute shift thresholds, sized to fault magnitudes rather than low-baseline
# noise: a real fault moves p95 by >100ms, CPU by >1 core, or error rate by >0.02.
# A 10%-relative test would trip on ~0.05-core CPU jitter and never eliminate a
# clean service.
_LATENCY_DELTA_MIN = 100.0
_ERROR_RATE_DELTA_MIN = 0.02


def _delta(store: TelemetryStore, service: str, metric: str, onset: int) -> float:
    rows = store.metric_series(service, metric)
    if not rows:
        return 0.0
    pre = [r.value for r in rows if r.time < onset]
    post = [r.value for r in rows if r.time >= onset]
    return (fmean(post) if post else 0.0) - (fmean(pre) if pre else 0.0)


def _resource_shifts(store: TelemetryStore, service: str, onset: int) -> list[str]:
    """Signal lines for every advertised resource metric that rose after onset."""
    shifts: list[str] = []
    for metric, unit in resource_metrics(store):
        rows = store.metric_series(service, metric)
        if not rows:
            continue
        pre = [r.value for r in rows if r.time < onset]
        post = [r.value for r in rows if r.time >= onset]
        pre_mean = fmean(pre) if pre else 0.0
        post_mean = fmean(post) if post else 0.0
        if significant_shift(metric, pre_mean, post_mean):
            shifts.append(f"{service} {metric} rose from {pre_mean:.2f} to {post_mean:.2f} {unit} after onset")
    return shifts


def _own_fault(store: TelemetryStore, service: str, onset: int) -> bool:
    if store.find_spans(service=service, span_kind="server", status=_ERROR):
        return True
    return (
        _delta(store, service, "latency_p95_ms", onset) > _LATENCY_DELTA_MIN
        or bool(_resource_shifts(store, service, onset))
    )


def _nearest_change_before(store: TelemetryStore, service: str | None, onset: int) -> str | None:
    candidates = [c for c in store.list_changes(service=service) if c.time < onset]
    candidates.sort(key=lambda c: c.time, reverse=True)
    return candidates[0].id if candidates else None


@tool(namespace="hypothesis")
def hypothesis_gather_evidence(params: GatherEvidenceInput, store: TelemetryStore) -> EvidenceReport:
    """Gather supporting and refuting evidence for one root-cause hypothesis.

    For a service hypothesis it checks the service's own server error spans and
    whether its error rate, latency, or any advertised resource metric (memory,
    cpu) shifted after onset. For an edge hypothesis it
    checks the caller's client errors to the callee and that the callee's own
    server spans stay clean (the edge signature). It also names the nearest change
    on the implicated service before onset as a suspect to confirm with
    changes_rank_culprit. Pass a Hypothesis from your candidate set.
    """
    h = params.hypothesis
    signals: list[str] = []
    if h.kind == "service" and h.service:
        server_err = store.find_spans(service=h.service, span_kind="server", status=_ERROR)
        n = len(server_err)
        signals.append(f"{h.service} own server error spans: {n}")
        err_d = _delta(store, h.service, "request_error_rate", h.onset_second)
        lat_d = _delta(store, h.service, "latency_p95_ms", h.onset_second)
        resource_signals = _resource_shifts(store, h.service, h.onset_second)
        if err_d > _ERROR_RATE_DELTA_MIN:
            signals.append(f"{h.service} error rate rose by {err_d:.2f} after onset")
        if lat_d > _LATENCY_DELTA_MIN:
            signals.append(f"{h.service} p95 latency rose by {lat_d:.0f}ms after onset")
        signals.extend(resource_signals)
        suspect = _nearest_change_before(store, h.service, h.onset_second)
        meaningful = err_d > _ERROR_RATE_DELTA_MIN or lat_d > _LATENCY_DELTA_MIN or bool(resource_signals)
        supported = n > 0 or meaningful
        confidence = 0.9 if n > 0 else (0.6 if meaningful else 0.1)
    elif h.kind == "edge" and h.caller and h.callee:
        client_err = store.find_spans(
            service=h.caller, span_kind="client", status=_ERROR, rpc_callee=h.callee
        )
        callee_server_err = store.find_spans(service=h.callee, span_kind="server", status=_ERROR)
        nc, ns = len(client_err), len(callee_server_err)
        signals.append(f"{h.caller}->{h.callee} client error spans: {nc}")
        signals.append(f"{h.callee} own server error spans: {ns}")
        suspect = _nearest_change_before(store, h.caller, h.onset_second)
        supported = nc > 0 and ns == 0
        confidence = 0.8 if supported else (0.3 if nc > 0 else 0.0)
    else:
        return EvidenceReport(
            hypothesis_id=h.id, supported=False, confidence=0.0,
            signals=["malformed hypothesis: need service, or caller+callee"], suspect_change_id=None,
        )
    if suspect:
        signals.append(f"nearest change before onset on the implicated service: {suspect}")
    return EvidenceReport(
        hypothesis_id=h.id, supported=supported, confidence=confidence,
        signals=signals, suspect_change_id=suspect,
    )


@tool(namespace="hypothesis")
def hypothesis_rule_out(params: RuleOutInput, store: TelemetryStore) -> RuleOutVerdict:
    """Eliminate an uninvolved service, or a change that postdates onset.

    Best for services: one with no own server errors and no latency/CPU rise after
    onset is ruled out as the origin (its errors, if any, are propagated from
    downstream). For changes it only checks timing: a change at or after onset is
    ruled out. It does NOT disambiguate same-service changes that both precede onset
    (e.g. a real culprit vs a near-miss decoy on the same service); for that, read
    their content with changes_search and compare diff_touches to the failure. So
    use it once per candidate service, not once per change.
    """
    if params.change_id is not None:
        match = [c for c in store.list_changes() if c.id == params.change_id]
        if not match:
            return RuleOutVerdict(ruled_out=False, reason=f"no change with id {params.change_id}")
        change = match[0]
        if change.time >= params.onset_second:
            return RuleOutVerdict(
                ruled_out=True,
                reason=f"change {change.id} at second {change.time} is at/after onset {params.onset_second}; a cause must precede its symptom",
            )
        return RuleOutVerdict(
            ruled_out=False,
            reason=(
                f"change {change.id} at second {change.time} precedes onset {params.onset_second}; "
                "still a candidate. To choose among same-service candidates, compare their "
                "diff_touches against the failure via changes_search, not this tool."
            ),
        )
    if params.service is not None:
        if _own_fault(store, params.service, params.onset_second):
            return RuleOutVerdict(
                ruled_out=False,
                reason=f"{params.service} shows an own-fault signal (server errors or a meaningful latency/CPU rise); cannot be eliminated",
            )
        return RuleOutVerdict(
            ruled_out=True,
            reason=f"{params.service} has no own server errors and no meaningful latency/CPU rise after onset; not the originating service",
        )
    return RuleOutVerdict(ruled_out=False, reason="provide a service or a change_id to test")
