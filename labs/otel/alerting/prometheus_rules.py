"""Compile offline AlertRule definitions into a Prometheus alerting-rules document."""

from __future__ import annotations

import sys
import yaml

from labs.otel.alerting.schema import AlertRule
from labs.otel.alerting.schema import load_rules

_GROUP_NAME = "sentinel_symptom_alerts"


def _prometheus_annotations(annotation_templates: dict[str, str]) -> dict[str, str]:
    # Prometheus rule annotations expose {{ $value }} and {{ $labels.X }} only.
    # Our offline templates use {{value}} and {{starts_at}}; translate the value token
    # and replace the window-relative onset token (no Prometheus rule-annotation equivalent)
    # with a literal. The precise onset travels on the alert's startsAt field, mapped by the webhook.
    out: dict[str, str] = {}
    for key, text in annotation_templates.items():
        out[key] = text.replace("{{value}}", "{{ $value }}").replace("{{starts_at}}", "onset")
    return out


def compile_prometheus_rules(rules: list[AlertRule]) -> dict:
    return {
        "groups": [
            {
                "name": _GROUP_NAME,
                "rules": [
                    {
                        "alert": rule.alertname,
                        "expr": f"({rule.expr}) {rule.comparison} {rule.threshold}",
                        "for": f"{rule.for_seconds}s",
                        "labels": {**rule.labels, "severity": rule.severity},
                        "annotations": _prometheus_annotations(rule.annotation_templates),
                    }
                    for rule in rules
                ],
            }
        ]
    }


def dump_prometheus_rules(rules: list[AlertRule]) -> str:
    return yaml.safe_dump(compile_prometheus_rules(rules), sort_keys=False)


if __name__ == "__main__":  # python -m labs.otel.alerting.prometheus_rules > prometheus_rules.yml
    sys.stdout.write(dump_prometheus_rules(load_rules()))
