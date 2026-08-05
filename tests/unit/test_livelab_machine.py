"""The live run state machine, driven end to end with fakes: no docker, no network,
no LLM, real scenarios + catalog + journal + gate."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from sentinel.actions.executor import Outcome
from sentinel.api.livelab.machine import PRESETS, Deps, LiveRun
from sentinel.api.livelab.scenarios import scenario_by_id

ENV = {"NEW_RELIC_USER_KEY": "u", "NEW_RELIC_ACCOUNT_ID": "1",
       "NEW_RELIC_LICENSE_KEY": "l", "OPEN_ROUTER_API_KEY": "o"}


class FakeClock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def clock(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class FakeLab:
    def __init__(self) -> None:
        self.up_calls = 0
        self.restarts: list[str] = []

    def daemon_up(self) -> bool:
        return True

    def up(self) -> None:
        self.up_calls += 1

    def app_services(self) -> list[dict]:
        from labs.sockshop.faults import APP_SERVICES
        return [{"name": s, "state": "running"} for s in APP_SERVICES]

    def restart(self, container: str) -> None:
        self.restarts.append(container)


class FakeReader:
    def ingest_age_s(self) -> float | None:
        return 20.0


class FakeExecutor:
    backend = "fake"

    def __init__(self, ok: bool = True, before: float = 300.0, after: float = 40.0) -> None:
        self.executed: list[str] = []
        self._ok = ok
        self._before = before
        self._after = after

    def render(self, action) -> str:
        return f"fix {action.target_service}"

    def execute(self, action) -> Outcome:
        self.executed.append(action.kind)
        return Outcome(ok=self._ok, before=self._before, after=self._after, duration_s=1.0)


def fake_run_rca_factory(root_cause: str = "shipping", raise_error: bool = False):
    calls: list[dict] = []

    def fake_run_rca(store, *, incident, out_dir, run_id, system, onset, backend,
                     worker_concurrency, prefer_trace, trace, model=None, **kw):
        from sentinel.oss.trace import TraceContext
        calls.append({"system": system, "prefer_trace": prefer_trace, "incident": incident})
        ctx = TraceContext(run_id=run_id, agent_id="manager")
        trace.manager(ctx, step="topology", source="static", edges=16)
        if raise_error:
            raise RuntimeError("llm exploded")
        trace.worker(ctx.child("worker-1"), step="verdict", supported=True)
        return SimpleNamespace(
            root_cause_service=root_cause,
            synthesis={"fault_type": "cpu", "justification": "cpu stepped at onset"},
            ranked_services=[root_cause, "orders"],
            verdicts=[{"hypothesis": root_cause, "supported": True}],
            graph={"source": "static", "edges": [["a", "b"]]},
            usage={"input": 100, "output": 20},
        )

    fake_run_rca.calls = calls
    return fake_run_rca


def make_deps(tmp_path: Path, scenario, *, run_rca=None, executor=None,
              ttl: float = 30.0, health_values: list[float] | None = None):
    clk = FakeClock()
    lab = FakeLab()
    injected: list[str] = []
    cleared: list[str] = []
    stores: list[dict] = []
    health = list(health_values or [40.0])

    def store_factory(start_ms: int, end_ms: int, alerts: list) -> object:
        stores.append({"start_ms": start_ms, "end_ms": end_ms, "alerts": alerts})
        return object()

    deps = Deps(
        lab=lab,
        reader=FakeReader(),
        inject=lambda: injected.append(scenario.truth_service),
        clear=lambda: cleared.append(scenario.truth_service),
        health_now=lambda: health.pop(0) if len(health) > 1 else health[0],
        store_factory=store_factory,
        run_rca=run_rca or fake_run_rca_factory(scenario.truth_service),
        executor_factory=lambda s: executor if executor is not None else FakeExecutor(),
        clock=clk.clock,
        sleep=clk.sleep,
        env=ENV,
        approval_ttl_s=ttl,
    )
    return deps, SimpleNamespace(clock=clk, lab=lab, injected=injected,
                                 cleared=cleared, stores=stores)


def phases_of(run: LiveRun) -> list[str]:
    return [f["data"]["phase"] for f in run.bus.backlog() if f["event"] == "phase"]


def frames_of(run: LiveRun, event: str) -> list[dict]:
    return [f["data"] for f in run.bus.backlog() if f["event"] == event]


def wait_for_phase(run: LiveRun, phase: str, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if phase in phases_of(run):
            return
        time.sleep(0.01)
    raise AssertionError(f"phase {phase} never reached; saw {phases_of(run)}")


SHIPPING = scenario_by_id("sockshop-cpu-shipping")
ORDERS = scenario_by_id("sockshop-cpu-orders")
PAYMENT = scenario_by_id("sockshop-cpu-payment")
OTEL_AD = scenario_by_id("otel-ad_high_cpu_live_001")


def test_happy_path_approve_execute_recover(tmp_path) -> None:
    executor = FakeExecutor()
    deps, world = make_deps(tmp_path, SHIPPING, executor=executor)
    run = LiveRun("r1", SHIPPING, "quick", deps, out_root=tmp_path)
    run.start()
    wait_for_phase(run, "awaiting_approval")
    run.approve("tester")
    run.join(timeout=5)

    assert phases_of(run) == ["preflight", "booting", "baseline", "injecting", "soak",
                              "investigating", "report", "awaiting_approval",
                              "executing", "recovering", "done"]
    assert world.injected == ["shipping"]
    assert "shipping" in world.cleared
    report = frames_of(run, "report")[0]
    assert report["root_cause_service"] == "shipping"
    assert report["hit"] is True
    action_frames = frames_of(run, "action")
    assert [a["status"] for a in action_frames] == ["posted", "approved", "execute_result"]
    assert action_frames[0]["action"]["kind"] == "restart"
    assert executor.executed == ["restart"]
    recovery = frames_of(run, "recovery")[-1]
    assert recovery["recovered"] is True
    snap = json.loads((tmp_path / "r1" / "run.json").read_text())
    assert snap["phase"] == "done"
    assert snap["target"] == "shipping"
    assert snap["scenario"]["lab"] == "sock_shop"
    assert frames_of(run, "agent")[0]["kind"] == "manager"
    # the agent ran with the scenario's system + trace preference
    assert deps.run_rca.calls[0]["system"] == "sock_shop"
    assert deps.run_rca.calls[0]["prefer_trace"] is False


def test_otel_scenario_elects_flag_reset_and_relative_recovery(tmp_path) -> None:
    # executor settle leaves badness barely moved; the recovering loop's later
    # health polls confirm via the relative rule (no absolute cpu scale pinned)
    executor = FakeExecutor(before=1.8, after=1.5)
    deps, world = make_deps(tmp_path, OTEL_AD, executor=executor,
                            run_rca=fake_run_rca_factory("ad"),
                            health_values=[1.4, 0.2, 0.2])
    run = LiveRun("r-ad", OTEL_AD, "quick", deps, out_root=tmp_path)
    run.start()
    wait_for_phase(run, "awaiting_approval")
    run.approve("tester")
    run.join(timeout=5)

    action_frames = frames_of(run, "action")
    assert action_frames[0]["action"]["kind"] == "remove_impairment"
    assert executor.executed == ["remove_impairment"]
    recovery = frames_of(run, "recovery")
    assert recovery[0]["recovered"] is False          # 1.5 is not < 1.8/3
    assert recovery[-1]["recovered"] is True          # 0.2 < 1.8/3
    assert frames_of(run, "report")[0]["hit"] is True
    assert deps.run_rca.calls[0]["system"] == "online_boutique"
    assert deps.run_rca.calls[0]["prefer_trace"] is True
    assert "ad" in world.injected and "ad" in world.cleared


def test_window_math_uses_baseline_onset_and_lag(tmp_path) -> None:
    deps, world = make_deps(tmp_path, ORDERS, run_rca=fake_run_rca_factory("orders"))
    t0 = world.clock.t
    run = LiveRun("r2", ORDERS, "quick", deps, out_root=tmp_path)
    run.start()
    wait_for_phase(run, "awaiting_approval")
    run.deny("tester", "just checking the window")
    run.join(timeout=5)

    timings = PRESETS["quick"]
    store = world.stores[0]
    assert store["start_ms"] == int(t0 * 1000)
    expected_end = int((t0 + timings.baseline_s + timings.soak_s - timings.lag_s) * 1000)
    assert abs(store["end_ms"] - expected_end) <= 2000
    alert = store["alerts"][0]
    assert alert.starts_at_second == timings.baseline_s
    assert alert.fingerprint == "live-sockshop-cpu-orders"


def test_deny_skips_execution_and_clears_fault(tmp_path) -> None:
    executor = FakeExecutor()
    deps, world = make_deps(tmp_path, PAYMENT, executor=executor,
                            run_rca=fake_run_rca_factory("payment"))
    run = LiveRun("r3", PAYMENT, "quick", deps, out_root=tmp_path)
    run.start()
    wait_for_phase(run, "awaiting_approval")
    run.deny("tester", "not today")
    run.join(timeout=5)

    assert executor.executed == []
    assert phases_of(run)[-1] == "done"
    assert [a["status"] for a in frames_of(run, "action")] == ["posted", "denied"]
    assert "payment" in world.cleared


def test_approval_ttl_expires_without_execution(tmp_path) -> None:
    executor = FakeExecutor()
    deps, world = make_deps(tmp_path, SHIPPING, executor=executor, ttl=0.0)
    run = LiveRun("r4", SHIPPING, "quick", deps, out_root=tmp_path)
    run.start()
    run.join(timeout=5)

    assert executor.executed == []
    assert [a["status"] for a in frames_of(run, "action")] == ["posted", "expired"]
    assert phases_of(run)[-1] == "done"


def test_abort_during_baseline_cancels_and_clears(tmp_path) -> None:
    deps, world = make_deps(tmp_path, SHIPPING)
    started = threading.Event()
    real_sleep = deps.sleep

    def slow_sleep(s: float) -> None:
        started.set()
        time.sleep(0.02)
        real_sleep(s)

    deps.sleep = slow_sleep
    run = LiveRun("r5", SHIPPING, "proven", deps, out_root=tmp_path)
    run.start()
    started.wait(timeout=5)
    run.abort()
    run.join(timeout=5)

    assert phases_of(run)[-1] == "cancelled"
    assert "shipping" in world.cleared
    assert world.stores == []


def test_agent_crash_fails_run_and_clears(tmp_path) -> None:
    deps, world = make_deps(tmp_path, SHIPPING,
                            run_rca=fake_run_rca_factory(raise_error=True))
    run = LiveRun("r6", SHIPPING, "quick", deps, out_root=tmp_path)
    run.start()
    run.join(timeout=5)

    assert phases_of(run)[-1] == "failed"
    assert "llm exploded" in frames_of(run, "error")[0]["message"]
    assert "shipping" in world.cleared
