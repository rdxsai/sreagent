"""Remediation catalog: a static, reviewable table mapping a diagnosed fault to ranked
candidate actions. The model produced the diagnosis; the catalog maps it, deterministically.
Reversible-first ordering. Exactly one mutate action is elected primary per brief (the guide
caps the card at one remediation button); the rest are text alternatives. Low confidence or
conflicting verdicts -> notify-only.
"""

from __future__ import annotations

import uuid

from sentinel.actions.executor import DryRunExecutor, Executor
from sentinel.actions.models import MUTATE_KINDS, SuggestedAction

# fault signature/type -> ordered (kind, params, reversible, risk). Reversible-first.
_TABLE: dict[str, list[tuple[str, dict, bool, str]]] = {
    "cpu":    [("restart", {}, True, "low"), ("scale", {"replicas": 2}, True, "low")],
    "mem":    [("restart", {}, True, "low"), ("scale", {"replicas": 2}, True, "low")],
    "memory": [("restart", {}, True, "low"), ("scale", {"replicas": 2}, True, "low")],
    "disk":   [("restart", {}, True, "medium"), ("page_oncall", {}, True, "low")],
    "delay":  [("remove_impairment", {}, True, "low"), ("restart", {}, True, "low")],
    "loss":   [("remove_impairment", {}, True, "low"), ("restart", {}, True, "low")],
    "socket": [("restart", {}, True, "low"), ("remove_impairment", {}, True, "low")],
    "error":  [("revert_change", {}, True, "medium"), ("restart", {}, True, "low")],
}
# signature -> the same lists (delay/loss present as latency+edge; resource covers cpu/mem/disk)
_SIGNATURE_FALLBACK = {"resource": "cpu", "latency": "delay", "error": "error"}

_NOTIFY_ONLY = [("open_ticket", {}, True, "low"), ("page_oncall", {}, True, "low")]

_DESCRIPTIONS = {
    "restart": "Restart {svc} to clear its impaired state.",
    "scale": "Scale {svc} to {replicas} replicas to shed load.",
    "remove_impairment": "Clear the injected network fault on {svc}.",
    "revert_change": "Revert the most recent change on {svc}.",
    "open_ticket": "Open an incident ticket for {svc}.",
    "page_oncall": "Page the on-call engineer for {svc}.",
}


def _key(fault_type: str | None, signature: str | None) -> list[tuple[str, dict, bool, str]]:
    if fault_type:
        for token in (fault_type.lower(), fault_type.lower().split()[0] if fault_type else ""):
            if token in _TABLE:
                return _TABLE[token]
    if signature and signature in _SIGNATURE_FALLBACK:
        return _TABLE[_SIGNATURE_FALLBACK[signature]]
    return []


def suggest_actions(
    *,
    target_service: str,
    fault_type: str | None,
    signature: str | None = None,
    citations: list[str] | None = None,
    confident: bool = True,
    executor: Executor | None = None,
) -> list[SuggestedAction]:
    """Ranked SuggestedAction drafts for the top-ranked service. Reversible-first; exactly one
    mutate elected primary (index 0 if any mutate present). Low confidence -> notify-only."""
    executor = executor or DryRunExecutor()
    citations = citations or []
    rows = _key(fault_type, signature) if confident else []
    if not rows:
        rows = _NOTIFY_ONLY
    # reversible-first, stable
    rows = sorted(rows, key=lambda r: (not r[2],))
    out: list[SuggestedAction] = []
    for kind, params, reversible, risk in rows:
        effect = "mutate" if kind in MUTATE_KINDS else "notify"
        desc = _DESCRIPTIONS.get(kind, kind).format(svc=target_service, **params)
        a = SuggestedAction(
            id=str(uuid.uuid4()), kind=kind, effect=effect, target_service=target_service,
            params=params, description=desc, risk=risk, reversible=reversible, citations=citations,
        )
        a = a.model_copy(update={"preview": executor.render(a)}).with_hash()
        out.append(a)
    return out


def elect_primary(actions: list[SuggestedAction]) -> SuggestedAction | None:
    """The single mutate action for the card's one remediation button (first mutate,
    which is reversible-first). Notify-only briefs return None (no remediation button)."""
    for a in actions:
        if a.effect == "mutate":
            return a
    return None
