"""Schemas for the gated action half.

A SuggestedAction is proposed with ABSTRACT params only (kind + target service + a small
param dict); the executor maps it to a concrete op at execute time. The model never sees or
emits the concrete command. params_hash binds an approval to exact action content: an approval
is valid only for the action it was granted for, re-checked at execute time.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

ActionKind = Literal["restart", "scale", "remove_impairment", "revert_change",
                     "open_ticket", "page_oncall"]
Effect = Literal["notify", "mutate"]
Risk = Literal["low", "medium", "high"]
Status = Literal["proposed", "posted", "approved", "denied", "expired",
                 "executing", "done", "failed"]

# which kinds change the incident system (need approval) vs. only communicate
MUTATE_KINDS: set[str] = {"restart", "scale", "remove_impairment", "revert_change"}
NOTIFY_KINDS: set[str] = {"open_ticket", "page_oncall"}


def params_hash(kind: str, target_service: str, params: dict) -> str:
    """Stable binding over (kind, target, sorted params). The approval is for exactly this."""
    payload = json.dumps({"kind": kind, "target_service": target_service,
                          "params": {k: params[k] for k in sorted(params)}}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SuggestedAction(BaseModel):
    id: str                                       # uuid, assigned at proposal time
    kind: ActionKind
    effect: Effect
    target_service: str
    params: dict[str, str | int] = Field(default_factory=dict)   # abstract only
    description: str = ""                          # human sentence for the card
    risk: Risk = "medium"
    reversible: bool = True
    preview: str = ""                              # the exact concrete op the executor would run
    citations: list[str] = Field(default_factory=list)   # evidence lines backing this
    params_hash: str = ""                          # sha256 over (kind, target, sorted params)

    def compute_hash(self) -> str:
        return params_hash(self.kind, self.target_service, dict(self.params))

    def with_hash(self) -> "SuggestedAction":
        return self.model_copy(update={"params_hash": self.compute_hash()})


class ApprovalState(BaseModel):
    action: SuggestedAction
    status: Status = "proposed"
    approver: str | None = None
    surface: str | None = None                     # slack | web
    expires_at: float | None = None
    outcome: dict | None = None                    # execute_result payload when done/failed
