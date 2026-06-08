"""Score a submitted RootCauseReport against eval-only ground truth.

This is the only code that reads `eval_only/truth.json`. It scores the three
graded dimensions: where (root-cause location), what (failure type, leniently),
and which change (culprit, plus whether the decoys were ruled out). The headline
`correct` is location AND culprit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sentinel.fixtures.schemas import PrivateTruth


def load_truth(truth_path: Path) -> PrivateTruth:
    return PrivateTruth.model_validate_json(Path(truth_path).read_text(encoding="utf-8"))


def _tokens(text: str | None) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", (text or "").lower())) - {""}


def _type_match(reported: str | None, truth: str) -> bool:
    rt, tt = _tokens(reported), _tokens(truth)
    if not rt:
        return False
    overlap = rt & tt
    return len(overlap) >= max(1, len(tt) - 1)


def grade(report: dict[str, Any] | None, truth: PrivateTruth) -> dict[str, Any]:
    if not report:
        return {
            "correct": False,
            "location_correct": False,
            "culprit_correct": False,
            "decoys_ruled_out": False,
            "type_match": False,
            "reason": "no report submitted",
        }
    rc = report.get("root_cause") or {}
    kind_ok = rc.get("kind") == truth.root_cause.kind
    if truth.root_cause.kind == "service":
        location_correct = kind_ok and rc.get("service") == truth.root_cause.service
    elif truth.root_cause.kind == "edge":
        location_correct = (
            kind_ok
            and rc.get("caller") == truth.root_cause.caller
            and rc.get("callee") == truth.root_cause.callee
        )
    else:
        location_correct = kind_ok

    culprit_correct = report.get("culprit_change_id") == truth.culprit_change_id
    decoys_ruled_out = set(truth.decoy_change_ids) <= set(report.get("ruled_out_change_ids") or [])
    type_match = _type_match(rc.get("type"), truth.root_cause.type)

    return {
        "correct": bool(location_correct and culprit_correct),
        "location_correct": bool(location_correct),
        "culprit_correct": bool(culprit_correct),
        "decoys_ruled_out": bool(decoys_ruled_out),
        "type_match": bool(type_match),
        "reported": {
            "root_cause": rc,
            "culprit_change_id": report.get("culprit_change_id"),
            "ruled_out_change_ids": report.get("ruled_out_change_ids"),
        },
        "truth": {
            "root_cause": truth.root_cause.model_dump(exclude_none=True),
            "culprit_change_id": truth.culprit_change_id,
            "decoy_change_ids": truth.decoy_change_ids,
        },
    }
