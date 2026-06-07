"""Alert allow-list package for symptom-level derived alerts."""

from labs.otel.alerting.allowlist import (
    ALLOWED_ALERTNAMES,
    ALLOWED_LABEL_KEYS,
    ALLOWED_LABEL_VALUES,
    FORBIDDEN_ANNOTATION_SUBSTRINGS,
    AllowlistError,
    assert_alert_is_symptom_level,
    assert_rule_templates_safe,
)
from labs.otel.alerting.schema import AlertRule, load_rules
from labs.otel.alerting.evaluator import (
    derive_alerts,
    NoAlertFired,
    PrometheusRangeClient,
)
from labs.otel.alerting.coverage import (
    CoverageError,
    assert_coverage_invariants,
    build_coverage_matrix,
    write_coverage_matrix,
)
from labs.otel.alerting.prometheus_rules import (
    compile_prometheus_rules,
    dump_prometheus_rules,
)

__all__ = [
    "ALLOWED_ALERTNAMES",
    "ALLOWED_LABEL_KEYS",
    "ALLOWED_LABEL_VALUES",
    "FORBIDDEN_ANNOTATION_SUBSTRINGS",
    "AllowlistError",
    "assert_alert_is_symptom_level",
    "assert_rule_templates_safe",
    "AlertRule",
    "load_rules",
    "derive_alerts",
    "NoAlertFired",
    "PrometheusRangeClient",
    "CoverageError",
    "assert_coverage_invariants",
    "build_coverage_matrix",
    "write_coverage_matrix",
    "compile_prometheus_rules",
    "dump_prometheus_rules",
]
