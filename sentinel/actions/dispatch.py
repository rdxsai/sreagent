"""Shared decision handling for every approval surface (Slack Socket Mode, Slack HTTP
interactivity, web fallback). An approve/deny folds into the journal and, on approve, drives
execute_approved and posts the outcome to the brief's thread. Keeping this in one place means
the gate and audit behave identically no matter where the click came from.
"""

from __future__ import annotations

from sentinel.actions.executor import DryRunExecutor, Executor
from sentinel.actions.gate import GateResult, execute_approved
from sentinel.actions.journal import ActionJournal
from sentinel.actions.slack import post_thread


def _slack_ts(journal: ActionJournal, action_id: str) -> str | None:
    for e in journal.events():
        if e["kind"] == "posted" and e["action_id"] == action_id and e.get("surface") == "slack":
            return e.get("ref")
    return None


def handle_decision(
    journal: ActionJournal,
    action_id: str,
    decision: str,
    approver: str,
    surface: str,
    *,
    executor: Executor | None = None,
) -> GateResult | None:
    """approve -> journal.approved + execute_approved + thread outcome; deny -> journal.denied.
    Returns the GateResult on approve, None on deny/unknown."""
    executor = executor or DryRunExecutor()
    if decision == "deny":
        journal.denied(action_id, approver)
        return None
    if decision != "approve":
        return None

    st = journal.state_of(action_id)
    if st is None:
        return None
    journal.approved(action_id, approver, surface)
    res = execute_approved(journal, st.action, executor)
    ts = _slack_ts(journal, action_id)
    if ts:
        post_thread(ts, f"Execution: *{res.status}* — `{executor.render(st.action)}`")
    return res
