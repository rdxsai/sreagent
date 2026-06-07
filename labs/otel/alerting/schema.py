"""AlertRule model and rules.yaml loader."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from labs.otel.alerting.allowlist import assert_rule_templates_safe


class AlertRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alertname: str = Field(min_length=1)
    expr: str = Field(min_length=1)
    threshold: float
    comparison: Literal[">", ">=", "<", "<="]
    for_seconds: int = Field(ge=0)
    severity: Literal["warning", "critical"]
    labels: dict[str, str] = Field(default_factory=dict)
    annotation_templates: dict[str, str] = Field(default_factory=dict)


def load_rules() -> list[AlertRule]:
    """Load and validate alert rules from rules.yaml in the package directory."""
    rules_path = Path(__file__).parent / "rules.yaml"
    with rules_path.open() as fh:
        data = yaml.safe_load(fh)

    raw_rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(raw_rules, list):
        raise ValueError(f"rules.yaml must contain a top-level 'rules' list; got {type(raw_rules)}")

    rules: list[AlertRule] = []
    for entry in raw_rules:
        rule = AlertRule.model_validate(entry)
        assert_rule_templates_safe(rule)
        rules.append(rule)

    return rules
