from __future__ import annotations
import logging
import threading
from typing import Any, Callable

import anthropic
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from sentinel.fixtures.schemas import DerivedAlert
from sentinel.tools.store import TelemetryStore
from labs.otel.alerting.allowlist import AllowlistError
from labs.otel.alerting.webhook import alertmanager_payload_to_alerts

logger = logging.getLogger("sentinel.alert")

OnAlert = Callable[[list[DerivedAlert]], None]


def default_on_alert(alerts: list[DerivedAlert]) -> None:
    # Inert default: no telemetry source or credentials at import time. Production
    # wires the real agent via make_agent_on_alert(client, store) below.
    logger.info("received %d alert(s): %s", len(alerts), [a.alertname for a in alerts])


def make_agent_on_alert(client: anthropic.Anthropic, store: TelemetryStore) -> OnAlert:
    """Production wiring: each firing alert batch kicks off an agent investigation
    against the configured telemetry store, in the background so the webhook returns
    immediately."""
    from sentinel.agent.runner import investigate

    def _on_alert(alerts: list[DerivedAlert]) -> None:
        names = [a.alertname for a in alerts]
        summaries = [getattr(a, "annotations", {}).get("summary", "") for a in alerts]
        symptom = "; ".join(s for s in summaries if s) or "; ".join(names) or "user-facing degradation"
        threading.Thread(
            target=lambda: investigate(client, store, symptom, names), daemon=True
        ).start()

    return _on_alert


def handle_alert_payload(payload: dict[str, Any], on_alert: OnAlert) -> dict[str, Any]:
    """Map an Alertmanager payload to DerivedAlerts and dispatch. Pure of HTTP for testing."""
    alerts = alertmanager_payload_to_alerts(payload)  # may raise AllowlistError / ValidationError
    on_alert(alerts)
    return {"received": len(alerts), "alertnames": [a.alertname for a in alerts]}


def create_app(on_alert: OnAlert = default_on_alert) -> FastAPI:
    app = FastAPI(title="Sentinel")

    # Dev convenience: the Vite frontend runs on a different origin. In production the
    # built frontend is served same-origin so this is a no-op.
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from sentinel.api.demo import router as demo_router

    app.include_router(demo_router)

    # Action half (gated remediation). Consumes the journal, never the eval path's live
    # objects. Mounted here as the server surface for Slack + web approvals.
    from pathlib import Path as _Path

    from sentinel.actions.api import make_action_router
    from sentinel.actions.journal import ActionJournal

    _action_journal = ActionJournal(_Path("runs/actions/server.jsonl"))
    app.include_router(make_action_router(_action_journal))

    # Live lab dashboard (Sock Shop): run control, SSE stream, telemetry, replay.
    # Deps are built lazily per run, so mounting costs nothing without docker/keys.
    from sentinel.api.livelab.router import make_livelab_router

    app.include_router(make_livelab_router())

    @app.post("/alert")
    async def alert(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            return handle_alert_payload(payload, on_alert)
        except (AllowlistError, ValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # Serve the built frontend (if present) same-origin, after the API routes.
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app


app = create_app()
