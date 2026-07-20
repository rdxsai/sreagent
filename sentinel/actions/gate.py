"""The gate. Two layers.

Structural (primary): a mutate op executes ONLY through execute_approved(), which is driven
by an approval event, not by the model. The proposing run has already exited; nothing in the
model's process can execute. This function re-checks the params_hash against the approval,
enforces single-use, and honors TTL.

Defense in depth: ApprovalGuard, a pre_tool_use hook that denies any effect="mutate" tool call
unless the run context carries an approval token whose params_hash matches, journaling a
near_miss otherwise. No investigation loop has mutate tools today (Component 1 excludes them),
so the guard is a tripwire proving the property rather than a routine code path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sentinel.actions.executor import Executor
from sentinel.actions.journal import ActionJournal
from sentinel.actions.models import SuggestedAction


@dataclass
class GateResult:
    executed: bool
    status: str          # done | failed | denied_hash | expired | already_consumed | not_approved
    detail: str = ""


def execute_approved(
    journal: ActionJournal,
    action: SuggestedAction,
    executor: Executor,
    *,
    now: float | None = None,
) -> GateResult:
    """Execute a mutate action iff its journal state is 'approved', the params_hash still
    matches, it is not expired, and it has not already been consumed. Idempotent per approval:
    a second trigger (Slack retry, double click) finds it consumed and no-ops."""
    now = now if now is not None else time.time()
    st = journal.state_of(action.id)
    if st is None or st.status != "approved":
        # includes already-executing/done (consumed), denied, expired, unknown
        if st is not None and st.status in ("executing", "done", "failed"):
            return GateResult(False, "already_consumed", f"status={st.status}")
        return GateResult(False, "not_approved", f"status={st.status if st else 'unknown'}")

    # TTL: an approval past its expiry is not honored
    if st.expires_at is not None and now > st.expires_at:
        journal.expired(action.id)
        return GateResult(False, "expired", "approval past TTL")

    # binding: the approval was granted for exact content; re-check the hash now
    approved_hash = st.action.params_hash or st.action.compute_hash()
    if action.compute_hash() != approved_hash:
        journal.near_miss(action.id, attempted=f"{action.kind} {action.target_service}",
                          by="executor", why="params_hash mismatch vs approval")
        return GateResult(False, "denied_hash", "params_hash mismatch")

    # consume the approval FIRST (single-use), then execute
    journal.execute_started(action.id, executor.backend, executor.render(action))
    outcome = executor.execute(action)
    journal.execute_result(action.id, ok=outcome.ok, before=outcome.before,
                           after=outcome.after, duration_s=outcome.duration_s, error=outcome.error)
    return GateResult(outcome.ok, "done" if outcome.ok else "failed", executor.render(action))


# -- defense in depth: the hook tripwire ------------------------------------------------

class ApprovalGuard:
    """pre_tool_use guard for any loop that could ever see a mutate tool. Denies unless the
    run context carries an approval token matching the call's params_hash; journals a near_miss.
    Mirrors the SealGuard/BudgetGuard shape (returns a deny decision or None)."""

    name = "approval_guard"

    def __init__(self, journal: ActionJournal | None = None) -> None:
        self._journal = journal

    def pre_tool_use(self, call, ctx):
        from sentinel.registry import REGISTRY
        spec = REGISTRY._specs.get(call.name)
        if spec is None or spec.effect != "mutate":
            return None  # not an action tool; unaffected
        token = getattr(ctx, "approval_token", None)
        want = call.input.get("params_hash") if isinstance(call.input, dict) else None
        if token and want and token == want:
            return None  # approved for exactly this content
        if self._journal is not None:
            self._journal.near_miss(call.input.get("id", "?") if isinstance(call.input, dict) else "?",
                                    attempted=call.name, by="model", why="mutate without matching approval")
        from sentinel.agent.hooks import ToolDecision
        return ToolDecision(action="deny", reason="mutate tool requires a matching human approval")
