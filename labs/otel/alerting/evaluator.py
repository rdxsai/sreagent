"""Deterministic alert evaluator: queries Prometheus over a recorded window, returns the firing set."""

from __future__ import annotations

from hashlib import sha256
from math import isfinite
from typing import Any, Protocol

from sentinel.fixtures.schemas import DerivedAlert
from labs.otel.alerting.schema import AlertRule


class NoAlertFired(ValueError):
    """No rule fired over the window (a coverage hole)."""


class PrometheusRangeClient(Protocol):
    def query_range(self, query: str, *, start: float, end: float, step: str) -> dict[str, Any]: ...


_STEP_SECONDS = 15
_STEP = f"{_STEP_SECONDS}s"
_SEVERITY_RANK = {"critical": 0, "warning": 1}


def derive_alerts(
    *,
    window_start_epoch_seconds: float,
    window_end_epoch_seconds: float,
    prometheus: PrometheusRangeClient,
    rules: list[AlertRule],
) -> list[DerivedAlert]:
    firings: list[DerivedAlert] = []
    for rule in rules:
        result = prometheus.query_range(
            rule.expr,
            start=window_start_epoch_seconds,
            end=window_end_epoch_seconds,
            step=_STEP,
        )
        firing = _first_continuous_breach(
            result,
            rule=rule,
            window_start_epoch_seconds=window_start_epoch_seconds,
        )
        if firing is not None:
            firings.append(firing)
    if not firings:
        raise NoAlertFired("no symptom rule fired over the recorded window")
    ordered = sorted(
        firings,
        key=lambda a: (_SEVERITY_RANK[a.severity], a.starts_at_second, a.alertname),
    )
    # Collapse rules that resolve to the same alert (same fingerprint = same
    # alertname + labels). The unified UserFacingDegradation trigger is built from
    # an error rule and a latency rule sharing a fingerprint; if both fire we keep
    # one alert, the highest-severity / earliest by the sort above.
    deduped: list[DerivedAlert] = []
    seen: set[str] = set()
    for alert in ordered:
        if alert.fingerprint in seen:
            continue
        seen.add(alert.fingerprint)
        deduped.append(alert)
    return deduped


def _first_continuous_breach(
    result: dict[str, Any],
    *,
    rule: AlertRule,
    window_start_epoch_seconds: float,
) -> DerivedAlert | None:
    series_list = result.get("result", [])
    if not series_list:
        return None

    first_series = series_list[0]
    raw_values = first_series.get("values", [])
    points = sorted(raw_values, key=lambda p: float(p[0]))

    run_start_ts: float | None = None
    run_start_value: float | None = None

    for point in points:
        ts = float(point[0])
        value_string = point[1]

        try:
            value = float(value_string)
        except (ValueError, TypeError):
            run_start_ts = None
            continue

        if not isfinite(value):
            run_start_ts = None
            continue

        breached = _compare(value, rule.comparison, rule.threshold)

        if breached:
            if run_start_ts is None:
                run_start_ts = ts
                run_start_value = value
            if (ts - run_start_ts) >= rule.for_seconds:
                starts_at_second = max(0, round(run_start_ts - window_start_epoch_seconds))
                labels = dict(rule.labels)
                annotations = _render(
                    rule.annotation_templates,
                    value=run_start_value,
                    starts_at_second=starts_at_second,
                )
                return DerivedAlert(
                    alertname=rule.alertname,
                    severity=rule.severity,
                    starts_at_second=starts_at_second,
                    value=run_start_value,
                    labels=labels,
                    annotations=annotations,
                    expr=rule.expr,
                    fingerprint=_fingerprint(rule.alertname, labels),
                )
        else:
            run_start_ts = None

    return None


def _compare(value: float, comparison: str, threshold: float) -> bool:
    if comparison == ">":
        return value > threshold
    if comparison == ">=":
        return value >= threshold
    if comparison == "<":
        return value < threshold
    if comparison == "<=":
        return value <= threshold
    raise ValueError(f"unsupported comparison operator: {comparison!r}")


def _render(
    templates: dict[str, str],
    *,
    value: float,
    starts_at_second: int,
) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for key, tmpl in templates.items():
        rendered[key] = tmpl.replace("{{value}}", str(value)).replace("{{starts_at}}", str(starts_at_second))
    return rendered


def _fingerprint(alertname: str, labels: dict[str, str]) -> str:
    canonical = alertname + "|" + ",".join(f"{k}={labels[k]}" for k in sorted(labels))
    return sha256(canonical.encode("utf-8")).hexdigest()[:16]
