from pathlib import Path

import sentinel.tools  # noqa: F401
from sentinel.agent.hooks import DecoyCompletenessGate, RunContext, ToolCall
from sentinel.tools.store import FixtureStore

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ad_high_cpu_001" / "public"


def _ctx() -> RunContext:
    return RunContext(store=FixtureStore(_FIXTURE), agent_id="code")


def _known(ctx) -> list[str]:
    return [c.id for c in ctx.store.list_changes()]


def test_denies_when_a_decoy_is_unaccounted():
    ctx = _ctx()
    known = _known(ctx)
    culprit = known[0]
    call = ToolCall("report_root_cause", {"culprit_change_id": culprit, "ruled_out_change_ids": known[1:-1]})
    decision = DecoyCompletenessGate().pre_tool_use(call, ctx)
    assert decision is not None and decision.action == "deny"
    assert known[-1] in decision.reason


def test_allows_when_all_decoys_ruled_out():
    ctx = _ctx()
    known = _known(ctx)
    culprit = known[0]
    call = ToolCall("report_root_cause", {"culprit_change_id": culprit, "ruled_out_change_ids": known[1:]})
    assert DecoyCompletenessGate().pre_tool_use(call, ctx) is None


def test_ignores_non_report_tools():
    ctx = _ctx()
    assert DecoyCompletenessGate().pre_tool_use(ToolCall("metrics_detect_shift", {}), ctx) is None
