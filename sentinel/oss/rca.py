"""End-to-end code-mode RCA over one scenario: dependency-graph worker -> manager
plan -> parallel depth-1 workers -> manager synthesize -> root_cause_service, all
writing one JSONL trace tree. Depth-1 only (no nested subagents); worker concurrency
is capped for OpenRouter rate limits.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sentinel.oss.catalog import TOPOLOGY_TOOLS
from sentinel.oss.manager import plan, synthesize
from sentinel.oss.schemas import GraphResult, WorkerVerdict
from sentinel.oss.trace import TraceContext, TraceLogger
from sentinel.oss.worker import run_worker
from sentinel.providers import client_for
from sentinel.tools.store import TelemetryStore

_GRAPH_HYPOTHESIS = (
    "Map the service dependency graph for this incident and rank the services by blast radius "
    "(widest first). Use traces_build_topology and the topology tools. Call finish with a dict "
    "{edges: [[caller, callee], ...], ranked_services: [...], notes: ...}."
)


@dataclass
class RcaResult:
    root_cause_service: str | None
    synthesis: dict[str, Any] | None
    graph: dict[str, Any]
    verdicts: list[dict[str, Any]] = field(default_factory=list)
    trace_path: str = ""
    usage: dict[str, int] = field(default_factory=dict)


def run_rca(
    store: TelemetryStore,
    *,
    incident: str,
    out_dir: str | Path,
    model: str | None = None,
    run_id: str | None = None,
    onset: int = 720,
    backend: str = "docker",
    worker_concurrency: int = 3,
    graph_iters: int = 6,
    worker_iters: int = 6,
) -> RcaResult:
    client, preset = client_for(model)
    run_id = run_id or f"rca-{int(time.time())}"
    trace = TraceLogger(Path(out_dir) / f"{run_id}.jsonl")
    root = TraceContext(run_id=run_id, agent_id="manager")
    usage: dict[str, int] = {"input": 0, "output": 0}

    def _add(u: dict[str, int]) -> None:
        usage["input"] += u.get("input", 0)
        usage["output"] += u.get("output", 0)

    # step 10: dependency graph as a code-mode worker over the topology tools
    graph_run = run_worker(
        client, preset, store, incident=incident, hypothesis=_GRAPH_HYPOTHESIS,
        tool_subset=TOPOLOGY_TOOLS, result_schema=GraphResult, onset=onset,
        ctx=root.child("worker:graph"), trace=trace, backend=backend, max_iters=graph_iters,
    )
    _add(graph_run.usage)
    graph = graph_run.verdict or {"edges": [], "ranked_services": [], "notes": "graph worker failed"}

    # step 9a: manager plans over the catalog + graph (no raw telemetry in its context)
    plan_obj = plan(client, preset, incident=incident, dep_graph=str(graph), trace=trace, ctx=root)

    # step 11: parallel depth-1 workers, one per hypothesis
    def _one(idx_h: tuple[int, Any]) -> WorkerVerdict | Any:
        i, h = idx_h
        wctx = root.child(f"worker:{i}:{h.candidate_service}")
        edge = f" on edge {h.edge[0]}->{h.edge[1]}" if h.edge else ""
        hyp_text = f"[{h.fault_class}] Candidate root-cause service: {h.candidate_service}{edge}. {h.rationale}"
        return run_worker(
            client, preset, store, incident=incident, hypothesis=hyp_text,
            tool_subset=h.tool_subset, result_schema=WorkerVerdict, onset=onset,
            ctx=wctx, trace=trace, backend=backend, max_iters=worker_iters,
        )

    with ThreadPoolExecutor(max_workers=worker_concurrency) as pool:
        runs = list(pool.map(_one, list(enumerate(plan_obj.hypotheses))))
    for r in runs:
        _add(r.usage)
    verdicts = [r.verdict for r in runs if r.verdict is not None]

    # step 9b: manager synthesizes the verdicts into a root cause
    if not verdicts:
        trace.manager(root, step="final_answer", root_cause_service=None, note="no verdicts")
        return RcaResult(root_cause_service=None, synthesis=None, graph=graph, verdicts=[],
                         trace_path=str(trace.path()), usage=usage)
    synth = synthesize(client, preset, incident=incident, verdicts=verdicts, trace=trace, ctx=root)
    trace.manager(root, step="final_answer", root_cause_service=synth.root_cause_service)
    return RcaResult(
        root_cause_service=synth.root_cause_service, synthesis=synth.model_dump(),
        graph=graph, verdicts=verdicts, trace_path=str(trace.path()), usage=usage,
    )
