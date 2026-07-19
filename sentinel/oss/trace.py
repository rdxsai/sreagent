"""Trace tree threaded through every LLM call and tool call, written as one JSONL
file per run. Each record carries run_id / agent_id / parent_id so the tree is
reconstructable, and distinguishes crashed (traceback / harness_fail) from wrong.

Code mode's reasoning lives in two places, so both are logged: the model's prose
(reasoning field) AND the code it wrote.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TraceContext:
    run_id: str
    agent_id: str
    parent_id: str | None = None

    def child(self, agent_id: str) -> "TraceContext":
        return TraceContext(run_id=self.run_id, agent_id=agent_id, parent_id=self.agent_id)


class TraceLogger:
    """Append-only JSONL writer, thread-safe for the parallel worker fan-out."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0

    def log(self, ctx: TraceContext, kind: str, **data: Any) -> None:
        with self._lock:
            self._seq += 1
            record = {
                "seq": self._seq,
                "run_id": ctx.run_id,
                "agent_id": ctx.agent_id,
                "parent_id": ctx.parent_id,
                "kind": kind,
                **data,
            }
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")

    # convenience shapes ------------------------------------------------------

    def manager(self, ctx: TraceContext, **data: Any) -> None:
        self.log(ctx, "manager", **data)

    def worker(self, ctx: TraceContext, **data: Any) -> None:
        self.log(ctx, "worker", **data)

    def code_iter(self, ctx: TraceContext, i: int, *, code: str, stdout: str,
                  traceback: str | None, reasoning: str = "") -> None:
        self.log(ctx, "code_iter", i=i, code=code, stdout=stdout,
                 traceback=traceback, reasoning=reasoning)

    def path(self) -> Path:
        return self._path


def load_tree(path: str | Path) -> list[dict]:
    """Read the JSONL back as records (for reconstruction / assertions)."""
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
