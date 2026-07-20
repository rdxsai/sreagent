"""Option B: LiveExecutor behavior + the security invariant re-run with the live backend +
the kill switch. Side effects are injected fakes; no real docker/flagd/Prometheus touched."""

from __future__ import annotations

from pathlib import Path

from sentinel.actions.executor import DryRunExecutor
from sentinel.actions.gate import execute_approved
from sentinel.actions.journal import ActionJournal
from sentinel.actions.live_executor import LiveExecutor, make_live_executor
from sentinel.actions.models import SuggestedAction


def _action(aid="a1", kind="remove_impairment", target="kafka", params=None):
    return SuggestedAction(id=aid, kind=kind, effect="mutate", target_service=target,
                           params=params or {}).with_hash()


def _fake_live(calls: dict, health_seq):
    """LiveExecutor with fakes: records the side effect and returns a scripted health sequence."""
    seq = list(health_seq)
    return LiveExecutor(
        settle_s=0.0,
        flag_reset=lambda: calls.__setitem__("flag_reset", calls.get("flag_reset", 0) + 1),
        restart=lambda c: calls.__setitem__("restart", c),
        health=lambda _svc: seq.pop(0) if seq else None,
        sleep=lambda _s: None,
    )


def test_remove_impairment_resets_flag_and_confirms_recovery():
    calls = {}
    ex = _fake_live(calls, health_seq=[100.0, 10.0])   # lag 100 -> 10 after clear
    out = ex.execute(_action(kind="remove_impairment"))
    assert calls["flag_reset"] == 1
    assert out.ok and out.before == 100.0 and out.after == 10.0
    assert "not yet confirmed" not in out.error   # dropped >50% -> recovered


def test_restart_calls_docker_with_mapped_container():
    calls = {}
    ex = _fake_live(calls, health_seq=[5.0, 5.0])
    ex.execute(_action(kind="restart", target="ad"))
    assert calls["restart"] == "ad"


def test_recovery_not_confirmed_when_health_stays_high():
    calls = {}
    ex = _fake_live(calls, health_seq=[100.0, 90.0])   # barely moved
    out = ex.execute(_action(kind="remove_impairment"))
    assert out.ok and "recovery not yet confirmed" in out.error


# the gate is executor-agnostic: the same safety invariant holds with LiveExecutor -------
def test_no_live_execution_without_approval(tmp_path: Path):
    j = ActionJournal(tmp_path / "j.jsonl")
    calls = {}
    ex = _fake_live(calls, health_seq=[100.0, 10.0])
    a = _action()
    j.proposed(a); j.posted(a.id, "web", "u")          # NOT approved
    res = execute_approved(j, a, ex)
    assert res.executed is False and "flag_reset" not in calls   # no real side effect ran


def test_live_execution_runs_only_after_approval(tmp_path: Path):
    j = ActionJournal(tmp_path / "j.jsonl")
    calls = {}
    ex = _fake_live(calls, health_seq=[100.0, 10.0])
    a = _action()
    j.proposed(a); j.posted(a.id, "web", "u"); j.approved(a.id, "op", "web")
    res = execute_approved(j, a, ex)
    assert res.executed is True and calls.get("flag_reset") == 1


# kill switch --------------------------------------------------------------------------
def test_kill_switch_forces_dry_run(monkeypatch):
    monkeypatch.setenv("SENTINEL_FORCE_DRYRUN", "1")
    ex = make_live_executor(health_query="whatever")
    assert isinstance(ex, DryRunExecutor)   # live requested, dry-run forced
    monkeypatch.delenv("SENTINEL_FORCE_DRYRUN")
    assert isinstance(make_live_executor(health_query="whatever"), LiveExecutor)
