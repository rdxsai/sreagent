"""Action routes: Slack interactivity (verified) + a web-approve fallback on the same journal
and gate. Approval, wherever it comes from, folds into the journal and drives execute_approved
in the background. The journal's single-use idempotency makes Slack retries safe.
"""

from __future__ import annotations

import json
import os
import threading

from fastapi import APIRouter, Request

from sentinel.actions.executor import DryRunExecutor
from sentinel.actions.gate import execute_approved
from sentinel.actions.journal import ActionJournal
from sentinel.actions.slack import post_thread
from sentinel.actions.verify import verify_slack


def make_action_router(journal: ActionJournal, *, executor=None, slack_ts_by_action=None) -> APIRouter:
    router = APIRouter()
    executor = executor or DryRunExecutor()
    slack_ts = slack_ts_by_action if slack_ts_by_action is not None else {}

    def _run_execution(action_id: str) -> None:
        st = journal.state_of(action_id)
        if st is None:
            return
        res = execute_approved(journal, st.action, executor)
        ts = slack_ts.get(action_id)
        if ts:
            post_thread(ts, f"Execution: {res.status} — `{executor.render(st.action)}`")

    @router.post("/slack/interact")
    async def slack_interact(request: Request) -> dict:
        raw = await request.body()   # RAW bytes, not re-serialized
        secret = os.environ.get("SLACK_SIGNING_SECRET")
        ok = verify_slack(secret, request.headers.get("X-Slack-Request-Timestamp"),
                          request.headers.get("X-Slack-Signature"), raw)
        if not ok:
            return {"ok": False, "error": "signature verification failed"}   # fail closed
        # Slack sends form-encoded payload=<json>
        from urllib.parse import parse_qs
        form = parse_qs(raw.decode())
        payload = json.loads(form.get("payload", ["{}"])[0])
        action = (payload.get("actions") or [{}])[0]
        action_id, decision = action.get("value"), action.get("action_id")
        approver = (payload.get("user") or {}).get("username", "slack-user")
        if not action_id:
            return {"ok": False, "error": "no action id"}
        if decision == "approve":
            journal.approved(action_id, approver, "slack")
            threading.Thread(target=_run_execution, args=(action_id,), daemon=True).start()
        elif decision == "deny":
            journal.denied(action_id, approver)
        return {"ok": True}   # ack within 3s; execution runs in background

    # -- web fallback (same journal, same gate) --------------------------------
    def _web_authed(request: Request) -> bool:
        want = os.environ.get("ACTION_WEB_SECRET")
        return bool(want) and request.headers.get("X-Action-Secret") == want

    @router.get("/actions/pending")
    async def pending(request: Request) -> dict:
        if not _web_authed(request):
            return {"ok": False, "error": "unauthorized"}
        out = [{"id": aid, "status": st.status, "kind": st.action.kind,
                "target": st.action.target_service, "preview": st.action.preview}
               for aid, st in journal.fold().items() if st.status in ("proposed", "posted")]
        return {"ok": True, "pending": out}

    @router.post("/actions/{action_id}/approve")
    async def web_approve(action_id: str, request: Request) -> dict:
        if not _web_authed(request):
            return {"ok": False, "error": "unauthorized"}
        journal.approved(action_id, "web-user", "web")
        threading.Thread(target=_run_execution, args=(action_id,), daemon=True).start()
        return {"ok": True}

    @router.post("/actions/{action_id}/deny")
    async def web_deny(action_id: str, request: Request) -> dict:
        if not _web_authed(request):
            return {"ok": False, "error": "unauthorized"}
        journal.denied(action_id, "web-user")
        return {"ok": True}

    return router
