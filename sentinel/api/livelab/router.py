"""The /live surface: run control, snapshots, one multiplexed SSE stream per run,
chart telemetry, topology, and lab hygiene endpoints.

One run (live or replay) is in flight at a time. Finished live runs remain
addressable forever: their snapshot, stream, and telemetry are served from the
journals on disk, which is also exactly what replay mode re-streams.
"""
from __future__ import annotations

import asyncio
import json
import queue
import time
from pathlib import Path
from threading import Lock
from typing import Callable, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from labs.sockshop.faults import APP_SERVICES, VETTED_TARGETS
from sentinel.api.livelab.machine import PRESETS, Deps, LiveRun
from sentinel.api.livelab.preflight import run_preflight
from sentinel.api.livelab.replay import ReplayRun, list_replays, start_replay
from sentinel.api.livelab.telemetry import ReplayTelemetryReader

_TOPOLOGY_PATH = Path("rcaeval/topology/sock_shop.json")

Target = Literal["shipping", "catalogue", "payment", "orders"]


class StartRunBody(BaseModel):
    target: Target
    preset: Literal["proven", "quick"] = "proven"


class DecisionBody(BaseModel):
    approver: str = "dashboard"
    reason: str = ""


class ClearFaultBody(BaseModel):
    target: Target


def default_deps_factory(run_dir: Path | None) -> Deps:
    """The real dependencies: docker, New Relic, the gpt-oss agent, LiveExecutor.
    Imported lazily so the module (and its tests) load without any of them."""
    import os

    from labs.sockshop.faults import clear_cpu, inject_cpu
    from sentinel.actions.live_executor import make_live_executor
    from sentinel.api.livelab.lab import Lab
    from sentinel.api.livelab.telemetry import TelemetryReader
    from sentinel.newrelic.client import NerdGraphClient
    from sentinel.newrelic.store import NewRelicStore
    from sentinel.oss.rca import run_rca

    client = NerdGraphClient.from_env()
    lab = Lab()
    reader = TelemetryReader(client.nrql, journal_dir=run_dir)

    def store_factory(start_ms: int, end_ms: int, alerts: list):
        return NewRelicStore(client, window_start_ms=start_ms, window_end_ms=end_ms,
                             alerts=alerts)

    def executor_factory(target: str):
        return make_live_executor(
            health=lambda svc: reader.cpu_now(svc),
            restart=lambda container: lab.restart(container),
            settle_s=60.0,
        )

    return Deps(lab=lab, reader=reader, inject_cpu=inject_cpu, clear_cpu=clear_cpu,
                store_factory=store_factory, run_rca=run_rca,
                executor_factory=executor_factory,
                approval_ttl_s=float(os.environ.get("ACTION_APPROVAL_TTL_S", "600")))


class Registry:
    """All runs this process has started, plus the single-flight lock."""

    def __init__(self, out_root: Path, deps_factory: Callable[[Path | None], Deps]) -> None:
        self.out_root = out_root
        self.deps_factory = deps_factory
        self._runs: dict[str, LiveRun | ReplayRun] = {}
        self._lock = Lock()
        self._shared_deps: Deps | None = None

    @property
    def shared(self) -> Deps:
        if self._shared_deps is None:
            self._shared_deps = self.deps_factory(None)
        return self._shared_deps

    def active(self) -> LiveRun | ReplayRun | None:
        return next((r for r in self._runs.values() if r.is_active), None)

    def get(self, run_id: str) -> LiveRun | ReplayRun | None:
        return self._runs.get(run_id)

    def start_live(self, target: str, preset: str) -> LiveRun:
        with self._lock:
            if self.active() is not None:
                raise HTTPException(status_code=409, detail="a run is already in flight")
            run_id = f"live-{target}-{int(time.time() * 1000)}"
            deps = self.deps_factory(self.out_root / run_id)
            run = LiveRun(run_id, target, preset, deps, out_root=self.out_root)
            self._runs[run_id] = run
            run.start()
            return run

    def start_replay(self, source_run_id: str) -> ReplayRun:
        with self._lock:
            if self.active() is not None:
                raise HTTPException(status_code=409, detail="a run is already in flight")
            try:
                run = start_replay(source_run_id, self.out_root)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            self._runs[run.run_id] = run
            run.start()
            return run


def _sse(frame: dict) -> str:
    return f"id: {frame['seq']}\nevent: {frame['event']}\ndata: {json.dumps(frame['data'], default=str)}\n\n"


