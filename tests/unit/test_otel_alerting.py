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


def test_rules_are_unified_and_leak_safe():
    from labs.otel.alerting import load_rules, ALLOWED_ALERTNAMES
    rules = load_rules()
    assert len(rules) >= 2
    # one unified, allow-listed trigger across the whole set
    assert all(r.alertname == "UserFacingDegradation" for r in rules)
    assert all(r.alertname in ALLOWED_ALERTNAMES for r in rules)
    error_rules = [r for r in rules if "status_code" in r.expr]
    latency_rules = [r for r in rules if "histogram_quantile" in r.expr]
    assert error_rules and latency_rules
    # error rule scoped to the user-facing edge
    assert all("frontend|checkout" in r.expr for r in error_rules)
    # latency rule aggregates over a generic service set (=~ with alternation),
    # so it never singles out one culprit service
    assert all(("=~" in r.expr and "|" in r.expr) for r in latency_rules)
    # async/batch services are excluded (they pin to the histogram ceiling)
    for r in rules:
        for batch in ("accounting", "fraud-detection", "load-generator"):
            assert batch not in r.expr


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


def test_evaluator_dedupes_rules_with_same_fingerprint():
    # The unified trigger is two rules (error + latency) sharing alertname+labels.
    # When both breach they must collapse to a single fired alert.
    breach = _series([(300, 0.5), (315, 0.5), (330, 0.5), (345, 0.5), (360, 0.5)])
    fake = _FakeRange({"err": breach, "lat": breach})
    rules = [
        _rule("UserFacingDegradation", "err", signal="degradation"),
        _rule("UserFacingDegradation", "lat", threshold=120, signal="degradation"),
    ]
    alerts = _derive(rules, fake)
    assert len(alerts) == 1
    assert alerts[0].alertname == "UserFacingDegradation"


def test_evaluator_keeps_distinct_alertnames():
    # Different alertnames have different fingerprints and must not be collapsed.
    breach = _series([(300, 0.5), (315, 0.5), (330, 0.5), (345, 0.5), (360, 0.5)])
    fake = _FakeRange({"a": breach, "b": breach})
    rules = [_rule("CheckoutFailureRate", "a"), _rule("FrontendHighErrorRate", "b", signal="error_rate")]
    alerts = _derive(rules, fake)
    assert len(alerts) == 2


# ---------------------------------------------------------------------------
# _validate_alerts gate tests (Task A6)
# ---------------------------------------------------------------------------

from labs.otel.validator import _validate_alerts


def _good_alert(**o):
    base = dict(alertname="CheckoutFailureRate", severity="critical", starts_at_second=312,
                labels={"tier": "user_facing", "signal": "checkout_error_rate"},
                annotations={"summary": "rate 0.5 at 312"}, value=0.5, expr="sum(...)", fingerprint="ab12")
    base.update(o)
    from sentinel.fixtures.schemas import DerivedAlert
    return DerivedAlert(**base)


def test_validate_alerts_accepts_clean_set():
    errors = []
    _validate_alerts([_good_alert()], window_end=900, errors=errors)
    assert errors == []


def test_validate_alerts_flags_non_allowlisted_alertname():
    errors = []
    _validate_alerts([_good_alert(alertname="PaymentChargeFailure")], window_end=900, errors=errors)
    assert errors and "symptom-level" in errors[0]


def test_validate_alerts_flags_label_interpolating_annotation():
    errors = []
    _validate_alerts([_good_alert(annotations={"summary": "down due to {{ $labels.service }}"})], window_end=900, errors=errors)
    assert any("forbidden token" in e for e in errors)


def test_validate_alerts_flags_onset_outside_window():
    errors = []
    _validate_alerts([_good_alert(starts_at_second=950)], window_end=900, errors=errors)
    assert any("outside window" in e for e in errors)


def test_validate_alerts_flags_empty():
    errors = []
    _validate_alerts([], window_end=900, errors=errors)
    assert errors and "no alerts" in errors[0]


# ---------------------------------------------------------------------------
# Prometheus rule compilation tests (Task B1)
# ---------------------------------------------------------------------------


