"""Brief builder: a compact, explainable incident brief from a completed run's handoff
artifact (the serialized result.json). Deterministic, no LLM call: the investigation already
produced the content. The brief carries telemetry citations (worker evidence lines) so a human
approves an explained action, and the elected primary action's exact preview.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from sentinel.actions.catalog import elect_primary, suggest_actions
from sentinel.actions.models import SuggestedAction

_CONF_THRESHOLD = 0.34   # below this (or conflicting verdicts) -> notify-only


class Brief(BaseModel):
    run_id: str
    symptom: str
    root_cause_service: str | None
    fault_type: str | None
    graph_source: str = ""
    ranked: list[dict] = Field(default_factory=list)       # top-3: service + evidence + signatures
    primary_action: SuggestedAction | None = None
    alternatives: list[SuggestedAction] = Field(default_factory=list)
    confident: bool = True


def _top_evidence(verdicts: list[dict], service: str) -> tuple[list[str], dict]:
    """Strongest evidence lines + observed signature vector for a service, from its verdict."""
    for v in verdicts:
        if v.get("candidate_service") == service or v.get("root_cause_service") == service:
            return v.get("evidence", [])[:3], v.get("observed_signatures", {})
    return [], {}


def build_brief(result: dict) -> Brief:
    """result: the serialized RcaResult (runs/oss/<id>.result.json shape)."""
    run_id = result.get("run_id", "")
    synth = result.get("synthesis") or {}
    root = result.get("root_cause_service") or synth.get("root_cause_service")
    fault_type = synth.get("fault_type")
    verdicts = result.get("verdicts", [])
    ranked_services = (result.get("ranked_services") or synth.get("ranked_services") or [])[:3]

    ranked = []
    top_sig = {}
    for i, svc in enumerate(ranked_services):
        ev, sig = _top_evidence(verdicts, svc)
        if i == 0:
            top_sig = sig
        ranked.append({"service": svc, "evidence": ev, "observed_signatures": sig})

    # confidence: a supported verdict for the root cause, and it is the top-ranked service
    supported_root = any(
        (v.get("candidate_service") == root or v.get("root_cause_service") == root) and v.get("supported")
        for v in verdicts
    )
    confident = bool(root) and supported_root and (not ranked_services or ranked_services[0] == root)

    citations, _ = _top_evidence(verdicts, root) if root else ([], {})
    signature = next((k for k, on in top_sig.items() if on), None)
    actions = suggest_actions(
        target_service=root or (ranked_services[0] if ranked_services else "unknown"),
        fault_type=fault_type, signature=signature, citations=citations, confident=confident,
    ) if (root or ranked_services) else []

    primary = elect_primary(actions)
    alternatives = [a for a in actions if a is not primary]

    return Brief(
        run_id=run_id,
        symptom=result.get("symptom", "") or synth.get("justification", "") or "Incident under investigation.",
        root_cause_service=root, fault_type=fault_type, graph_source=result.get("graph_source", ""),
        ranked=ranked, primary_action=primary, alternatives=alternatives, confident=confident,
    )
