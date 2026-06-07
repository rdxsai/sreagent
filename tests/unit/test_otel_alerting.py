import pytest
from sentinel.fixtures.schemas import DerivedAlert
from labs.otel.alerting.allowlist import (
    AllowlistError, assert_alert_is_symptom_level, assert_rule_templates_safe,
)


def _alert(**overrides):
    base = dict(
        alertname="CheckoutFailureRate", severity="critical", starts_at_second=312,
        labels={"tier": "user_facing", "signal": "checkout_error_rate"}, annotations={},
        value=0.25, expr="sum(...)", fingerprint="abc123",
    )
    base.update(overrides)
    return DerivedAlert(**base)


def test_allowlist_accepts_symptom_level_alert():
    assert_alert_is_symptom_level(_alert())  # no raise


def test_allowlist_rejects_unknown_alertname():
    with pytest.raises(AllowlistError):
        assert_alert_is_symptom_level(_alert(alertname="PaymentChargeFailure"))


def test_allowlist_rejects_unknown_label_value():
    with pytest.raises(AllowlistError):
        assert_alert_is_symptom_level(_alert(labels={"tier": "user_facing", "signal": "payment_errors"}))


def test_allowlist_rejects_unknown_label_key():
    with pytest.raises(AllowlistError):
        assert_alert_is_symptom_level(_alert(labels={"culprit": "payment"}))


def test_rule_templates_safe_rejects_label_interpolation():
    class FakeRule:
        annotation_templates = {"summary": "down because {{ $labels.service }}"}
    with pytest.raises(AllowlistError):
        assert_rule_templates_safe(FakeRule())


def test_rule_templates_safe_accepts_value_only_template():
    class FakeRule:
        annotation_templates = {"summary": "rate {{value}} since {{starts_at}}"}
    assert_rule_templates_safe(FakeRule())  # no raise


def test_derived_alert_rejects_unknown_field():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DerivedAlert(
            alertname="CheckoutFailureRate", severity="critical", starts_at_second=312,
            labels={"tier": "user_facing"}, annotations={}, value=0.25,
            expr="x", fingerprint="abc", culprit="payment",  # extra field must be rejected
        )


def test_derived_alert_roundtrips():
    a = DerivedAlert(
        alertname="CheckoutFailureRate", severity="critical", starts_at_second=312,
        labels={"tier": "user_facing", "signal": "checkout_error_rate"}, annotations={},
        value=0.25, expr="sum(...)", fingerprint="abc123",
    )
    assert a.model_dump(mode="json")["starts_at_second"] == 312


def test_rules_are_symptom_scoped_and_allowlisted():
    from labs.otel.alerting import load_rules, ALLOWED_ALERTNAMES
    rules = load_rules()
    assert len(rules) >= 3
    downstream = ["payment", "cart", "ad", "recommendation", "product-catalog", "kafka", "shipping"]
    for r in rules:
        assert r.alertname in ALLOWED_ALERTNAMES
        assert ('service_name="frontend"' in r.expr) or ('service_name="checkout"' in r.expr)
        assert not any(f'service_name="{d}"' in r.expr for d in downstream)