def test_compile_prometheus_rules_shape():
    from labs.otel.alerting import load_rules
    from labs.otel.alerting.prometheus_rules import compile_prometheus_rules
    rules = load_rules()
    doc = compile_prometheus_rules(rules)
    out_rules = doc["groups"][0]["rules"]
    assert len(out_rules) == len(rules)
    r0 = out_rules[0]
    assert r0["alert"] == "UserFacingDegradation"
    assert r0["for"] == "60s"
    assert r0["labels"]["severity"] == "critical"
    assert r0["labels"]["tier"] == "user_facing"
    assert r0["expr"].startswith("(")  # original expr wrapped
    assert any(tail in r0["expr"] for tail in ("> 0.05", "> 120"))  # comparison + threshold appended
    # offline onset token translated; no leftover offline tokens
    assert "{{starts_at}}" not in r0["annotations"]["summary"]
    assert "{{value}}" not in r0["annotations"]["summary"]


def test_dump_prometheus_rules_is_yaml():
    import yaml
    from labs.otel.alerting import load_rules
    from labs.otel.alerting.prometheus_rules import dump_prometheus_rules
    parsed = yaml.safe_load(dump_prometheus_rules(load_rules()))
    assert parsed["groups"][0]["name"] == "sentinel_symptom_alerts"


# ---------------------------------------------------------------------------
# Alertmanager webhook mapper tests (Task B2)
# ---------------------------------------------------------------------------


def test_webhook_maps_firing_alerts_with_relative_onset():
    from labs.otel.alerting.webhook import alertmanager_payload_to_alerts
    payload = {"status": "firing", "alerts": [
        {"status": "firing",
         "labels": {"alertname": "CheckoutFailureRate", "severity": "critical", "tier": "user_facing", "signal": "checkout_error_rate"},
         "annotations": {"summary": "Checkout failure rate 0.5 onset", "value": "0.5"},
         "startsAt": "2026-06-06T21:05:00Z"},
        {"status": "firing",
         "labels": {"alertname": "FrontendHighLatency", "severity": "warning", "tier": "user_facing", "signal": "latency_p95"},
         "annotations": {"summary": "p95 high"},
         "startsAt": "2026-06-06T21:04:00Z"},
    ]}
    alerts = alertmanager_payload_to_alerts(payload)
    assert len(alerts) == 2
    by = {a.alertname: a for a in alerts}
    assert by["CheckoutFailureRate"].starts_at_second == 60   # 21:05 - earliest(21:04)
    assert by["FrontendHighLatency"].starts_at_second == 0
    assert by["CheckoutFailureRate"].value == 0.5
    assert all(a.labels.get("tier") == "user_facing" for a in alerts)
    assert by["CheckoutFailureRate"].fingerprint  # non-empty


def test_webhook_skips_resolved_alerts():
    from labs.otel.alerting.webhook import alertmanager_payload_to_alerts
    payload = {"alerts": [
        {"status": "resolved", "labels": {"alertname": "CheckoutFailureRate", "severity": "critical", "tier": "user_facing", "signal": "checkout_error_rate"}, "annotations": {}, "startsAt": "2026-06-06T21:05:00Z"},
    ]}
    assert alertmanager_payload_to_alerts(payload) == []


def test_webhook_rejects_non_allowlisted_alert():
    import pytest
    from labs.otel.alerting.allowlist import AllowlistError
    from labs.otel.alerting.webhook import alertmanager_payload_to_alerts
    payload = {"alerts": [
        {"status": "firing", "labels": {"alertname": "PaymentChargeFailure", "severity": "critical", "tier": "user_facing", "signal": "checkout_error_rate"}, "annotations": {}, "startsAt": "2026-06-06T21:05:00Z"},
    ]}
    with pytest.raises(AllowlistError):
        alertmanager_payload_to_alerts(payload)


def test_webhook_parses_nanosecond_z_timestamps():
    from labs.otel.alerting.webhook import alertmanager_payload_to_alerts
    payload = {"alerts": [
        {"status": "firing", "labels": {"alertname": "CheckoutFailureRate", "severity": "critical", "tier": "user_facing", "signal": "checkout_error_rate"}, "annotations": {"value": "0.3"}, "startsAt": "2026-06-06T21:05:00.843219842Z"},
    ]}
    alerts = alertmanager_payload_to_alerts(payload)
    assert len(alerts) == 1 and alerts[0].starts_at_second == 0
