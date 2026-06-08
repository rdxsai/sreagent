"""Integration: the manager + investigator orchestration end to end, offline.

The Anthropic client is faked with a scripted tool-use sequence so the whole path
runs without the network: manager triage -> investigate_parallel (real subagent
threads) -> investigator findings -> reconcile -> report_root_cause, through the
hooks and real tool dispatch against a recorded fixture. The fake is stateless
(it derives each turn from the message history), so the parallel workers are safe.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sentinel.agent.runner import investigate
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
STORE = FixtureStore(ROOT / "fixtures" / "payment_failure_001" / "public")

_USAGE = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _tool(name, inp):
    return SimpleNamespace(type="tool_use", name=name, input=inp, id="t1")


def _resp(blocks, stop="tool_use"):
    return SimpleNamespace(content=blocks, stop_reason=stop, usage=_USAGE)


_REPORT = {
    "root_cause": {"kind": "service", "service": "payment", "type": "error"},
    "culprit_change_id": "chg_0003",
    "ruled_out_change_ids": ["chg_0001", "chg_0002", "chg_0004", "chg_0005", "chg_0006"],
    "evidence": ["payment server spans error after onset"],
    "timeline": [],
}


def _finding(service):
    origin = service == "payment"
    return {
        "service": service,
        "is_origin": origin,
        "fault_type": "error" if origin else None,
        "confidence": 0.9 if origin else 0.7,
        "evidence": ["payment charge spans error after onset"] if origin else [],
    }


class _FakeAnthropic:
    """Scripted, stateless fake: routes by exposed tools, branches by turn count."""

    def __init__(self):
        self.messages = self

    def create(self, *, messages, tools, **kwargs):
        names = {t["name"] for t in tools}
        n_assistant = sum(1 for m in messages if m.get("role") == "assistant")
        if "investigate_parallel" in names:  # manager
            if n_assistant == 0:
                return _resp([_tool("traces_build_topology", {})])
            if n_assistant == 1:
                return _resp([_tool("investigate_parallel", {"services": ["payment", "checkout"]})])
            return _resp([_tool("report_root_cause", _REPORT)])
        if "report_finding" in names:  # service investigator
            if n_assistant == 0:
                return _resp([_tool("metrics_summary_all", {})])
            goal = messages[0]["content"]
            service = goal.split("service '")[1].split("'")[0]
            return _resp([_tool("report_finding", _finding(service))])
        if "report_change_verdict" in names:  # change investigator (not used here)
            return _resp([_tool("report_change_verdict", {"change_id": "chg_0003", "is_culprit": True, "confidence": 0.8, "reason": "x"})])
        return _resp([SimpleNamespace(type="text", text="done")], stop="end_turn")


def test_manager_orchestration_runs_end_to_end_offline():
    result = investigate(_FakeAnthropic(), STORE, "Checkout attempts started failing", ["UserFacingDegradation"])
    # the manager produced a report by reconciling two real subagents
    assert result.report is not None
    assert result.report["root_cause"]["service"] == "payment"
    assert result.report["culprit_change_id"] == "chg_0003"
    assert result.subagents == 2
    assert len(result.findings) == 2
    # observability: the run produced a surfaced trace of tool events
    assert result.trace, "expected a non-empty event trace"
    assert any(e.get("agent", "").startswith("investigator") for e in result.trace)
