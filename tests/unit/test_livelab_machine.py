"""The live run state machine, driven end to end with fakes: no docker, no network,
no LLM, real catalog + journal + gate."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from sentinel.actions.executor import Outcome
from sentinel.api.livelab.machine import PRESETS, Deps, LiveRun

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
    def __init__(self) -> None:
        self.cpu_values = [40.0]  # recovered immediately by default

    def ingest_age_s(self) -> float | None:
        return 20.0

    def cpu_now(self, service: str) -> float | None:
        return self.cpu_values.pop(0) if len(self.cpu_values) > 1 else self.cpu_values[0]


class FakeExecutor:
    backend = "fake"

    def __init__(self, ok: bool = True) -> None:
        self.executed: list[str] = []
        self._ok = ok

    def render(self, action) -> str:
        return f"docker restart {action.target_service}"

    def execute(self, action) -> Outcome:
        self.executed.append(action.kind)
        return Outcome(ok=self._ok, before=300.0, after=40.0, duration_s=1.0)


def fake_run_rca_factory(records: int = 2, raise_error: bool = False):
    def fake_run_rca(store, *, incident, out_dir, run_id, system, onset, backend,
                     worker_concurrency, prefer_trace, trace, model=None, **kw):
        from sentinel.oss.trace import TraceContext
        ctx = TraceContext(run_id=run_id, agent_id="manager")
        trace.manager(ctx, step="topology", source="static", edges=16)
        if raise_error:
            raise RuntimeError("llm exploded")
        trace.worker(ctx.child("worker-1"), step="verdict", supported=True)
        return SimpleNamespace(
            root_cause_service="shipping",
            synthesis={"fault_type": "cpu", "justification": "cpu stepped at onset"},
            ranked_services=["shipping", "orders"],
            verdicts=[{"hypothesis": "shipping", "supported": True}],
            graph={"source": "static", "edges": [["a", "b"]]},
            usage={"input": 100, "output": 20},
        )
    return fake_run_rca


def make_deps(tmp_path: Path, *, run_rca=None, executor=None, ttl: float = 30.0):
    clk = FakeClock()
    lab = FakeLab()
    reader = FakeReader()
    injected: list[tuple[str, int]] = []
    cleared: list[str] = []
    stores: list[dict] = []

    def store_factory(start_ms: int, end_ms: int, alerts: list) -> object:
        stores.append({"start_ms": start_ms, "end_ms": end_ms, "alerts": alerts})
        return object()

    deps = Deps(
        lab=lab,
        reader=reader,
        inject_cpu=lambda target, hogs=3: injected.append((target, hogs)),
        clear_cpu=lambda target: cleared.append(target),
        store_factory=store_factory,
        run_rca=run_rca or fake_run_rca_factory(),
        executor_factory=lambda target: executor if executor is not None else FakeExecutor(),
        clock=clk.clock,
        sleep=clk.sleep,
        env=ENV,
        approval_ttl_s=ttl,
    )
    return deps, SimpleNamespace(clock=clk, lab=lab, reader=reader, injected=injected,
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


def test_happy_path_approve_execute_recover(tmp_path) -> None:
    executor = FakeExecutor()
    deps, world = make_deps(tmp_path, executor=executor)
    run = LiveRun("r1", "shipping", "quick", deps, out_root=tmp_path)
    run.start()
    wait_for_phase(run, "awaiting_approval")
    run.approve("tester")
    run.join(timeout=5)

    assert phases_of(run) == ["preflight", "booting", "baseline", "injecting", "soak",
                              "investigating", "report", "awaiting_approval",
                              "executing", "recovering", "done"]
    assert world.injected == [("shipping", 3)]
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
    assert (tmp_path / "r1" / "run.json").exists()
    snap = json.loads((tmp_path / "r1" / "run.json").read_text())
    assert snap["phase"] == "done"
    assert snap["target"] == "shipping"
    agent_frames = frames_of(run, "agent")
    assert agent_frames[0]["kind"] == "manager"


def test_window_math_uses_baseline_onset_and_lag(tmp_path) -> None:
    deps, world = make_deps(tmp_path)
    t0 = world.clock.t
    run = LiveRun("r2", "orders", "quick", deps, out_root=tmp_path)
    run.start()
    wait_for_phase(run, "awaiting_approval")
    run.deny("tester", "just checking the window")
    run.join(timeout=5)

    timings = PRESETS["quick"]
    store = world.stores[0]
    assert store["start_ms"] == int(t0 * 1000)
    # end = start + baseline + soak minus ingest lag (fake sleeps advance exactly)
    expected_end = int((t0 + timings.baseline_s + timings.soak_s - timings.lag_s) * 1000)
    assert abs(store["end_ms"] - expected_end) <= 2000
    alert = store["alerts"][0]
    assert alert.starts_at_second == timings.baseline_s


def test_deny_skips_execution_and_clears_fault(tmp_path) -> None:
    executor = FakeExecutor()
    deps, world = make_deps(tmp_path, executor=executor)
    run = LiveRun("r3", "payment", "quick", deps, out_root=tmp_path)
    run.start()
    wait_for_phase(run, "awaiting_approval")
    run.deny("tester", "not today")
    run.join(timeout=5)

    assert executor.executed == []
    assert phases_of(run)[-1] == "done"
    statuses = [a["status"] for a in frames_of(run, "action")]
    assert statuses == ["posted", "denied"]
    assert "payment" in world.cleared


def test_approval_ttl_expires_without_execution(tmp_path) -> None:
    executor = FakeExecutor()
    deps, world = make_deps(tmp_path, executor=executor, ttl=0.0)
    run = LiveRun("r4", "shipping", "quick", deps, out_root=tmp_path)
    run.start()
    run.join(timeout=5)

    assert executor.executed == []
    statuses = [a["status"] for a in frames_of(run, "action")]
    assert statuses == ["posted", "expired"]
    assert phases_of(run)[-1] == "done"


def test_abort_during_baseline_cancels_and_clears(tmp_path) -> None:
    deps, world = make_deps(tmp_path)
    started = threading.Event()
    real_sleep = deps.sleep

    def slow_sleep(s: float) -> None:
        started.set()
        time.sleep(0.02)
        real_sleep(s)

    deps.sleep = slow_sleep
    run = LiveRun("r5", "shipping", "proven", deps, out_root=tmp_path)
    run.start()
    started.wait(timeout=5)
    run.abort()
    run.join(timeout=5)

    assert phases_of(run)[-1] == "cancelled"
    assert "shipping" in world.cleared
    assert world.stores == []  # never reached investigation


def test_agent_crash_fails_run_and_clears(tmp_path) -> None:
    deps, world = make_deps(tmp_path, run_rca=fake_run_rca_factory(raise_error=True))
    run = LiveRun("r6", "shipping", "quick", deps, out_root=tmp_path)
    run.start()
    run.join(timeout=5)

    assert phases_of(run)[-1] == "failed"
    errors = frames_of(run, "error")
    assert "llm exploded" in errors[0]["message"]
    assert "shipping" in world.cleared
