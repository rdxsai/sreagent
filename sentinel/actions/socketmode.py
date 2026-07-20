"""Slack Socket Mode listener: the app opens an outbound WebSocket to Slack (no public URL,
no per-request HMAC; the connection, authenticated by the app-level token, is the trust
boundary). Interaction envelopes (Approve/Deny button clicks) arrive over the socket, are
acked within Slack's window, and dispatched to the SAME gate + journal every surface uses.

  python -m sentinel.actions.socketmode          # runs against runs/actions/server.jsonl

Needs SLACK_APP_TOKEN (xapp-, connections:write) and SLACK_BOT_TOKEN (for thread replies).
Hand-rolled on `websockets` to avoid adding slack_sdk.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import websockets

from sentinel.actions.dispatch import handle_decision
from sentinel.actions.executor import DryRunExecutor, Executor
from sentinel.actions.journal import ActionJournal

_OPEN = "https://slack.com/api/apps.connections.open"


def _open_socket_url(app_token: str) -> str:
    r = httpx.post(_OPEN, headers={"Authorization": f"Bearer {app_token}"}, timeout=20).json()
    if not r.get("ok"):
        raise RuntimeError(f"apps.connections.open failed: {r.get('error')}")
    return r["url"]


def _decision_from_payload(payload: dict) -> tuple[str | None, str, str]:
    """-> (action_id, decision, approver) from a block_actions payload."""
    action = (payload.get("actions") or [{}])[0]
    approver = (payload.get("user") or {}).get("username") or (payload.get("user") or {}).get("id", "slack-user")
    return action.get("value"), action.get("action_id", ""), approver


async def _run(journal: ActionJournal, executor: Executor, app_token: str,
               *, on_event=None, max_events: int | None = None) -> None:
    seen = 0
    while True:
        url = _open_socket_url(app_token)
        async with websockets.connect(url, max_size=2**20) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                mtype = msg.get("type")
                if mtype in ("hello", "disconnect"):
                    if mtype == "disconnect":
                        break  # reconnect via the outer loop
                    continue
                # ack first (Slack requires it within ~3s)
                if msg.get("envelope_id"):
                    await ws.send(json.dumps({"envelope_id": msg["envelope_id"]}))
                if mtype != "interactive":
                    continue
                payload = msg.get("payload", {})
                if payload.get("type") != "block_actions":
                    continue
                action_id, decision, approver = _decision_from_payload(payload)
                if not action_id:
                    continue
                res = handle_decision(journal, action_id, decision, approver, "slack", executor=executor)
                if on_event is not None:
                    on_event(action_id, decision, approver, res)
                seen += 1
                if max_events is not None and seen >= max_events:
                    return


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Slack Socket Mode approval listener.")
    ap.add_argument("--journal", default="runs/actions/server.jsonl")
    ap.add_argument("--max-events", type=int, default=None, help="stop after N interactions (demo)")
    args = ap.parse_args(argv)

    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token or not app_token.startswith("xapp-"):
        raise SystemExit("SLACK_APP_TOKEN (xapp-...) required for Socket Mode")
    journal = ActionJournal(Path(args.journal))

    def _log(aid, decision, approver, res):
        status = res.status if res else "denied"
        print(f"[socket] {decision} by {approver} -> {aid[:8]} : {status}", flush=True)

    print("[socket] connected; waiting for Approve/Deny clicks ...", flush=True)
    asyncio.run(_run(journal, DryRunExecutor(), app_token, on_event=_log, max_events=args.max_events))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
