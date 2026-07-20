"""Component 5: the four CORE SECURITY TESTS, written before any Slack code.

The safety invariant: zero mutate executions without a matching prior approval. These prove
it against the gate directly, with no network surface. Plus the ApprovalGuard tripwire.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from sentinel.actions.executor import DryRunExecutor
from sentinel.actions.gate import ApprovalGuard, execute_approved
from sentinel.actions.journal import ActionJournal
from sentinel.actions.models import SuggestedAction
from sentinel.registry import REGISTRY


def _j(tmp_path) -> ActionJournal:
    return ActionJournal(tmp_path / "journal.jsonl")


def _action(aid="a1", params=None):
    return SuggestedAction(id=aid, kind="restart", effect="mutate", target_service="orders",
                           params=params or {}).with_hash()


def _propose_and_approve(j, a, *, approver="alice", expires_at=None):
    j.proposed(a, expires_at=expires_at)
    j.posted(a.id, "web", "u")
    j.approved(a.id, approver, "web")


# 1. mutate without approval: denied, nothing executes ----------------------------------
def test_mutate_without_approval_denied(tmp_path: Path):
    j = _j(tmp_path); a = _action()
    j.proposed(a); j.posted(a.id, "web", "u")   # posted, NOT approved
    res = execute_approved(j, a, DryRunExecutor())
    assert res.executed is False and res.status == "not_approved"
    kinds = [e["kind"] for e in j.events()]
    assert "execute_started" not in kinds and "execute_result" not in kinds


# 2. approve action A, attempt execute with altered params: hash mismatch, near_miss -----
def test_altered_params_hash_mismatch(tmp_path: Path):
    j = _j(tmp_path)
    approved = _action(params={"replicas": 2})
    _propose_and_approve(j, approved)
    tampered = _action(params={"replicas": 9})   # same id, different params -> different hash
    res = execute_approved(j, tampered, DryRunExecutor())
    assert res.executed is False and res.status == "denied_hash"
    assert any(e["kind"] == "near_miss" for e in j.events())
    assert not any(e["kind"] == "execute_result" for e in j.events())


# 3. approve, execute, re-trigger: second execute no-ops (single use) --------------------
def test_single_use_idempotent(tmp_path: Path):
    j = _j(tmp_path); a = _action()
    _propose_and_approve(j, a)
    first = execute_approved(j, a, DryRunExecutor())
    assert first.executed is True and first.status == "done"
    second = execute_approved(j, a, DryRunExecutor())   # Slack retry / double click
    assert second.executed is False and second.status == "already_consumed"
    assert [e["kind"] for e in j.events()].count("execute_result") == 1


# 4. expiry: approve after TTL: rejected -------------------------------------------------
def test_expired_approval_rejected(tmp_path: Path):
    j = _j(tmp_path); a = _action()
    _propose_and_approve(j, a, expires_at=1000.0)
    res = execute_approved(j, a, DryRunExecutor(), now=2000.0)   # well past TTL
    assert res.executed is False and res.status == "expired"
    assert not any(e["kind"] == "execute_result" for e in j.events())
    assert j.state_of(a.id).status == "expired"


# ApprovalGuard tripwire: a mutate tool call without a matching token is denied + near_miss
class _In(BaseModel):
    id: str = "a1"
    params_hash: str = ""


class _Out(BaseModel):
    ok: bool = True


def test_approval_guard_denies_unapproved_mutate(tmp_path: Path):
    from sentinel.agent.hooks import RunContext, ToolCall

    @REGISTRY.tool(namespace="gatetest", effect="mutate")
    def gatetest_restart(params: _In, store: object) -> _Out:  # noqa: ARG001
        """mutate tool for the guard test."""
        return _Out()

    try:
        j = _j(tmp_path)
        guard = ApprovalGuard(j)
        ctx = RunContext(store=None, agent_id="x", max_tool_calls=5)   # no approval_token
        call = ToolCall(name="gatetest_restart", input={"id": "a1", "params_hash": "deadbeef"})
        decision = guard.pre_tool_use(call, ctx)
        assert decision is not None and decision.action == "deny"
        assert any(e["kind"] == "near_miss" for e in j.events())

        # with a matching token, the guard allows
        ctx.approval_token = "deadbeef"
        assert guard.pre_tool_use(call, ctx) is None
    finally:
        REGISTRY._specs.pop("gatetest_restart", None)
