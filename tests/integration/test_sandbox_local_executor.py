from pathlib import Path

import sentinel.tools  # noqa: F401  populate the registry
from sentinel.agent.hooks import BudgetGuard, HookRunner, Observer, RunContext, SealGuard
from sentinel.sandbox.client_gen import code_tool_specs, generate_client_source
from sentinel.sandbox.executor import LocalExecutor
from sentinel.sandbox.rpc import RpcHandler
from sentinel.tools.store import FixtureStore

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ad_high_cpu_001" / "public"


def _executor() -> LocalExecutor:
    store = FixtureStore(_FIXTURE)
    ctx = RunContext(store=store, agent_id="code", max_tool_calls=50)
    hooks = HookRunner([SealGuard(), BudgetGuard(), Observer()])
    handler = RpcHandler(store, hooks, ctx, {s.name for s in code_tool_specs()})
    return LocalExecutor(handler, generate_client_source(code_tool_specs()), timeout_s=15.0)


def test_runs_code_and_proxies_a_tool_call():
    ex = _executor()
    ex.start()
    try:
        res = ex.run('s = metrics.detect_shift("ad", "cpu_cores"); print(s["shift_second"])')
        assert res.error is None
        assert res.stdout.strip() == "420"
        assert ex.handler.ctx.events[-1]["tool"] == "metrics_detect_shift"
    finally:
        ex.close()


def test_kernel_state_persists_across_runs():
    ex = _executor()
    ex.start()
    try:
        ex.run("x = 41")
        res = ex.run("print(x + 1)")
        assert res.stdout.strip() == "42"
    finally:
        ex.close()


def test_script_error_is_returned_not_raised():
    ex = _executor()
    ex.start()
    try:
        res = ex.run("raise ValueError('boom')")
        assert res.error is not None
        assert "ValueError: boom" in res.error
    finally:
        ex.close()
