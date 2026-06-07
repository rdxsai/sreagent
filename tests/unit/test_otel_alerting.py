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


# ---------------------------------------------------------------------------
# Evaluator tests (Task A4)
# ---------------------------------------------------------------------------

from labs.otel.alerting.schema import AlertRule
from labs.otel.alerting.evaluator import derive_alerts, NoAlertFired

_WS, _WE = 1000.0, 1900.0


class _FakeRange:
    def __init__(self, series_by_expr):
        self.series_by_expr = series_by_expr

    def query_range(self, query, *, start, end, step):
        return {"result": [{"metric": {}, "values": self.series_by_expr.get(query, [])}]}


def _series(pairs):  # pairs: list of (offset_seconds, value)
    return [[_WS + off, str(v)] for off, v in pairs]


def _rule(name, expr, threshold=0.1, comparison=">", for_seconds=60, severity="critical", signal="checkout_error_rate"):
    return AlertRule(alertname=name, expr=expr, threshold=threshold, comparison=comparison,
                     for_seconds=for_seconds, severity=severity,
                     labels={"tier": "user_facing", "signal": signal},
                     annotation_templates={"summary": "v {{value}} at {{starts_at}}"})


def _derive(rules, fake):
    return derive_alerts(window_start_epoch_seconds=_WS, window_end_epoch_seconds=_WE, prometheus=fake, rules=rules)


def test_breach_shorter_than_for_seconds_does_not_fire():
    fake = _FakeRange({"e": _series([(0, 0.0), (300, 0.5), (315, 0.5), (330, 0.5), (345, 0.5), (360, 0.0)])})
    with pytest.raises(NoAlertFired):
        _derive([_rule("CheckoutFailureRate", "e")], fake)


def test_breach_at_boundary_fires_with_onset_and_value():
    fake = _FakeRange({"e": _series([(0, 0.0), (300, 0.5), (315, 0.5), (330, 0.5), (345, 0.5), (360, 0.5)])})
    alerts = _derive([_rule("CheckoutFailureRate", "e")], fake)
    assert len(alerts) == 1
    assert alerts[0].alertname == "CheckoutFailureRate"
    assert alerts[0].starts_at_second == 300
    assert alerts[0].value == 0.5


def test_multiple_rules_sorted_critical_then_onset_then_name():
    crit = _series([(300, 0.5), (315, 0.5), (330, 0.5), (345, 0.5), (360, 0.5)])           # start 300
    warn = _series([(270, 300.0), (285, 300.0), (300, 300.0), (315, 300.0), (330, 300.0)])  # start 270 (earlier)
    fake = _FakeRange({"crit": crit, "warn": warn})
    rules = [_rule("CheckoutFailureRate", "crit", severity="critical"),
             _rule("FrontendHighLatency", "warn", threshold=200, severity="warning", signal="latency_p95")]
    alerts = _derive(rules, fake)
    assert [a.alertname for a in alerts] == ["CheckoutFailureRate", "FrontendHighLatency"]  # critical first
    assert alerts[1].starts_at_second == 270


def test_no_firing_raises():
    fake = _FakeRange({"e": _series([(0, 0.0), (300, 0.0)])})
    with pytest.raises(NoAlertFired):
        _derive([_rule("CheckoutFailureRate", "e")], fake)


def test_derive_is_deterministic():
    fake = _FakeRange({"e": _series([(300, 0.5), (315, 0.5), (330, 0.5), (345, 0.5), (360, 0.5)])})
    rules = [_rule("CheckoutFailureRate", "e")]
    first = [a.model_dump() for a in _derive(rules, fake)]
    second = [a.model_dump() for a in _derive(rules, fake)]
    assert first == second
    assert first[0]["fingerprint"]  # non-empty stable hash
