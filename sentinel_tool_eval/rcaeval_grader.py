"""Score a RootCauseReport against RCAEvalTruth: AC@1 service localization.

RCAEval has no change events, so the change-based dimensions of the standard
grader do not apply. The headline `correct` is location-only (the agent's single
committed service must be an accepted root-cause service). type_match and
indicator_correct are reported for analysis but do not gate `correct`.
"""

from __future__ import annotations

from typing import Any

from sentinel_rcaeval.truth import RCAEvalTruth
from sentinel_tool_eval.grader import _type_match


def grade_localization(report: dict[str, Any] | None, truth: RCAEvalTruth) -> dict[str, Any]:
    if not report:
        return {
            "correct": False,
            "location_correct": False,
            "type_match": False,
            "indicator_correct": False,
            "reason": "no report submitted",
        }
    rc = report.get("root_cause") or {}
    accepted = truth.accepted_services or ([truth.root_cause.service] if truth.root_cause.service else [])
    location_correct = rc.get("service") in accepted
    if not location_correct and rc.get("kind") == "edge":
        location_correct = rc.get("caller") in accepted and rc.get("callee") in accepted

    type_match = _type_match(rc.get("type"), truth.root_cause.type)
    indicator = truth.root_cause_indicator
    evidence_text = " ".join(report.get("evidence") or [])
    indicator_correct = bool(indicator and indicator in evidence_text)

    return {
        "correct": bool(location_correct),
        "location_correct": bool(location_correct),
        "type_match": bool(type_match),
        "indicator_correct": indicator_correct,
        "reported": {"root_cause": rc},
        "truth": {
            "root_cause": truth.root_cause.model_dump(exclude_none=True),
            "accepted_services": accepted,
            "indicator": indicator,
        },
    }
