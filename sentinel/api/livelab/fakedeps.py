"""Scripted stand-in dependencies for rehearsing the dashboard without docker,
New Relic, or an LLM: SENTINEL_LIVELAB_FAKE=1 on the server wires these in.

A shared virtual clock runs SENTINEL_LIVELAB_FAKE_SPEED x faster than the wall
(default 20), and every dependency (machine clock, sleeps, synthesized telemetry,
recovery reads) lives in that one clock domain, so the whole state machine plays
out in seconds while timestamps stay mutually consistent. One rehearsal artifact:
the UI's phase countdowns tick in wall time, so they lag the accelerated phases.

Telemetry is synthesized: 13 services idle around 5-25% CPU, the injected target
ramps toward saturation until the restart clears it, so charts, topology heat,
and the recovery check all behave like the real lab.
"""
from __future__ import annotations

import math
import os
import random
import time
from pathlib import Path
from types import SimpleNamespace

from labs.sockshop.faults import APP_SERVICES
from sentinel.actions.executor import Outcome
from sentinel.api.livelab.machine import Deps
from sentinel.oss.trace import TraceContext


class VirtualClock:
    """Wall-anchored clock running `speed` times faster than real time."""

    def __init__(self, speed: float) -> None:
        self._t0 = time.time()
        self._speed = speed

    def now(self) -> float:
        return self._t0 + (time.time() - self._t0) * self._speed

    def sleep(self, s: float) -> None:
        time.sleep(s / self._speed)


class FakeLab:
    def daemon_up(self) -> bool:
        return True

    def up(self) -> None:
        pass

    def app_services(self) -> list[dict]:
        return [{"name": s, "state": "running"} for s in APP_SERVICES]

    def restart(self, container: str) -> None:
        pass


class FakeWorld:
    """The synthesized fault, in virtual-clock time."""

    def __init__(self, clock: VirtualClock) -> None:
        self.clock = clock
        self.inject_at: float | None = None
        self.cleared_at: float | None = None

    def cpu_of(self, service: str, target: str, at_s: float) -> float:
        base = 8.0 + (hash(service) % 12) + 6.0 * math.sin(at_s / 37.0 + hash(service) % 7)
        if service != target or self.inject_at is None or at_s < self.inject_at:
            return max(1.0, base)
        if self.cleared_at is not None and at_s >= self.cleared_at:
            return max(1.0, base)
        ramp = min(1.0, (at_s - self.inject_at) / 90.0)
        return max(1.0, base + 280.0 * ramp + random.uniform(-8, 8))


