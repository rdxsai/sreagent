"""Eval-only ground truth for an RCAEval case.

Derived entirely from the case directory name. Written only to
eval_only/truth.json and read only by the RCAEval grader.
"""

from __future__ import annotations

from sentinel.fixtures.schemas import RootCause, StrictModel
from sentinel_rcaeval.case import RCAEvalCase

FAULT_TYPE = {
    "cpu": "cpu exhaustion",
    "mem": "memory leak",
    "disk": "disk stress",
    "socket": "socket stress",
    "delay": "network delay",
    "loss": "packet loss",
    "f1": "code fault incorrect parameter",
    "f2": "code fault missing parameter",
    "f3": "code fault missing call",
    "f4": "code fault incorrect return",
    "f5": "code fault missing exception handler",
}

FAULT_CATEGORY = {
    "cpu": "resource", "mem": "resource", "disk": "resource", "socket": "resource",
    "delay": "network", "loss": "network",
    "f1": "code", "f2": "code", "f3": "code", "f4": "code", "f5": "code",
}

FAULT_INDICATOR = {
    "cpu": "cpu_utilization",
    "mem": "memory_mb",
    "delay": "latency_p95_ms",
    "loss": "request_error_rate",
}


class RCAEvalTruth(StrictModel):
    scenario_id: str
    root_cause: RootCause
    accepted_services: list[str]
    root_cause_indicator: str | None = None
    fault_category: str


def build_truth(case: RCAEvalCase) -> RCAEvalTruth:
    return RCAEvalTruth(
        scenario_id=case.case_id,
        root_cause=RootCause(
            kind="service",
            type=FAULT_TYPE.get(case.fault, case.fault),
            service=case.service,
        ),
        accepted_services=[case.service],
        root_cause_indicator=FAULT_INDICATOR.get(case.fault),
        fault_category=FAULT_CATEGORY.get(case.fault, "unknown"),
    )