def make_livelab_router(*, out_root: Path = Path("runs/dashboard"),
                        deps_factory: Callable[[Path | None], Deps] = default_deps_factory,
                        ) -> APIRouter:
    router = APIRouter(prefix="/live")
    registry = Registry(out_root, deps_factory)

    @router.get("/status")
    def status() -> dict:
        deps = registry.shared
        active = registry.active()
        checks = run_preflight(lab=deps.lab, reader=deps.reader, env=deps.env)
        try:
            ingest_age = deps.reader.ingest_age_s()
        except Exception:
            ingest_age = None
        return {
            "run": active.snapshot() if active is not None else None,
            "preflight": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
            "lab": {"services": deps.lab.app_services(), "ingest_age_s": ingest_age},
            "replays": list_replays(out_root),
            "targets": list(VETTED_TARGETS),
            "presets": {name: vars(t) for name, t in PRESETS.items()},
        }

    @router.get("/topology")
    def topology() -> dict:
        edges = json.loads(_TOPOLOGY_PATH.read_text()).get("edges", [])
        return {"services": list(APP_SERVICES), "edges": edges}

    @router.post("/runs")
    def start_run(body: StartRunBody) -> dict:
        run = registry.start_live(body.target, body.preset)
        return {"run_id": run.run_id}

    @router.post("/replays/{source_run_id}")
    def start_replay_run(source_run_id: str) -> dict:
        run = registry.start_replay(source_run_id)
        return {"run_id": run.run_id}

    def _snapshot_of(run_id: str) -> dict:
        run = registry.get(run_id)
        if run is not None:
            return run.snapshot()
        on_disk = registry.out_root / run_id / "run.json"
        if on_disk.exists():
            return json.loads(on_disk.read_text())
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")

    @router.get("/runs/{run_id}")
    def snapshot(run_id: str) -> dict:
        return _snapshot_of(run_id)

    @router.post("/runs/{run_id}/abort")
    def abort(run_id: str) -> dict:
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
        run.abort()
        return {"ok": True}

    @router.post("/runs/{run_id}/approve")
    def approve(run_id: str, body: DecisionBody) -> dict:
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
        if not run.approve(body.approver):
            raise HTTPException(status_code=409, detail="run is not awaiting approval")
        return {"ok": True}

    @router.post("/runs/{run_id}/deny")
    def deny(run_id: str, body: DecisionBody) -> dict:
        run = registry.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
        if not run.deny(body.approver, body.reason):
            raise HTTPException(status_code=409, detail="run is not awaiting approval")
        return {"ok": True}

    @router.get("/runs/{run_id}/stream")
    async def stream(run_id: str, after: int = 0) -> StreamingResponse:
        run = registry.get(run_id)

        if run is not None and run.is_active:
            sub = run.bus.subscribe(after)

            def _next() -> dict | None | str:
                try:
                    return sub.get(timeout=1.0)
                except queue.Empty:
                    return "timeout"

            async def live_gen():
                loop = asyncio.get_running_loop()
                while True:
                    frame = await loop.run_in_executor(None, _next)
                    if frame is None:
                        break
                    if frame == "timeout":
                        yield ": keepalive\n\n"
                        continue
                    yield _sse(frame)

            return StreamingResponse(live_gen(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache"})

        # finished (or restarted-server) runs: replay the journal verbatim
        events_path = registry.out_root / run_id / "events.jsonl"
        if run is not None and not events_path.exists():
            frames = run.bus.backlog(after)  # a finished replay keeps its ring buffer

            async def ring_gen():
                for frame in frames:
                    yield _sse(frame)

            return StreamingResponse(ring_gen(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache"})
        if not events_path.exists():
            raise HTTPException(status_code=404, detail=f"unknown run {run_id}")

        async def disk_gen():
            for line in events_path.read_text().splitlines():
                if not line.strip():
                    continue
                frame = json.loads(line)
                if frame["seq"] > after:
                    yield _sse(frame)

        return StreamingResponse(disk_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @router.get("/telemetry")
    def telemetry(run_id: str) -> dict:
        run = registry.get(run_id)
        snap = _snapshot_of(run_id)

        if run is not None and run.mode == "replay":
            path = run.telemetry_path
            if not path.exists():
                return {"series": {}, "fetched_at_ms": 0}
            at_ms = run.current_at_ms if run.is_active else 2**62
            return ReplayTelemetryReader(path).at(at_ms)

        journal = registry.out_root / run_id / "telemetry.jsonl"
        if run is not None and run.is_active:
            deps = getattr(run, "_deps")
            until_ms = int(deps.clock() * 1000)
            return deps.reader.series(list(APP_SERVICES), snap["started_ms"], until_ms)
        if journal.exists():
            return ReplayTelemetryReader(journal).at(2**62)
        if run is not None:
            deps = getattr(run, "_deps")
            last = snap["phases"][-1]["at_ms"] if snap.get("phases") else int(deps.clock() * 1000)
            return deps.reader.series(list(APP_SERVICES), snap["started_ms"], last)
        return {"series": {}, "fetched_at_ms": 0}

    @router.post("/lab/boot")
    def boot() -> dict:
        registry.shared.lab.up()
        return {"ok": True}

    @router.post("/lab/clear-fault")
    def clear_fault(body: ClearFaultBody) -> dict:
        registry.shared.clear_cpu(body.target)
        return {"ok": True}

    return router
