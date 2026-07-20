"""Append-only, event-sourced action journal. Current state is a FOLD over events, never
stored separately, so the audit is the single source of truth and the safety property
(no mutate execution without a matching prior approval) is replayable from the file.

Mirrors sentinel/oss/trace.py TraceLogger. One journal file per action run; each action's
state is reconstructed by folding its events in order.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from sentinel.actions.models import ApprovalState, SuggestedAction

# events in lifecycle order; the fold enforces the legal transitions
EVENTS = ("proposed", "posted", "approved", "denied", "expired",
          "execute_started", "execute_result", "near_miss")

_TERMINAL = {"denied", "expired", "done", "failed"}


class ActionJournal:
    def __init__(self, path: str | Path, *, now: Callable[[], float] | None = None) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._now = now or time.time

    def append(self, kind: str, action_id: str, **data: Any) -> dict:
        if kind not in EVENTS:
            raise ValueError(f"unknown event kind {kind!r}")
        rec = {"ts": self._now(), "kind": kind, "action_id": action_id, **data}
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        return rec

    # convenience emitters -----------------------------------------------------

    def proposed(self, action: SuggestedAction) -> None:
        self.append("proposed", action.id, action=action.model_dump())

    def posted(self, action_id: str, surface: str, ref: str) -> None:
        self.append("posted", action_id, surface=surface, ref=ref)

    def approved(self, action_id: str, approver: str, surface: str) -> None:
        self.append("approved", action_id, approver=approver, surface=surface)

    def denied(self, action_id: str, approver: str, reason: str = "") -> None:
        self.append("denied", action_id, approver=approver, reason=reason)

    def expired(self, action_id: str) -> None:
        self.append("expired", action_id)

    def execute_started(self, action_id: str, backend: str, preview: str) -> None:
        self.append("execute_started", action_id, backend=backend, preview=preview)

    def execute_result(self, action_id: str, ok: bool, before: Any = None,
                       after: Any = None, duration_s: float = 0.0, error: str = "") -> None:
        self.append("execute_result", action_id, ok=ok, before=before, after=after,
                    duration_s=duration_s, error=error)

    def near_miss(self, action_id: str, attempted: str, by: str, why: str) -> None:
        self.append("near_miss", action_id, attempted=attempted, by=by, why=why)

    # read + fold --------------------------------------------------------------

    def events(self) -> list[dict]:
        if not self._path.exists():
            return []
        return [json.loads(l) for l in self._path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def fold(self) -> dict[str, ApprovalState]:
        """Reconstruct current state per action id from the event stream."""
        states: dict[str, ApprovalState] = {}
        for e in self.events():
            aid, kind = e["action_id"], e["kind"]
            if kind == "proposed":
                states[aid] = ApprovalState(action=SuggestedAction.model_validate(e["action"]),
                                            status="proposed")
                continue
            st = states.get(aid)
            if st is None:
                continue  # event for an unknown action; ignore (defensive)
            # legal transitions only; out-of-order events are ignored, never crash
            if kind == "posted" and st.status == "proposed":
                st.status, st.surface = "posted", e.get("surface")
            elif kind == "approved" and st.status in ("proposed", "posted"):
                st.status, st.approver, st.surface = "approved", e.get("approver"), e.get("surface")
            elif kind == "denied" and st.status in ("proposed", "posted"):
                st.status, st.approver = "denied", e.get("approver")
            elif kind == "expired" and st.status in ("proposed", "posted"):
                st.status = "expired"
            elif kind == "execute_started" and st.status == "approved":
                st.status = "executing"
            elif kind == "execute_result" and st.status == "executing":
                st.status = "done" if e.get("ok") else "failed"
                st.outcome = {k: e.get(k) for k in ("ok", "before", "after", "duration_s", "error")}
            # near_miss never changes lifecycle state; it is an audit signal only
        return states

    def state_of(self, action_id: str) -> ApprovalState | None:
        return self.fold().get(action_id)

    def is_terminal(self, action_id: str) -> bool:
        st = self.state_of(action_id)
        return st is not None and st.status in _TERMINAL

    def path(self) -> Path:
        return self._path
