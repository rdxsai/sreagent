"""End-to-end code-mode RCA over one scenario: fetch maintained topology -> manager
plan (signature taxonomy) -> parallel depth-1 workers (full onset vector) -> manager
synthesize (ranked) -> ranked root cause, all writing one JSONL trace tree. Topology is
an input, not a per-incident trace computation, so metric-only faults still get a graph.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sentinel.oss.manager import plan, synthesize
from sentinel.oss.schemas import Hypothesis, WorkerVerdict
from sentinel.oss.topology import resolve_topology, system_of
from sentinel.oss.trace import TraceContext, TraceLogger
from sentinel.oss.worker import run_worker
from sentinel.providers import client_for
from sentinel.tools.stats import Z_MIN
from sentinel.tools.store import TelemetryStore

# family -> human fault label (shared with run_live._fast_localize) and the metric a worker
# should read to confirm that family, so a corrected directive names a concrete series.
_SIG_FAULT = {"resource": "resource saturation", "latency": "latency/slowdown", "error": "errors"}
_SIG_METRIC = {"resource": "cpu_utilization / memory_mb", "latency": "latency_p95_ms",
               "error": "request_error_rate"}


_CONFIRM_TOOLS = ["metrics_summary_all", "metrics_compare_baseline", "metrics_top_movers",
                  "metrics_resource_saturation", "metrics_detect_shift"]


def _dominant_family(effects: dict[str, float] | None) -> str | None:
    """The family that deterministically stepped for a service (max z >= Z_MIN), else None."""
    if not effects:
        return None
    fam, z = max(effects.items(), key=lambda kv: kv[1])
    return fam if z >= Z_MIN else None


def _confirm_directive(service: str, family: str) -> str:
    return (f"The anomaly overlay shows {service}'s own {family} signal stepped at onset. "
            f"Confirm it directly: compare {service}'s {_SIG_METRIC[family]} pre vs post onset, "
            f"and set observed_signatures/supported from what actually stepped.")


@dataclass
class RcaResult:
    root_cause_service: str | None
    synthesis: dict[str, Any] | None
    graph: dict[str, Any]
    ranked_services: list[str] = field(default_factory=list)
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
    system: str | None = None,
    onset: int = 720,
    backend: str = "docker",
    worker_concurrency: int = 3,
    worker_iters: int = 6,
    prefer_trace: bool = False,
    trace: TraceLogger | None = None,
) -> RcaResult:
    client, preset = client_for(model)
    run_id = run_id or f"rca-{int(time.time())}"
    system = system or system_of(run_id)
    if trace is None:
        trace = TraceLogger(Path(out_dir) / f"{run_id}.jsonl")
    root = TraceContext(run_id=run_id, agent_id="manager")
    usage: dict[str, int] = {"input": 0, "output": 0}

    def _add(u: dict[str, int]) -> None:
        usage["input"] += u.get("input", 0)
        usage["output"] += u.get("output", 0)

    # Fix 1: topology is a maintained INPUT (static/trace/causal chain), not a trace worker.
    # Live systems prefer the trace source so the graph carries the live service vocabulary.
    graph = resolve_topology(store, system=system, onset=onset, prefer_trace=prefer_trace)
    trace.manager(root, step="topology", source=graph.source, edges=len(graph.edges),
                  ranked=graph.ranked_services[:8], traces_present=graph.traces_present)
    traces_available = graph.traces_present  # spans exist -> workers may use trace tools
    dep_graph = json.dumps({"edges": graph.edges, "ranked_services": graph.ranked_services,
                            "source": graph.source})
    graph_out = {"edges": graph.edges, "ranked_services": graph.ranked_services, "source": graph.source}

    # Fix 2: plan by observable signature, metrics-first (trace tools only if traces exist).
    plan_obj = plan(client, preset, incident=incident, dep_graph=dep_graph,
                    traces_available=traces_available, trace=trace, ctx=root)

    # Guarantee the deterministically anomalous, top-ranked origins are actually investigated. The
    # manager never sees telemetry and sometimes omits the #1 anomalous service entirely; inject a
    # hypothesis for any it missed, signed by the family that stepped, so a worker confirms the origin
    # with its own evidence rather than the pick resting on the overlay alone. Anomalous origins can't
    # be refuted (below), so this never changes the localization -- only whether it is evidence-backed.
    planned = {h.candidate_service for h in plan_obj.hypotheses}
    for svc in graph.ranked_services[:3]:
        dom = _dominant_family(graph.effects.get(svc))
        if dom and svc in graph.anomalous and svc not in planned:
            plan_obj.hypotheses.append(Hypothesis(
                candidate_service=svc, signature=dom, tool_subset=list(_CONFIRM_TOOLS),
                investigation_directive=_confirm_directive(svc, dom)))
            trace.manager(root, step="candidate_injected", candidate=svc, signature=dom,
                          effects={k: round(v, 1) for k, v in graph.effects[svc].items()})

    def _one(idx_h: tuple[int, Any]) -> Any:
        i, h = idx_h
        # The manager guesses each candidate's signature from the graph alone (it never sees
        # telemetry). Correct it to the family that DETERMINISTICALLY stepped for this candidate
        # (the anomaly overlay is authoritative), so the worker investigates the metric that
        # actually moved instead of chasing a mis-guessed family. Only fires for a deterministically
        # anomalous candidate, so rule-out hypotheses on quiet services keep the manager's framing.
        dom = _dominant_family(graph.effects.get(h.candidate_service))
        if dom and dom != h.signature:
            trace.manager(root, step="signature_correction", candidate=h.candidate_service,
                          manager_signature=h.signature, overlay_signature=dom,
                          effects={k: round(v, 1) for k, v in graph.effects[h.candidate_service].items()})
            h.signature = dom
            h.investigation_directive = _confirm_directive(h.candidate_service, dom)
        wctx = root.child(f"worker:{i}:{h.candidate_service}")
        edge = f" on edge {h.edge[0]}->{h.edge[1]}" if h.edge else ""
        hyp_text = f"[{h.signature}] Candidate origin: {h.candidate_service}{edge}. {h.investigation_directive}"
        run = run_worker(
            client, preset, store, incident=incident, hypothesis=hyp_text,
            tool_subset=h.tool_subset, result_schema=WorkerVerdict, onset=onset,
            ctx=wctx, trace=trace, backend=backend, max_iters=worker_iters,
        )
        # A worker confirms/refutes ITS candidate; a supported verdict attributes to that
        # candidate, never to a louder victim the model may have named. Origin-vs-victim is
        # decided deterministically over the graph rank below.
        if run.verdict is not None:
            run.verdict["candidate_service"] = h.candidate_service
            # For a deterministically anomalous candidate, the overlay's onset_effects ARE the
            # evidence (the same metric step the worker's own tools compute -- observed live as an
            # effect_z=27.7 cpu shift the LLM then labelled "unsupported"). Trust the overlay over the
            # model's verdict judgment: reconcile observed_signatures/supported/signature to what
            # deterministically stepped. Localization is unchanged (anomalous origins are refutation
            # -proof either way); this only makes the verdict and fault_type evidence-backed.
            eff = graph.effects.get(h.candidate_service, {})
            dom = _dominant_family(eff)
            if dom:
                run.verdict["observed_signatures"] = {fam: z >= Z_MIN for fam, z in eff.items()}
                run.verdict["supported"] = True
                run.verdict["signature"] = dom
            if run.verdict.get("supported"):
                run.verdict["root_cause_service"] = h.candidate_service
        return run

    with ThreadPoolExecutor(max_workers=worker_concurrency) as pool:
        runs = list(pool.map(_one, list(enumerate(plan_obj.hypotheses))))
    for r in runs:
        _add(r.usage)
    verdicts = [r.verdict for r in runs if r.verdict is not None]

    # DETERMINISTIC origin pick over the graph rank (the anomaly overlay is correct where the
    # LLM workers/synthesis are not): the root cause is the highest-ranked service that a worker
    # did NOT refute. Refutation must ALSO be deterministic: a worker's supported=False only
    # counts if the service is deterministically NOT anomalous (no onset signature stepped). This
    # blocks the failure where the manager mis-assigns a signature (e.g. latency for a socket=
    # resource fault) and the worker refutes the true origin whose OTHER signature did step. So:
    # victim self-support is harmless (ranks below origin), origin harness-fail is harmless (no
    # refutation), and a mis-signature refutation of an anomalous origin is ignored.
    anomalous = set(graph.anomalous)
    refuted = {v.get("candidate_service") for v in verdicts
               if v.get("supported") is False and v.get("candidate_service") not in anomalous}
    ranked_det = [s for s in graph.ranked_services if s not in refuted] or graph.ranked_services
    root_cause = ranked_det[0] if ranked_det else None

    # The LLM synthesis now only narrates (justification); it cannot change the pick. fault_type is
    # taken deterministically from the chosen origin's own stepped family (the overlay is authoritative
    # and never null when the origin is anomalous); the LLM synthesis is a fallback for the rare
    # non-anomalous pick.
    fault_type = _SIG_FAULT.get(_dominant_family(graph.effects.get(root_cause)))
    justification = None
    if verdicts:
        synth = synthesize(client, preset, incident=incident, verdicts=verdicts, dep_graph=dep_graph,
                           trace=trace, ctx=root)
        justification = synth.justification
        fault_type = fault_type or synth.fault_type
    trace.manager(root, step="final_answer", root_cause_service=root_cause,
                  ranked=ranked_det[:5], refuted=sorted(x for x in refuted if x))
    return RcaResult(
        root_cause_service=root_cause,
        synthesis={"root_cause_service": root_cause, "ranked_services": ranked_det[:5],
                   "fault_type": fault_type, "justification": justification},
        graph=graph_out, ranked_services=ranked_det[:5], verdicts=verdicts,
        trace_path=str(trace.path()), usage=usage,
    )
