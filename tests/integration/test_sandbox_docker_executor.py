"""DockerExecutor runs the sandbox kernel in an isolated container over stdio.

The Docker backend talks to the host over the container's stdin/stdout pipes
(`docker run -i`) rather than a unix socket, because a host AF_UNIX socket
bind-mounted into a Linux container cannot be connected to on macOS Docker
Desktop (separate kernels). The pipe transport works on macOS and Linux while
keeping `--network none` (zero networking).

Skips when docker is unavailable.
"""

import shutil
from pathlib import Path

import pytest

import sentinel.tools  # noqa: F401  populate the registry
from sentinel.agent.hooks import BudgetGuard, HookRunner, Observer, RunContext, SealGuard
from sentinel.sandbox.client_gen import code_tool_specs, generate_client_source
from sentinel.sandbox.executor import DockerExecutor
from sentinel.sandbox.rpc import RpcHandler
from sentinel.tools.store import FixtureStore

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ad_high_cpu_001" / "public"

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")


def _executor(timeout_s: float = 60.0) -> DockerExecutor:
    store = FixtureStore(_FIXTURE)
    ctx = RunContext(store=store, agent_id="code", max_tool_calls=50)
    hooks = HookRunner([SealGuard(), BudgetGuard(), Observer()])
    handler = RpcHandler(store, hooks, ctx, {s.name for s in code_tool_specs()})
    return DockerExecutor(handler, generate_client_source(code_tool_specs()), timeout_s=timeout_s)


def test_docker_executor_runs_isolated():
    """One container, three runs against the persistent kernel: a real proxied
    tool call, error isolation, and no network egress."""
    ex = _executor()
    ex.start()
    try:
        # real proxied tool call: the value 420 only appears if the sandboxed
        # metrics.detect_shift RPC'd through the inner plane into REGISTRY.dispatch
        r1 = ex.run('print(metrics.detect_shift("ad", "cpu_cores")["shift_second"])')
        assert r1.error is None
        assert r1.stdout.strip() == "420"
        assert ex.handler.ctx.events[-1]["tool"] == "metrics_detect_shift"

        # a script exception is returned, not raised, and does not crash the kernel
        r2 = ex.run("raise ValueError('boom')")
        assert r2.error is not None
        assert "ValueError: boom" in r2.error

        # --network none: the kernel survived r2, and has no internet
        r3 = ex.run("import urllib.request; urllib.request.urlopen('http://example.com', timeout=5)")
        assert r3.error is not None
    finally:
        ex.close()
