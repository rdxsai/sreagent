"""The in-process event seam for a live lab run.

The run thread emits named frames ({seq, event, data, at_ms}); any number of SSE
subscribers drain them. A bounded ring buffer lets a late (or reconnecting) client
replay from a sequence number, and every frame is journaled to events.jsonl so a
finished run replays through the identical contract.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from sentinel.oss.trace import TraceLogger


class Subscription:
    """One consumer's queue. `get` returns the next frame, or None once the bus is
    closed and the backlog is drained."""

    def __init__(self) -> None:
        self._q: "queue.Queue[dict | None]" = queue.Queue()

    def put(self, frame: dict | None) -> None:
        self._q.put(frame)

    def get(self, timeout: float | None = None) -> dict | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


class EventBus:
    def __init__(self, *, journal_dir: Path | None = None, ring: int = 5000) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._ring: deque[dict] = deque(maxlen=ring)
        self._subs: list[Subscription] = []
        self._closed = False
        self._journal_path: Path | None = None
        if journal_dir is not None:
            journal_dir.mkdir(parents=True, exist_ok=True)
            self._journal_path = journal_dir / "events.jsonl"

    def emit(self, event: str, data: dict[str, Any]) -> int:
        with self._lock:
            self._seq += 1
            frame = {"seq": self._seq, "event": event, "data": data,
                     "at_ms": int(time.time() * 1000)}
            self._ring.append(frame)
            if self._journal_path is not None:
                with self._journal_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(frame, default=str) + "\n")
            for sub in self._subs:
                sub.put(frame)
            return self._seq

    def backlog(self, after: int = 0) -> list[dict]:
        with self._lock:
            return [f for f in self._ring if f["seq"] > after]

    def subscribe(self, after: int = 0) -> Subscription:
        sub = Subscription()
        with self._lock:
            for frame in self._ring:
                if frame["seq"] > after:
                    sub.put(frame)
            if self._closed:
                sub.put(None)
            else:
                self._subs.append(sub)
        return sub

    def close(self) -> None:
        """Emit the terminal done frame and end every subscription."""
        with self._lock:
            already = self._closed
        if not already:
            self.emit("done", {})
        with self._lock:
            self._closed = True
            for sub in self._subs:
                sub.put(None)
            self._subs.clear()

    @property
    def last_seq(self) -> int:
        with self._lock:
            return self._seq


class BroadcastTraceLogger(TraceLogger):
    """TraceLogger that also broadcasts each record as an `agent` frame, so the
    oss agent streams to the dashboard without knowing the dashboard exists."""

    def __init__(self, path: str | Path, bus: EventBus) -> None:
        super().__init__(path)
        self._bus = bus

    def log(self, ctx, kind: str, **data: Any) -> None:
        super().log(ctx, kind, **data)
        self._bus.emit("agent", {"run_id": ctx.run_id, "agent_id": ctx.agent_id,
                                 "parent_id": ctx.parent_id, "kind": kind, **data})
