"""Executors: render a SuggestedAction's concrete op (for preview) and execute it.

The agent process never holds the credential that runs a mutate op; execution happens
here, in the server process, driven by an approval event. Option A ships DryRunExecutor
(journals the exact op it would run, simulated ok outcome, no state change). Option B adds
LiveExecutor behind the same interface.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from sentinel.actions.models import SuggestedAction


@dataclass
class Outcome:
    ok: bool
    before: object = None
    after: object = None
    duration_s: float = 0.0
    error: str = ""


class Executor(Protocol):
    backend: str
    def render(self, action: SuggestedAction) -> str: ...
    def execute(self, action: SuggestedAction) -> Outcome: ...


# abstract action -> concrete op template. The model never sees these strings.
def _concrete_op(action: SuggestedAction) -> str:
    svc = action.target_service
    p = action.params
    if action.kind == "restart":
        return f"docker compose restart {svc}"
    if action.kind == "scale":
        return f"docker compose up -d --scale {svc}={p.get('replicas', 2)}"
    if action.kind == "remove_impairment":
        return f"clear injected fault on {svc} (kill pumba/stress for {svc})"
    if action.kind == "revert_change":
        return f"revert last change on {svc}"
    if action.kind == "open_ticket":
        return f"open incident ticket for {svc}: {action.description}"
    if action.kind == "page_oncall":
        return f"page on-call for {svc}: {action.description}"
    return f"(unknown op for kind={action.kind})"


class DryRunExecutor:
    """Option A: renders and journals the op it WOULD run; changes no real state."""
    backend = "dry_run"

    def render(self, action: SuggestedAction) -> str:
        return _concrete_op(action)

    def execute(self, action: SuggestedAction) -> Outcome:
        t0 = time.time()
        op = self.render(action)
        # simulated success; no side effect. before/after are illustrative placeholders.
        return Outcome(ok=True, before={"state": "impaired"}, after={"state": "would_recover"},
                       duration_s=round(time.time() - t0, 4), error="")
