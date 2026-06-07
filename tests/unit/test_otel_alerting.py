from sentinel.fixtures.schemas import DerivedAlert


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
