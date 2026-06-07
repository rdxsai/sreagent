from __future__ import annotations
import logging
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from sentinel.fixtures.schemas import DerivedAlert
from labs.otel.alerting.allowlist import AllowlistError
from labs.otel.alerting.webhook import alertmanager_payload_to_alerts

logger = logging.getLogger("sentinel.alert")

OnAlert = Callable[[list[DerivedAlert]], None]


def default_on_alert(alerts: list[DerivedAlert]) -> None:
    # SEAM: the real agent kickoff is wired here once the agent exists (see plan).
    logger.info("received %d alert(s): %s", len(alerts), [a.alertname for a in alerts])


def handle_alert_payload(payload: dict[str, Any], on_alert: OnAlert) -> dict[str, Any]:
    """Map an Alertmanager payload to DerivedAlerts and dispatch. Pure of HTTP for testing."""
    alerts = alertmanager_payload_to_alerts(payload)  # may raise AllowlistError / ValidationError
    on_alert(alerts)
    return {"received": len(alerts), "alertnames": [a.alertname for a in alerts]}


def create_app(on_alert: OnAlert = default_on_alert) -> FastAPI:
    app = FastAPI(title="Sentinel alert receiver")

    @app.post("/alert")
    async def alert(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            return handle_alert_payload(payload, on_alert)
        except (AllowlistError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return app


app = create_app()
