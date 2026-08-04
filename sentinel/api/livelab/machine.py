"""The live run state machine: one thread walks a real incident end to end and
narrates every step onto the EventBus.

    preflight -> booting -> baseline -> injecting -> soak -> investigating
    -> report -> awaiting_approval -> executing -> recovering -> done
    (any point: failed / cancelled)

Every side effect is injected through Deps so the whole machine runs under test
with fakes; the router builds the real Deps from the environment. The remediation
half reuses the existing action journal + gate untouched: the machine proposes via
the catalog, journals, waits for a human decision from the dashboard, and only
gate.execute_approved can run the op (single-use, hash-bound, TTL enforced there).

Fault hygiene is unconditional: the injected CPU hogs are ours, so they are swept
in a finally regardless of approval outcome, crash, or abort.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sentinel.actions.catalog import elect_primary, suggest_actions
from sentinel.actions.gate import execute_approved
from sentinel.actions.journal import ActionJournal
from sentinel.api.livelab.bus import BroadcastTraceLogger, EventBus
from sentinel.api.livelab.preflight import run_preflight
from sentinel.fixtures.schemas import DerivedAlert

SYMPTOM = ("Customers report the Sock Shop storefront is sluggish: browsing the "
           "product catalogue and loading pages feels slow. Overall the system is degraded.")

_RECOVERED_CPU_PCT = 50.0
_RECOVERY_POLLS = 12
_RECOVERY_POLL_S = 15.0


@dataclass(frozen=True)
class Timings:
    baseline_s: int
    soak_s: int
    lag_s: int


PRESETS: dict[str, Timings] = {
    "proven": Timings(180, 240, 60),   # the validated run_sockshop_live protocol
    "quick": Timings(120, 180, 60),    # rehearsal preset; weaker detection margins
}


@dataclass
class Deps:
    lab: Any
    reader: Any
    inject_cpu: Callable[..., None]
    clear_cpu: Callable[[str], None]
    store_factory: Callable[[int, int, list], Any]
    run_rca: Callable[..., Any]
    executor_factory: Callable[[str], Any]
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    env: Any = field(default_factory=lambda: os.environ)
    approval_ttl_s: float = 600.0
    hogs: int = 3


class _Aborted(Exception):
    pass


class LiveRun:
    mode = "live"

    def __init__(self, run_id: str, target: str, preset: str, deps: Deps, *,
                 out_root: Path) -> None:
        self.run_id = run_id
        self.target = target
        self.preset = preset
        self.timings = PRESETS[preset]
        self._deps = deps
        self._dir = out_root / run_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self.bus = EventBus(journal_dir=self._dir)
        self._thread = threading.Thread(target=self._run, name=f"liverun-{run_id}", daemon=True)
        self._abort = threading.Event()
        self._decision_ready = threading.Event()
        self._decision: tuple[str, str, str] | None = None  # (verb, approver, reason)
        self._phase = "idle"
        self._phases: list[dict] = []
        self._report: dict | None = None
        self._action: dict | None = None
        self._recovery: dict | None = None
        self._started_ms = int(deps.clock() * 1000)

    # -- control surface (router) ---------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    @property
    def is_active(self) -> bool:
        return self._thread.is_alive()

    def abort(self) -> None:
        self._abort.set()
        self._decision_ready.set()

    def approve(self, approver: str) -> bool:
        if self._phase != "awaiting_approval":
            return False
        self._decision = ("approved", approver, "")
        self._decision_ready.set()
        return True

    def deny(self, approver: str, reason: str = "") -> bool:
        if self._phase != "awaiting_approval":
            return False
        self._decision = ("denied", approver, reason)
        self._decision_ready.set()
        return True

    def snapshot(self) -> dict:
        return {
            "run_id": self.run_id, "mode": self.mode, "target": self.target,
            "preset": self.preset,
            "timings": {"baseline_s": self.timings.baseline_s,
                        "soak_s": self.timings.soak_s, "lag_s": self.timings.lag_s},
            "started_ms": self._started_ms, "phase": self._phase,
            "phases": self._phases, "report": self._report, "action": self._action,
            "recovery": self._recovery, "last_seq": self.bus.last_seq,
        }

    # -- internals -------------------------------------------------------------

    def _emit_phase(self, phase: str, **detail: Any) -> None:
        self._phase = phase
        entry = {"phase": phase, "at_ms": int(self._deps.clock() * 1000), **detail}
        self._phases.append(entry)
        self.bus.emit("phase", {"run_id": self.run_id, "target": self.target,
                                "timings": self.snapshot()["timings"], **entry})
        self._write_snapshot()

    def _write_snapshot(self) -> None:
        (self._dir / "run.json").write_text(json.dumps(self.snapshot(), default=str))

    def _emit_lab(self) -> None:
        try:
            services = self._deps.lab.app_services()
            age = self._deps.reader.ingest_age_s()
        except Exception:
            return
        self.bus.emit("lab", {"services": services, "ingest_age_s": age})

    def _check_abort(self) -> None:
        if self._abort.is_set():
            raise _Aborted()

    def _wait(self, seconds: float) -> None:
        """Interruptible wait that keeps the lab panel alive while nothing else
        is happening (a lab frame roughly every 15s)."""
        deadline = self._deps.clock() + seconds
        while True:
            self._check_abort()
            remaining = deadline - self._deps.clock()
            if remaining <= 0:
                return
            self._deps.sleep(min(15.0, remaining))
            self._emit_lab()

    def _run(self) -> None:
        deps = self._deps
        try:
            self._preflight()
            self._boot()
            deps.clear_cpu(self.target)  # sweep residue so the baseline is clean
            t0_ms = int(deps.clock() * 1000)
            self._emit_phase("baseline", detail=f"{self.timings.baseline_s}s clean window")
            self._wait(self.timings.baseline_s)

            self._emit_phase("injecting",
                             detail=f"{deps.hogs} CPU hogs into {self.target}")
            deps.inject_cpu(self.target, hogs=deps.hogs)
            self._inject_at_ms = int(deps.clock() * 1000)
            self._emit_lab()

            self._emit_phase("soak", detail=f"{self.timings.soak_s}s under fault")
            self._wait(self.timings.soak_s)

            result = self._investigate(t0_ms)
            action = self._propose(result)
            self._decide_and_execute(action)
            self._emit_phase("done")
        except _Aborted:
            self._emit_phase("cancelled", detail="aborted by operator")
        except Exception as exc:  # noqa: BLE001 - a failed run must end as a failed run
            self.bus.emit("error", {"message": f"{type(exc).__name__}: {exc}"})
            self._emit_phase("failed", detail=str(exc))
        finally:
            try:
                deps.clear_cpu(self.target)
            except Exception:
                pass
            self._write_snapshot()
            self.bus.close()

    def _preflight(self) -> None:
        self._emit_phase("preflight")
        checks = run_preflight(lab=self._deps.lab, reader=None, env=self._deps.env)
        blocking = [c for c in checks if not c.ok and c.name != "lab"]
        if blocking:
            raise RuntimeError("; ".join(f"{c.name}: {c.detail}" for c in blocking))

    def _boot(self) -> None:
        deps = self._deps
        self._emit_phase("booting")
        if any(s["state"] != "running" for s in deps.lab.app_services()):
            deps.lab.up()
            for _ in range(36):
                self._check_abort()
                if all(s["state"] == "running" for s in deps.lab.app_services()):
                    break
                deps.sleep(5.0)
            else:
                raise RuntimeError("lab did not reach running state within 180s")
        for _ in range(36):
            self._check_abort()
            age = deps.reader.ingest_age_s()
            if age is not None and age <= 120.0:
                break
            deps.sleep(5.0)
        else:
            raise RuntimeError("New Relic ingest never became fresh (no recent Metric data)")
        self._emit_lab()

    def _investigate(self, t0_ms: int):
        deps = self._deps
        onset = self.timings.baseline_s
        window_end_ms = int(deps.clock() * 1000) - self.timings.lag_s * 1000
        window_s = (window_end_ms - t0_ms) // 1000
        self._window = {"start_ms": t0_ms, "end_ms": window_end_ms, "onset_s": onset}
        self._emit_phase("investigating", window=self._window)

        alert = DerivedAlert(alertname="ServiceDegraded", severity="warning",
                             starts_at_second=onset, labels={"tier": "user_facing"},
                             annotations={"summary": SYMPTOM}, value=1.0, expr="live",
                             fingerprint=f"live-sockshop-{self.target}-cpu")
        store = deps.store_factory(t0_ms, window_end_ms, [alert])
        incident = (
            f"Symptom: {SYMPTOM}\n"
            f"Recording window: seconds 0 to {window_s}; the incident onset is around second {onset}.\n"
            "This is a LIVE microservices system (many services). Investigate the telemetry and "
            "determine which single service is the root cause and the failure type."
        )
        trace = BroadcastTraceLogger(self._dir / "agent.jsonl", self.bus)
        result = deps.run_rca(store, incident=incident, out_dir=str(self._dir),
                              run_id=self.run_id, system="sock_shop", onset=onset,
                              backend="local", worker_concurrency=2, prefer_trace=False,
                              trace=trace)
        synthesis = result.synthesis or {}
        self._report = {
            "root_cause_service": result.root_cause_service,
            "fault_type": synthesis.get("fault_type"),
            "justification": synthesis.get("justification", ""),
            "ranked_services": result.ranked_services,
            "verdicts": result.verdicts,
            "graph_source": result.graph.get("source"),
            "graph_edges": len(result.graph.get("edges", [])),
            "usage": result.usage,
            "hit": result.root_cause_service == self.target,
        }
        self._emit_phase("report")
        self.bus.emit("report", self._report)
        return result

    def _propose(self, result):
        synthesis = result.synthesis or {}
        justification = synthesis.get("justification", "")
        executor = self._deps.executor_factory(self.target)
        actions = suggest_actions(
            target_service=result.root_cause_service or self.target,
            fault_type=synthesis.get("fault_type"),
            signature="resource",
            citations=[justification] if justification else [],
            confident=True,
            executor=executor,
        )
        primary = elect_primary(actions)
        if primary is None:
            raise RuntimeError("catalog produced no mutate action for a cpu fault")
        self._journal = ActionJournal(self._dir / "actions.jsonl", now=self._deps.clock)
        expires = self._deps.clock() + self._deps.approval_ttl_s
        self._journal.proposed(primary, expires_at=expires)
        self._journal.posted(primary.id, "dashboard", self.run_id)
        self._action = {"action": primary.model_dump(), "status": "posted"}
        self.bus.emit("action", self._action)
        self._executor = executor
        return primary

    def _decide_and_execute(self, action) -> None:
        deps = self._deps
        self._emit_phase("awaiting_approval",
                         detail=f"TTL {int(deps.approval_ttl_s)}s")
        deadline = deps.clock() + deps.approval_ttl_s
        while not self._decision_ready.is_set():
            if deps.clock() >= deadline:
                break
            self._decision_ready.wait(0.25)
        self._check_abort()

        if self._decision is None:  # TTL ran out with no human decision
            self._journal.expired(action.id)
            self._action = {"action": action.model_dump(), "status": "expired"}
            self.bus.emit("action", self._action)
            return

        verb, approver, reason = self._decision
        if verb == "denied":
            self._journal.denied(action.id, approver, reason)
            self._action = {"action": action.model_dump(), "status": "denied",
                            "approver": approver}
            self.bus.emit("action", self._action)
            return

        self._journal.approved(action.id, approver, "dashboard")
        self._action = {"action": action.model_dump(), "status": "approved",
                        "approver": approver}
        self.bus.emit("action", self._action)

        self._emit_phase("executing", detail=self._executor.render(action))
        gate = execute_approved(self._journal, action, self._executor, now=deps.clock())
        state = self._journal.state_of(action.id)
        outcome = (state.outcome if state is not None else None) or {}
        before, after = outcome.get("before"), outcome.get("after")
        self._action = {"action": action.model_dump(), "status": "execute_result",
                        "ok": gate.executed, "outcome": outcome}
        self.bus.emit("action", self._action)
        recovered = before is not None and after is not None and after < before * 0.5
        self._recovery = {"before": before, "after": after, "recovered": recovered}
        self.bus.emit("recovery", self._recovery)

        self._emit_phase("recovering")
        for _ in range(_RECOVERY_POLLS):
            if self._recovery["recovered"]:
                break
            self._check_abort()
            deps.sleep(_RECOVERY_POLL_S)
            cpu = deps.reader.cpu_now(self.target)
            self._recovery = {"before": before, "after": cpu,
                              "recovered": cpu is not None and cpu < _RECOVERED_CPU_PCT}
            self.bus.emit("recovery", self._recovery)
