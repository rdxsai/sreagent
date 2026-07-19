"""The manager's two reasoning ops. It holds no tools and never sees raw telemetry:
plan() reasons over the compact catalog + the dependency graph to produce hypotheses
with tight tool subsets; synthesize() reasons over the workers' structured verdicts to
pick a root cause. Both are schema-constrained.
"""

from __future__ import annotations

import json

import openai

from sentinel.oss.catalog import all_tool_names, catalog
from sentinel.oss.llm import structured
from sentinel.oss.schemas import Plan, Synthesis
from sentinel.oss.trace import TraceContext, TraceLogger
from sentinel.providers import ModelPreset

_PLAN_SYSTEM = (
    "You are the SRE incident manager. You do NOT see raw telemetry -- you reason only over the "
    "service dependency graph and the tool catalog, and delegate investigation to subagents.\n\n"
    "TASK\n"
    "Produce 2-4 COMPETING hypotheses about the SINGLE root-cause service. Workers confirm or "
    "refute each independently; a later step picks the winner. Make them distinct bets, not "
    "variants of one guess.\n\n"
    "RULES\n"
    "- Real identifiers only: candidate_service must appear verbatim in the graph or incident; "
    "every tool name must appear verbatim in the catalog. Never invent a service or a tool name.\n"
    "- Propagation: the reported symptom is usually DOWNSTREAM of the cause. Trace the anomaly "
    "BACKWARD along dependency edges toward upstream services inside the blast radius.\n"
    "- Fault-class diversity is a hedge, not a quota: saturation (cpu/mem/disk), network_edge "
    "(delay/loss on an A->B edge), internal (the service's own errors/latency). If the graph "
    "clearly points to one class, several hypotheses within it (different services) are fine; if "
    "ambiguous, span >=2 classes. The same service may appear under two classes.\n"
    "- network_edge hypotheses: set edge=[upstream, downstream] and use the UPSTREAM service as "
    "candidate_service (the side attributed as root cause) unless the incident says otherwise.\n"
    "- tool_subset (3-8 names) must let the worker BOTH confirm the symptom on the candidate AND "
    "test its class: saturation -> resource + baseline + latency/error tools; network_edge -> "
    "edge-latency + correlation tools; internal -> error-origin + latency tools.\n"
    "- This environment may have NO deploy/change events; do not spend a hypothesis on 'a recent "
    "change' unless the telemetry clearly implicates one.\n\n"
    "OUTPUT -- a single JSON object, no prose:\n"
    '{{"hypotheses":[{{"candidate_service":str,"fault_class":"saturation"|"network_edge"|"internal",'
    '"edge":[str,str] or null,"tool_subset":[str,...],"rationale":str}}]}}  (2-4 items)\n\n'
    "EXAMPLE\n"
    'Incident: "checkout p99 latency up 5x, started ~14:38"\n'
    "Graph (edges ranked by blast radius): frontend->checkout, checkout->payment (anomalous), checkout->cart\n"
    "Output:\n"
    '{{"hypotheses":[\n'
    ' {{"candidate_service":"payment","fault_class":"saturation","edge":null,\n'
    '  "tool_subset":["metrics_resource_saturation","metrics_compare_baseline","traces_latency_origin","traces_error_origin","logs_error_clusters"],\n'
    '  "rationale":"payment is upstream of the anomalous checkout->payment edge; cpu/mem saturation there raises checkout latency downstream."}},\n'
    ' {{"candidate_service":"payment","fault_class":"network_edge","edge":["checkout","payment"],\n'
    '  "tool_subset":["traces_edge_latency_origin","correlate_signals","traces_latency_origin","metrics_top_movers","logs_search"],\n'
    '  "rationale":"the checkout->payment edge is the ranked anomaly; delay/loss there shows as checkout p99 without payment-side saturation."}}\n'
    ']}}\n\n'
    "TOOL CATALOG\n{catalog}"
)

_SYNTH_SYSTEM = (
    "You are the SRE incident manager doing final synthesis. You receive structured verdicts from "
    "subagents, each of which tested one hypothesis by inspecting telemetry. Choose the single "
    "root_cause_service, the fault_type, and a short justification citing the verdicts. Prefer a "
    "service whose OWN work failed (supported=true, higher confidence) over a victim that merely "
    "calls a failing dependency."
)


def plan(client: openai.OpenAI, preset: ModelPreset, *, incident: str, dep_graph: str,
         trace: TraceLogger, ctx: TraceContext) -> Plan:
    messages = [
        {"role": "system", "content": _PLAN_SYSTEM.format(catalog=catalog())},
        {"role": "user", "content": (
            f"INCIDENT\n{incident}\n\nDEPENDENCY GRAPH (edges ranked by blast radius)\n{dep_graph}")},
    ]
    plan_obj, usage, reasoning = structured(client, preset, messages, Plan)
    valid = set(all_tool_names())
    for h in plan_obj.hypotheses:
        kept = [t for t in h.tool_subset if t in valid]  # repair: drop any tool not in the registry
        h.tool_subset = kept or ["traces_build_topology", "metrics_resource_saturation", "traces_error_origin"]
    trace.manager(ctx, step="plan", hypotheses=[h.model_dump() for h in plan_obj.hypotheses],
                  reasoning=reasoning, usage=usage)
    return plan_obj


def synthesize(client: openai.OpenAI, preset: ModelPreset, *, incident: str, verdicts: list[dict],
               trace: TraceLogger, ctx: TraceContext) -> Synthesis:
    messages = [
        {"role": "system", "content": _SYNTH_SYSTEM},
        {"role": "user", "content": f"Incident:\n{incident}\n\nWorker verdicts:\n{json.dumps(verdicts, indent=2)}"},
    ]
    synth, usage, reasoning = structured(client, preset, messages, Synthesis)
    trace.manager(ctx, step="synthesize", result=synth.model_dump(), reasoning=reasoning, usage=usage)
    return synth
