"""Component 2: the journal fold is the single source of truth. Every legal event
sequence reaches exactly one terminal state; illegal/out-of-order events are ignored,
not crash, and cannot revive a terminal action."""

from __future__ import annotations

from pathlib import Path

from sentinel.actions.journal import ActionJournal
from sentinel.actions.models import SuggestedAction, params_hash


def _action(aid="a1", kind="restart", target="orders", params=None):
    params = params or {}
    return SuggestedAction(id=aid, kind=kind, effect="mutate", target_service=target,
                           params=params).with_hash()


def _j(tmp_path) -> ActionJournal:
    return ActionJournal(tmp_path / "journal.jsonl")


def test_happy_path_reaches_done(tmp_path: Path):
    j = _j(tmp_path); a = _action()
    j.proposed(a); j.posted(a.id, "web", "url"); j.approved(a.id, "alice", "web")
    j.execute_started(a.id, "dry_run", a.preview); j.execute_result(a.id, ok=True, duration_s=0.1)
    st = j.state_of(a.id)
    assert st.status == "done" and st.approver == "alice"
    assert j.is_terminal(a.id)


def test_deny_is_terminal(tmp_path: Path):
    j = _j(tmp_path); a = _action()
    j.proposed(a); j.posted(a.id, "web", "u"); j.denied(a.id, "bob", "too risky")
    assert j.state_of(a.id).status == "denied" and j.is_terminal(a.id)


def test_approve_after_expire_is_rejected(tmp_path: Path):
    j = _j(tmp_path); a = _action()
    j.proposed(a); j.posted(a.id, "web", "u"); j.expired(a.id)
    j.approved(a.id, "mallory", "web")   # out of order: expired is terminal
    assert j.state_of(a.id).status == "expired"   # approve ignored, stays expired


def test_execute_without_approval_does_not_advance(tmp_path: Path):
    j = _j(tmp_path); a = _action()
    j.proposed(a); j.posted(a.id, "web", "u")
    j.execute_started(a.id, "dry_run", a.preview)   # no approval -> illegal
    assert j.state_of(a.id).status == "posted"      # unchanged


def test_near_miss_does_not_change_state(tmp_path: Path):
    j = _j(tmp_path); a = _action()
    j.proposed(a)
    j.near_miss(a.id, attempted="restart orders", by="model", why="no approval")
    assert j.state_of(a.id).status == "proposed"
    kinds = [e["kind"] for e in j.events()]
    assert "near_miss" in kinds   # audited, present in the stream


def test_every_action_reaches_one_state(tmp_path: Path):
    j = _j(tmp_path)
    a, b = _action("a1"), _action("a2")
    j.proposed(a); j.proposed(b)
    j.posted(a.id, "web", "u"); j.approved(a.id, "x", "web")
    j.execute_started(a.id, "dry_run", ""); j.execute_result(a.id, ok=False, error="boom")
    j.posted(b.id, "web", "u"); j.denied(b.id, "y")
    fold = j.fold()
    assert fold[a.id].status == "failed"
    assert fold[b.id].status == "denied"
    assert set(fold) == {"a1", "a2"}


def test_params_hash_binds_content():
    h1 = params_hash("restart", "orders", {"replicas": 2})
    h2 = params_hash("restart", "orders", {"replicas": 3})   # different params
    h3 = params_hash("restart", "orders", {"replicas": 2})   # same as h1
    assert h1 != h2 and h1 == h3
