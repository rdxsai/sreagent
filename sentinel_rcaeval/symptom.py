"""Synthesize a symptom string and one firing alert from public telemetry.

RCAEval provides no alerts, but the eval prompt (build_task_prompt) needs a
symptom and at least one DerivedAlert. Derive both from the largest post-onset
shift in a user-facing RED metric. This reads only observable metric rows, never
the ground-truth label, so it does not leak the answer.
"""

from __future__ import annotations

import hashlib
from statistics import fmean

from sentinel.fixtures.schemas import DerivedAlert, MetricRow
from sentinel_rcaeval.normalize import Window


def _mean_split(rows: list[MetricRow], onset: int) -> tuple[float, float]:
    pre = [r.value for r in rows if r.time < onset]
    post = [r.value for r in rows if r.time >= onset]
    return (fmean(pre) if pre else 0.0, fmean(post) if post else 0.0)


def _top_service(metrics: list[MetricRow], metric: str, onset: int) -> tuple[str, float, float] | None:
    best: tuple[str, float, float] | None = None
    services = {r.service for r in metrics if r.metric == metric}
    for service in services:
        rows = [r for r in metrics if r.service == service and r.metric == metric]
        pre, post = _mean_split(rows, onset)
        if post > pre and (best is None or post - pre > best[2] - best[1]):
            best = (service, pre, post)
    return best


def _fingerprint(alertname: str, labels: dict[str, str]) -> str:
    payload = alertname + "|" + ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def synthesize_symptom(metrics: list[MetricRow], window: Window) -> tuple[str, DerivedAlert]:
    onset = window.onset_second
    err = _top_service(metrics, "request_error_rate", onset)
    if err:
        service, _pre, post = err
        alertname, severity, expr = "HighErrorRate", "critical", "request_error_rate > 0.05"
        symptom = f"Elevated error rate on {service} beginning around second {onset}."
    else:
        lat = _top_service(metrics, "latency_p95_ms", onset)
        if lat:
            service, _pre, post = lat
            alertname, severity, expr = "HighLatency", "warning", "latency_p95_ms > 250"
            symptom = f"Elevated p95 latency on {service} beginning around second {onset}."
        else:
            movers = [m for m in (_top_service(metrics, mk, onset) for mk in {r.metric for r in metrics}) if m]
            if not movers:
                raise ValueError("no post-onset metric shift to synthesize a symptom from")
            service, _pre, post = max(movers, key=lambda m: m[2] - m[1])
            alertname, severity, expr = "MetricAnomaly", "warning", "metric shifted after onset"
            symptom = f"Anomalous metric shift on {service} beginning around second {onset}."
    labels = {"service": service}
    alert = DerivedAlert(
        alertname=alertname,
        severity=severity,
        starts_at_second=onset,
        labels=labels,
        annotations={"summary": symptom},
        value=post,
        expr=expr,
        fingerprint=_fingerprint(alertname, labels),
    )
    return symptom, alert
