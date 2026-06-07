"""Symptom allow-list and template safety checks for derived alerts."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentinel.fixtures.schemas import DerivedAlert

ALLOWED_ALERTNAMES: set[str] = {
    "CheckoutFailureRate",
    "FrontendHighErrorRate",
    "FrontendHighLatency",
    "CheckoutHighLatency",
}

ALLOWED_LABEL_KEYS: set[str] = {"tier", "signal", "severity"}

ALLOWED_LABEL_VALUES: dict[str, set[str]] = {
    "tier": {"user_facing"},
    "signal": {"error_rate", "checkout_error_rate", "latency_p95", "checkout_latency_p95"},
}

FORBIDDEN_ANNOTATION_SUBSTRINGS: tuple[str, ...] = (
    "labels.",
    "$labels",
    "{{labels",
    "service",
)


class AllowlistError(ValueError):
    """Raised when an alert or rule names a fault, classification, or interpolates a label."""


def assert_alert_is_symptom_level(alert: DerivedAlert) -> None:
    """Raise AllowlistError if the alert violates the symptom-level allow-list."""
    if alert.alertname not in ALLOWED_ALERTNAMES:
        raise AllowlistError(
            f"alertname {alert.alertname!r} is not in the symptom allow-list"
        )

    for key in alert.labels:
        if key not in ALLOWED_LABEL_KEYS:
            raise AllowlistError(
                f"label key {key!r} is not in ALLOWED_LABEL_KEYS"
            )

    for key, allowed_values in ALLOWED_LABEL_VALUES.items():
        if key in alert.labels and alert.labels[key] not in allowed_values:
            raise AllowlistError(
                f"label {key!r} has value {alert.labels[key]!r} which is not in the allowed set"
            )


def assert_rule_templates_safe(rule: object) -> None:
    """Raise AllowlistError if any annotation template contains a forbidden substring."""
    for _name, template in rule.annotation_templates.items():  # type: ignore[union-attr]
        for forbidden in FORBIDDEN_ANNOTATION_SUBSTRINGS:
            if forbidden in template:
                raise AllowlistError(
                    f"annotation template {_name!r} contains forbidden substring {forbidden!r}"
                )
