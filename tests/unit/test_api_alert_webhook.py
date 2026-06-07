from __future__ import annotations
import pytest
from labs.otel.alerting.allowlist import AllowlistError
from sentinel.api.app import handle_alert_payload, create_app


def _firing_payload(alertname="CheckoutFailureRate", signal="checkout_error_rate"):
    return {"status": "firing", "alerts": [
        {"status": "firing",
         "labels": {"alertname": alertname, "severity": "critical", "tier": "user_facing", "signal": signal},
         "annotations": {"summary": "x", "value": "0.5"},
         "startsAt": "2026-06-06T21:05:00Z"},
    ]}


def test_handle_alert_payload_dispatches_mapped_alerts():
    captured = []
    result = handle_alert_payload(_firing_payload(), captured.append)
    assert result == {"received": 1, "alertnames": ["CheckoutFailureRate"]}
    assert len(captured) == 1 and captured[0][0].alertname == "CheckoutFailureRate"


def test_handle_alert_payload_rejects_non_allowlisted():
    with pytest.raises(AllowlistError):
        handle_alert_payload(_firing_payload(alertname="PaymentChargeFailure"), lambda a: None)


def test_alert_endpoint_returns_200(monkeypatch):
    pytest.importorskip("httpx")  # FastAPI TestClient needs httpx; skip if absent
    from fastapi.testclient import TestClient
    captured = []
    client = TestClient(create_app(on_alert=captured.append))
    resp = client.post("/alert", json=_firing_payload())
    assert resp.status_code == 200
    assert resp.json()["received"] == 1
    assert len(captured) == 1


def test_alert_endpoint_rejects_non_allowlisted(monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    client = TestClient(create_app())
    resp = client.post("/alert", json=_firing_payload(alertname="PaymentChargeFailure"))
    assert resp.status_code == 400
