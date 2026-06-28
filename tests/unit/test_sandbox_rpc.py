# tests/unit/test_sandbox_rpc.py
from pathlib import Path

import sentinel.tools  # noqa: F401  populate the registry
from sentinel.agent.hooks import BudgetGuard, HookRunner, Observer, RunContext, SealGuard
from sentinel.sandbox.client_gen import code_tool_specs
from sentinel.sandbox.rpc import RpcHandler
from sentinel.tools.store import FixtureStore

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ad_high_cpu_001" / "public"


def _handler(max_tool_calls: int = 10) -> RpcHandler:
    store = FixtureStore(_FIXTURE)
    ctx = RunContext(store=store, agent_id="code", max_tool_calls=max_tool_calls)
    hooks = HookRunner([SealGuard(), BudgetGuard(), Observer()])
    allowed = {s.name for s in code_tool_specs()}
    return RpcHandler(store, hooks, ctx, allowed)


def test_dispatches_a_real_tool():
    h = _handler()
    resp = h.handle("metrics_detect_shift", {"service": "ad", "metric": "cpu_cores"})
    assert resp["ok"] is True
    assert "shift_second" in resp["result"]
    assert h.ctx.tool_calls == 1
    assert h.ctx.events[-1]["tool"] == "metrics_detect_shift"


def test_unknown_tool_is_rejected():
    h = _handler()
    resp = h.handle("metrics_nope", {})
    assert resp["ok"] is False
    assert resp["error"]["code"] == "unknown_tool"


def test_seal_guard_denies_eval_only_reference():
    h = _handler()
    resp = h.handle("changes_search", {"service": "truth.json"})
    assert resp["ok"] is False
    assert "forbidden" in resp["error"]["message"]


def test_budget_guard_caps_calls():
    h = _handler(max_tool_calls=1)
    assert h.handle("metrics_detect_shift", {"service": "ad", "metric": "cpu_cores"})["ok"] is True
    blocked = h.handle("metrics_detect_shift", {"service": "ad", "metric": "cpu_cores"})
    assert blocked["ok"] is False
    assert "budget" in blocked["error"]["message"]
