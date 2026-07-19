"""Schema-constrained shapes for the code-mode orchestration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkerVerdict(BaseModel):
    hypothesis: str
    supported: bool = Field(description="does the evidence support this hypothesis")
    root_cause_service: str | None = None
    fault_type: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class GraphResult(BaseModel):
    edges: list[list[str]] = Field(default_factory=list, description="[caller, callee] pairs")
    ranked_services: list[str] = Field(default_factory=list, description="services by blast radius, widest first")
    notes: str = ""


class Hypothesis(BaseModel):
    candidate_service: str = Field(description="the single suspected root-cause service, verbatim from graph/incident")
    fault_class: str = Field(default="internal", description="saturation | network_edge | internal")
    edge: list[str] | None = Field(default=None, description="[upstream, downstream] for network_edge, else null")
    tool_subset: list[str] = Field(default_factory=list, description="3-8 tool names verbatim from the catalog")
    rationale: str = ""


class Plan(BaseModel):
    hypotheses: list[Hypothesis] = Field(default_factory=list)


class Synthesis(BaseModel):
    root_cause_service: str
    fault_type: str | None = None
    justification: str
