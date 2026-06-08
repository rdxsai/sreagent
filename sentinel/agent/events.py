"""Live event stream for the demo.

The agent (manager + investigator threads) emits reasoning, tool, and subagent
events to a thread-safe sink; the SSE endpoint drains it and forwards each event to
the browser. Attaching a sink is opt-in: with no sink the agent runs exactly as in
the eval (non-streaming), so this never affects correctness or the test suite.

Event types (all carry `type`, `agent`, `t` seconds-since-start, plus a payload):
  status         {message}                         phase narration
  thinking       {text}                            a reasoning delta (token chunk)
  text           {text}                            a narration delta
  tool_call      {tool, input}                     a tool the agent invoked
  tool_result    {tool, summary, is_error}         the (truncated) tool result
  subagent_spawn {service}                          an investigator was spawned
  subagent_done  {service}                          an investigator finished
  finding        {service, is_origin, fault_type, confidence, suspect_change_id}
  report         {root_cause, culprit_change_id, ruled_out_change_ids}
  done           {}                                 run complete
  error          {message}
"""

from __future__ import annotations

import queue
import time
from typing import Any


class EventSink:
    """Thread-safe queue the agent emits to and the SSE generator drains."""

    def __init__(self) -> None:
        self._q: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
        self._t0 = time.monotonic()

    def emit(self, type_: str, agent: str = "manager", **payload: Any) -> None:
        self._q.put({"type": type_, "agent": agent, "t": round(time.monotonic() - self._t0, 3), **payload})

    def close(self) -> None:
        self._q.put(None)  # sentinel: no more events

    def get(self, timeout: float | None = None) -> dict[str, Any] | None:
        return self._q.get(timeout=timeout)


def summarize_result(output: dict[str, Any], limit: int = 400) -> str:
    """A short, human-readable one-liner for a tool result (full JSON would flood the UI)."""
    import json

    if isinstance(output, dict) and "error" in output:
        err = output["error"]
        return f"error: {err.get('code', '')}: {err.get('message', '')}"[:limit]
    text = json.dumps(output, separators=(",", ":"))
    return text if len(text) <= limit else text[:limit] + " …"
