"""The terminal output tool. The agent calls this once to submit its structured
RootCauseReport; the eval harness intercepts the call, validates the report, and
ends the run. Keeping the answer in a typed tool call avoids parsing free text.
"""

from __future__ import annotations

from statistics import fmean

from sentinel.registry import tool
from sentinel.tools.models import (
    BuildEvidenceInput,
    ChangeVerdict,
    EvidenceBundle,
    FindingAck,
    ReportAck,
    ReportCheck,
    RootCauseReport,
    ServiceFinding,
    TimelineEntry,
)
from sentinel.tools.store import TelemetryStore

REPORT_TOOL = "report_root_cause"
FINDING_TOOL = "report_finding"
CHANGE_VERDICT_TOOL = "report_change_verdict"

_SIGNAL_THRESHOLDS = {"request_error_rate": 0.02, "latency_p95_ms": 100.0, "cpu_cores": 1.0}
_SIGNAL_LABELS = {"request_error_rate": "error rate", "latency_p95_ms": "p95 latency", "cpu_cores": "cpu cores"}


@tool(namespace="report")
def report_root_cause(params: RootCauseReport, store: object) -> ReportAck:
    """Submit the final incident report and end the investigation.

    Call this exactly once, after you have traced the failure to its origin and
    correlated it to a change. Provide the root cause (kind=service with the
    faulting service, or kind=edge with caller and callee, plus the failure
    type), the culprit change id, the change ids you ruled out, and the evidence
    that supports your conclusion.
    """
    return ReportAck(accepted=True)


@tool(namespace="report")
def report_build_evidence(params: BuildEvidenceInput, store: TelemetryStore) -> EvidenceBundle:
    """Compile a ready-to-attach evidence package for the report.

    Pulls the implicated service's own server-error count and any meaningful
    error/latency/CPU rise after onset, orders the changes against onset into a
    timeline, and (if given) states the culprit change with its timing. Call it
    once you have your conclusion to assemble the evidence list and timeline the
    report fields expect, instead of hand-writing them.
    """
    svc, onset = params.suspected_service, params.onset_second
    evidence: list[str] = []
    server_err = store.find_spans(service=svc, span_kind="server", status="ERROR")
    if server_err:
        evidence.append(f"{svc} own server spans error ({len(server_err)}): a service-fault signal")
    for metric, threshold in _SIGNAL_THRESHOLDS.items():
        rows = store.metric_series(svc, metric)
        if not rows:
            continue
        pre = [r.value for r in rows if r.time < onset]
        post = [r.value for r in rows if r.time >= onset]
        pre_mean = fmean(pre) if pre else 0.0
        post_mean = fmean(post) if post else 0.0
        if post_mean - pre_mean > threshold:
            evidence.append(f"{svc} {_SIGNAL_LABELS[metric]} rose from {pre_mean:.2f} to {post_mean:.2f} after onset")
    changes = store.list_changes()
    timeline = [
        TimelineEntry(second=c.time, kind="change", detail=f"{c.id} on {c.service}: {c.summary}")
        for c in changes
    ]
    timeline.append(TimelineEntry(second=onset, kind="onset", detail="first error span"))
    timeline.sort(key=lambda e: (e.second, 0 if e.kind == "change" else 1))
    if params.culprit_change_id:
        match = [c for c in changes if c.id == params.culprit_change_id]
        if match:
            c = match[0]
            evidence.append(
                f"culprit {c.id} on {c.service} at second {c.time} precedes onset {onset}: {c.summary}"
            )
    return EvidenceBundle(evidence=evidence, timeline=timeline)


@tool(namespace="report")
def report_self_check(params: RootCauseReport, store: TelemetryStore) -> ReportCheck:
    """Validate a draft report for completeness and consistency before submitting.

    Checks the root cause is well-formed (a service for kind=service, caller and
    callee for kind=edge), the culprit change id exists and is not also in the
    ruled-out set, and that evidence is attached. Run it before report_root_cause
    and fix any issues it returns.
    """
    issues: list[str] = []
    rc = params.root_cause
    if rc.kind == "service" and not rc.service:
        issues.append("root_cause kind=service requires a service")
    if rc.kind == "edge" and not (rc.caller and rc.callee):
        issues.append("root_cause kind=edge requires both caller and callee")
    known = {c.id for c in store.list_changes()}
    if not params.culprit_change_id:
        issues.append("culprit_change_id is empty")
    elif params.culprit_change_id not in known:
        issues.append(f"culprit_change_id {params.culprit_change_id} is not a known change")
    if params.culprit_change_id and params.culprit_change_id in params.ruled_out_change_ids:
        issues.append("culprit_change_id also appears in ruled_out_change_ids")
    if not params.evidence:
        issues.append("evidence is empty; attach the supporting signals")
    return ReportCheck(ok=not issues, issues=issues)


@tool(namespace="report")
def report_finding(params: ServiceFinding, store: object) -> FindingAck:
    """Submit your ServiceFinding for the service you investigated and end your work.

    Call this exactly once. Set is_origin true only if THIS service's own work failed,
    its own server spans error, or its own latency/CPU shifted after onset, not if it
    merely calls a failing dependency (that is a victim, not the origin). Include the
    suspect change on this service, your confidence, and the evidence you found.
    """
    return FindingAck(accepted=True)


@tool(namespace="report")
def report_change_verdict(params: ChangeVerdict, store: object) -> FindingAck:
    """Submit your verdict on whether the assessed change is the culprit, and end your work.

    Call this exactly once. Set is_culprit true only if the change's diff_touches
    plausibly cause the observed failure and its timing precedes onset; give your
    confidence and the reason.
    """
    return FindingAck(accepted=True)