class FakeReader:
    def __init__(self, world: FakeWorld, target_ref: dict, journal_dir: Path | None) -> None:
        self._world = world
        self._target_ref = target_ref
        self._journal_dir = journal_dir

    def ingest_age_s(self) -> float | None:
        return 15.0

    def cpu_now(self, service: str) -> float | None:
        return self._world.cpu_of(service, self._target_ref.get("target", ""),
                                  self._world.clock.now())

    def series(self, services: list[str], since_ms: int, until_ms: int) -> dict:
        import json

        target = self._target_ref.get("target", "")
        out: dict = {"series": {"cpu": {}, "mem": {}},
                     "fetched_at_ms": int(self._world.clock.now() * 1000)}
        for svc in services:
            cpu, mem = [], []
            t = since_ms
            while t <= until_ms:
                cpu.append([t, round(self._world.cpu_of(svc, target, t / 1000.0), 1)])
                mem.append([t, round(22.0 + (hash(svc) % 30) + 3 * math.sin(t / 90000.0), 1)])
                t += 15000
            out["series"]["cpu"][svc] = cpu
            out["series"]["mem"][svc] = mem
        if self._journal_dir is not None:
            self._journal_dir.mkdir(parents=True, exist_ok=True)
            with (self._journal_dir / "telemetry.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(out) + "\n")
        return out


_WORKER_CODE = [
    'rows = client.metrics_series(service="{svc}", metric="container.cpu.utilization")\n'
    "pre = [v for t, v in rows if t < onset_ms]\npost = [v for t, v in rows if t >= onset_ms]\n"
    "print(median(pre), median(post))",
    "z = robust_z(post, pre)\nprint({'effect_z': round(z, 1), 'family': 'resource'})",
]


def _fake_run_rca(clock: VirtualClock) -> callable:
    def run_rca(store, *, incident, out_dir, run_id, system, onset, backend,
                worker_concurrency, prefer_trace, trace, model=None, **kw):
        target = getattr(store, "fake_target", "shipping")
        root = TraceContext(run_id=run_id, agent_id="manager")
        ranked = [target] + [s for s in ("user-db", "catalogue-db", "orders") if s != target]
        trace.manager(root, step="topology", source="static", edges=16,
                      ranked=ranked[:4], traces_present=False)
        clock.sleep(20)
        trace.manager(root, step="plan",
                      reasoning="The symptom is whole-system sluggishness with no error spike; "
                                "the anomaly overlay puts one service far above its baseline. "
                                "Testing the resource signature on the top candidates.",
                      hypotheses=[
                          {"candidate_service": target, "signature": "resource",
                           "investigation_directive": "Compare CPU pre/post onset; confirm a sustained step."},
                          {"candidate_service": ranked[1], "signature": "latency",
                           "investigation_directive": "Rule out an inherited slowdown from the fault path."},
                      ])
        for i, svc in enumerate([target, ranked[1]]):
            ctx = root.child(f"worker-{svc}")
            trace.worker(ctx, hypothesis={"candidate_service": svc,
                                          "signature": "resource" if i == 0 else "latency"},
                         tool_subset=["metrics_series", "metrics_compare_baseline", "logs_search"])
            for j, code in enumerate(_WORKER_CODE):
                clock.sleep(25)
                trace.code_iter(ctx, j, code=code.replace("{svc}", svc),
                                stdout="(11.2, 291.4)" if (i == 0 and j == 0)
                                else "{'effect_z': 14.1, 'family': 'resource'}" if i == 0
                                else "(48.1, 52.3)",
                                traceback=None,
                                reasoning=f"Checking whether {svc}'s own signal stepped at onset."
                                if j == 0 else "The step is large; quantifying the effect size.")
            trace.log(ctx, "verdict", iters=2, verdict={
                "hypothesis": svc, "supported": i == 0,
                "root_cause_service": svc if i == 0 else None,
                "signature": "resource" if i == 0 else None,
                "confidence": 0.94 if i == 0 else 0.2,
                "evidence": [f"{svc} CPU median 11% -> 291% at onset (effect_z 14.1)"] if i == 0
                else [f"{svc} p95 flat across onset; inherited wait only"],
            })
        clock.sleep(15)
        trace.manager(root, step="synthesize",
                      reasoning="One worker confirmed a saturated resource signature at the onset "
                                "second; the other candidate shows only inherited latency.",
                      result={"ranked_services": ranked[:3], "root_cause_service": target,
                              "fault_type": "cpu"}, usage={"input": 31200, "output": 4100})
        trace.manager(root, step="final_answer", root_cause_service=target)
        return SimpleNamespace(
            root_cause_service=target,
            synthesis={"fault_type": "cpu",
                       "justification": f"{target} CPU stepped from ~11% to ~290% at the onset "
                                        "second and stayed saturated; callers only inherit wait."},
            ranked_services=ranked[:3],
            verdicts=[{"hypothesis": target, "supported": True, "confidence": 0.94}],
            graph={"source": "static", "edges": [[a, b] for a, b in [("front-end", target)]]},
            usage={"input": 31200, "output": 4100},
        )

    return run_rca


def fake_deps_factory(run_dir: Path | None, scenario=None) -> Deps:
    speed = float(os.environ.get("SENTINEL_LIVELAB_FAKE_SPEED", "20"))
    clock = VirtualClock(speed)
    world = FakeWorld(clock)
    truth = scenario.truth_service if scenario is not None else "shipping"
    target_ref: dict = {}
    reader = FakeReader(world, target_ref, run_dir)

    def inject() -> None:
        target_ref["target"] = truth
        world.inject_at = clock.now()
        world.cleared_at = None

    def clear() -> None:
        if world.cleared_at is None:
            world.cleared_at = clock.now()

    def store_factory(start_ms: int, end_ms: int, alerts: list):
        return SimpleNamespace(fake_target=target_ref.get("target", truth))

    class FakeExecutor:
        backend = "fake-live"

        def render(self, action) -> str:
            return f"docker restart {action.target_service}"

        def execute(self, action) -> Outcome:
            before = reader.cpu_now(action.target_service)
            clear()
            clock.sleep(60)
            return Outcome(ok=True, before=before,
                           after=reader.cpu_now(action.target_service), duration_s=3.0)

    return Deps(lab=FakeLab(), reader=reader, inject=inject, clear=clear,
                health_now=lambda: reader.cpu_now(truth),
                store_factory=store_factory, run_rca=_fake_run_rca(clock),
                executor_factory=lambda scn: FakeExecutor(),
                clock=clock.now, sleep=clock.sleep,
                approval_ttl_s=600.0 * speed,  # ~10 real minutes to click Approve
                env={"NEW_RELIC_USER_KEY": "fake", "NEW_RELIC_ACCOUNT_ID": "0",
                     "NEW_RELIC_LICENSE_KEY": "fake", "OPEN_ROUTER_API_KEY": "fake"})
