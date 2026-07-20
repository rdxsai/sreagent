"""Schema-constrained shapes for the code-mode orchestration.

Faults are classified by observable SIGNATURE (what the telemetry shows), not by the
tool that proves them, so a metric-only signal has a home. The three symptom signatures
are all detectable from metrics alone: resource, latency, error. `edge` is an optional
localizer, not a fourth class.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Signature = Literal["resource", "latency", "error"]


class Hypothesis(BaseModel):
    candidate_service: str = Field(description="the single suspected root-cause service, verbatim from graph/incident")
    signature: Signature = Field(description="resource | latency | error -- the onset symptom to test")
    edge: list[str] | None = Field(default=None, description="[upstream, downstream] localizer when relevant, else null")
    tool_subset: list[str] = Field(default_factory=list, description="3-8 tool names verbatim from the catalog")
    investigation_directive: str = Field(default="", description="1-3 sentences of what the worker should focus on")


class Plan(BaseModel):
    hypotheses: list[Hypothesis] = Field(default_factory=list)


class WorkerVerdict(BaseModel):
    hypothesis: str
    supported: bool = Field(description="does the evidence support this service as the origin")
    root_cause_service: str | None = None
    signature: Signature | None = Field(default=None, description="the signature actually observed at onset")
    observed_signatures: dict[str, bool] = Field(
        default_factory=dict, description="resource/latency/error(/edge) that stepped at onset for the candidate")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)



class Synthesis(BaseModel):
    ranked_services: list[str] = Field(default_factory=list, description="up to 5, most likely root cause first")
    root_cause_service: str = Field(description="== ranked_services[0]")
    fault_type: str | None = None
    justification: str = ""
