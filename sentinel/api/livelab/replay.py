"""Replay a journaled run through the identical event contract, waits compressed.

The UI cannot tell a replay from a live run (same frames, same order); only the
snapshot's mode says so. Charts follow along: `current_at_ms` tracks the source
timestamp of the last re-emitted frame, and the router serves the telemetry
journal frame nearest that moment, so the charts draw progressively too.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from sentinel.api.livelab.bus import EventBus

_MAX_SLEEP_S = 5.0
_SPEEDUP = 4.0


def compress_gap(gap_ms: float) -> float:
    """Real gap between journaled frames -> replay sleep: quarter speed, capped at
    5s so baseline/soak minutes collapse while the investigation keeps its rhythm."""
    if gap_ms <= 0:
        return 0.0
    return min(_MAX_SLEEP_S, (gap_ms / 1000.0) / _SPEEDUP)


def list_replays(out_root: Path) -> list[dict]:
    """Finished journaled runs under out_root, newest first."""
    replays: list[dict] = []
    if not out_root.exists():
        return replays
    for d in sorted(out_root.iterdir()):
        run_json, events = d / "run.json", d / "events.jsonl"
        if not (d.is_dir() and run_json.exists() and events.exists()):
            continue
        try:
            meta = json.loads(run_json.read_text())
        except json.JSONDecodeError:
            continue
        replays.append({"run_id": d.name, "target": meta.get("target"),
                        "phase": meta.get("phase"), "preset": meta.get("preset"),
                        "started_ms": meta.get("started_ms")})
    replays.sort(key=lambda r: r.get("started_ms") or 0, reverse=True)
    return replays


class ReplayRun:
    mode = "replay"

    def __init__(self, run_id: str, source_dir: Path, *, sleep=time.sleep) -> None:
        self.run_id = run_id
        self.source_dir = source_dir
        self._sleep = sleep
        self._meta = json.loads((source_dir / "run.json").read_text())
        self.target = self._meta.get("target")
        self.bus = EventBus()
        self.current_at_ms: int = 0
        self._abort = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"replay-{run_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    @property
    def is_active(self) -> bool:
        return self._thread.is_alive()

    def abort(self) -> None:
        self._abort.set()

    def approve(self, approver: str) -> bool:  # a replay takes no decisions
        return False

    def deny(self, approver: str, reason: str = "") -> bool:
        return False

    @property
    def telemetry_path(self) -> Path:
        return self.source_dir / "telemetry.jsonl"

    def snapshot(self) -> dict:
        return {**self._meta, "run_id": self.run_id, "mode": self.mode,
                "source_run_id": self.source_dir.name,
                "current_at_ms": self.current_at_ms, "last_seq": self.bus.last_seq}

    def _run(self) -> None:
        try:
            frames = [json.loads(line)
                      for line in (self.source_dir / "events.jsonl").read_text().splitlines()
                      if line.strip()]
            prev_at: int | None = None
            for frame in frames:
                if self._abort.is_set():
                    return
                if frame["event"] == "done":   # the terminal frame is re-minted by close()
                    continue
                at_ms = frame.get("at_ms", 0)
                if prev_at is not None:
                    pause = compress_gap(at_ms - prev_at)
                    if pause > 0:
                        self._sleep(pause)
                        if self._abort.is_set():
                            return
                prev_at = at_ms
                self.current_at_ms = at_ms
                self.bus.emit(frame["event"], frame["data"])
        finally:
            self.bus.close()


def start_replay(source_run_id: str, out_root: Path, *, sleep=time.sleep) -> ReplayRun:
    source_dir = out_root / source_run_id
    if not (source_dir / "events.jsonl").exists():
        raise FileNotFoundError(f"no journaled run at {source_dir}")
    replay_id = f"replay-{source_run_id}-{int(time.time())}"
    return ReplayRun(replay_id, source_dir, sleep=sleep)
