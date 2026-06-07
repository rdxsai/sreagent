"""Map an Alertmanager webhook payload to a list of DerivedAlert instances."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sentinel.fixtures.schemas import DerivedAlert
from labs.otel.alerting.schema import load_rules
from labs.otel.alerting.allowlist import assert_alert_is_symptom_level
from labs.otel.alerting.evaluator import _fingerprint


def alertmanager_payload_to_alerts(payload: dict[str, Any]) -> list[DerivedAlert]:
    """Map firing Alertmanager alerts to DerivedAlert instances.

    Resolved alerts are skipped. Every mapped alert is checked against the
    symptom-level allow-list; AllowlistError propagates to the caller.
    """
    firing = [a for a in payload.get("alerts", []) if a.get("status", "firing") == "firing"]
    if not firing:
        return []
    starts = [_parse_rfc3339(a["startsAt"]) for a in firing]
    earliest = min(starts)
    expr_by_name = {r.alertname: r.expr for r in load_rules()}
    alerts: list[DerivedAlert] = []
    for raw, start in zip(firing, starts):
        labels_in = raw.get("labels", {})
        alertname = labels_in.get("alertname", "")
        severity = labels_in.get("severity", "")
        symptom_labels = {k: v for k, v in labels_in.items() if k in ("tier", "signal")}
        annotations = dict(raw.get("annotations", {}))
        value = _coerce_float(annotations.get("value"))
        alert = DerivedAlert(
            alertname=alertname,
            severity=severity,
            starts_at_second=max(0, round((start - earliest).total_seconds())),
            labels=symptom_labels,
            annotations=annotations,
            value=value,
            expr=expr_by_name.get(alertname, f"live:{alertname}"),
            fingerprint=_fingerprint(alertname, symptom_labels),
        )
        assert_alert_is_symptom_level(alert)  # raises AllowlistError on a non-symptom alert
        alerts.append(alert)
    return alerts


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_rfc3339(text: str) -> datetime:
    """Parse RFC 3339 timestamps robustly on Python 3.10.

    Handles trailing Z and variable fractional-second precision (including
    nanoseconds from Alertmanager).
    """
    t = text.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    if "." in t:
        head, frac = t.split(".", 1)
        offset = ""
        for marker in ("+", "-"):
            if marker in frac:
                frac, _, tail = frac.partition(marker)
                offset = marker + tail
                break
        frac = frac[:6].ljust(6, "0")
        t = f"{head}.{frac}{offset}"
    return datetime.fromisoformat(t)
