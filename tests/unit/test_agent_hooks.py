"""Tests for the deterministic control hooks."""

from __future__ import annotations

from types import SimpleNamespace

from sentinel.agent.hooks import (
    BudgetGuard,
    FindingValidator,
    HookRunner,
    Hook,
    Observer,
    ReportGate,
    RunContext,
    SealGuard,
    ToolCall,
    ToolDecision,
    default_hooks,
)
from sentinel.tools.models import ServiceFinding


class _FakeStore:
    def list_changes(self, **_kw):
        return [SimpleNamespace(id=cid) for cid in ("chg_0001", "chg_0002", "chg_0003")]


def _ctx(**kw):
    return RunContext(store=_FakeStore(), **kw)


# ---- SealGuard ------------------------------------------------------------


def test_seal_guard_denies_forbidden_input_reference() -> None:
    d = SealGuard().pre_tool_use(ToolCall("traces_find", {"path": "fixtures/x/eval_only/truth.json"}), _ctx())
    assert d is not None and d.action == "deny"


def test_seal_guard_allows_clean_input() -> None:
    assert SealGuard().pre_tool_use(ToolCall("traces_find", {"service": "payment"}), _ctx()) is None


def test_seal_guard_redacts_banned_tokens_from_output() -> None:
    ctx = _ctx()
    out = SealGuard().post_tool_use(ToolCall("x", {}), {"note": "flag adHighCpu via feature_flag"}, ctx)
    assert "adHighCpu" not in out["note"] and "feature_flag" not in out["note"]
    assert "[redacted]" in out["note"]
    assert ctx.redactions == 1


# ---- BudgetGuard ----------------------------------------------------------


def test_budget_guard_denies_past_budget() -> None:
    ctx = _ctx(max_tool_calls=5, tool_calls=5)
    d = BudgetGuard().pre_tool_use(ToolCall("traces_find", {}), ctx)
    assert d is not None and d.action == "deny"


def test_budget_guard_always_allows_the_report() -> None:
    ctx = _ctx(max_tool_calls=5, tool_calls=99)
    assert BudgetGuard().pre_tool_use(ToolCall("report_root_cause", {}), ctx) is None


# ---- ReportGate -----------------------------------------------------------


def test_report_gate_denies_culprit_in_ruled_out() -> None:
    report = {
        "root_cause": {"kind": "service", "service": "payment", "type": "x"},
        "culprit_change_id": "chg_0003",
        "ruled_out_change_ids": ["chg_0003"],
    }
    d = ReportGate().pre_tool_use(ToolCall("report_root_cause", report), _ctx())
    assert d is not None and d.action == "deny"


def test_report_gate_allows_clean_report() -> None:
    report = {
        "root_cause": {"kind": "service", "service": "payment", "type": "x"},
        "culprit_change_id": "chg_0003",
        "ruled_out_change_ids": ["chg_0001", "chg_0002"],
    }
    assert ReportGate().pre_tool_use(ToolCall("report_root_cause", report), _ctx()) is None


# ---- Observer -------------------------------------------------------------


def test_observer_records_events() -> None:
    ctx = _ctx()
    Observer().post_tool_use(ToolCall("traces_find", {}), {"spans": []}, ctx)
    Observer().post_tool_use(ToolCall("metrics_series", {}), {"error": {"code": "x"}}, ctx)
    assert [e["tool"] for e in ctx.events] == ["traces_find", "metrics_series"]
    assert ctx.events[1]["error"] is True


# ---- FindingValidator -----------------------------------------------------


def test_finding_validator_accepts_complete_finding() -> None:
    f = ServiceFinding(service="ad", is_origin=True, fault_type="cpu_saturation", confidence=0.9, evidence=["cpu high"])
    v = FindingValidator().subagent_stop("inv-ad", f, _ctx())
    assert v is None or v.accept


def test_finding_validator_rejects_origin_without_evidence() -> None:
    f = ServiceFinding(service="ad", is_origin=True, fault_type="cpu_saturation", confidence=0.9, evidence=[])
    v = FindingValidator().subagent_stop("inv-ad", f, _ctx())
    assert v is not None and not v.accept


def test_finding_validator_rejects_none() -> None:
    v = FindingValidator().subagent_stop("inv-ad", None, _ctx())
    assert v is not None and not v.accept


# ---- HookRunner -----------------------------------------------------------


def test_hook_runner_deny_short_circuits() -> None:
    ctx = _ctx(max_tool_calls=0, tool_calls=0)
    runner = HookRunner(default_hooks())
    d = runner.pre_tool_use(ToolCall("traces_find", {}), ctx)
    assert d.action == "deny"  # BudgetGuard denies at budget 0


def test_hook_runner_post_chains_redaction_and_observation() -> None:
    ctx = _ctx()
    runner = HookRunner(default_hooks())
    out = runner.post_tool_use(ToolCall("x", {}), {"note": "adHighCpu"}, ctx)
    assert "[redacted]" in out["note"]  # SealGuard ran
    assert ctx.events and ctx.events[0]["tool"] == "x"  # Observer ran


def test_hook_runner_subagent_stop_reject_wins() -> None:
    runner = HookRunner(default_hooks())
    v = runner.subagent_stop("inv", None, _ctx())
    assert not v.accept


def test_modify_decision_is_applied() -> None:
    class _Modifier(Hook):
        def pre_tool_use(self, call, ctx):
            return ToolDecision(action="modify", modified_input={**call.input, "added": 1})

    runner = HookRunner([_Modifier()])
    d = runner.pre_tool_use(ToolCall("x", {"a": 1}), _ctx())
    assert d.action == "modify" and d.modified_input == {"a": 1, "added": 1}
