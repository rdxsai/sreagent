"""The manager's two reasoning ops. It holds no tools and never sees raw telemetry:
plan() reasons over the maintained dependency graph + the tool catalog to produce
hypotheses classified by observable signature (resource/latency/error), each with a
tight metrics-first tool subset; synthesize() reasons over the workers' structured
verdicts (including the onset signature vector) to produce a ranked root-cause list.
"""

from __future__ import annotations

import json

import openai

from sentinel.oss.catalog import all_tool_names, catalog
from sentinel.oss.llm import structured
from sentinel.oss.schemas import Plan, Synthesis
from sentinel.oss.trace import TraceContext, TraceLogger
from sentinel.providers import ModelPreset

# metrics-first; every subset gets these so a worker can read the full onset vector
_RULING_OUT_CORE = ["metrics_summary_all", "metrics_compare_baseline", "metrics_top_movers"]

_PLAN_SYSTEM = (
    "You are the SRE incident manager. You do NOT see raw telemetry -- you reason only over the "
    "maintained service dependency graph and the tool catalog, and delegate to subagents.\n\n"
    "TASK\n"
    "Produce 2-4 COMPETING hypotheses about the SINGLE root-cause service. Workers confirm or refute "
    "each independently over metrics; a later step ranks them. Distinct bets, not variants of one guess.\n\n"
    "RULES\n"
    "- Classify by observable SIGNATURE, all detectable from metrics: resource (cpu/mem/disk/socket "
    "stepped), latency (own p50/p95 stepped, with cpu flat and no errors -- this is first-class origin "
    "evidence on its own, a delay or slow dependency), error (error rate / 5xx / connection errors "
    "stepped). Do NOT require a resource or error rise to accept a latency signal.\n"
    "- Real identifiers only: candidate_service verbatim from the graph or incident; every tool name "
    "verbatim from the catalog. Never invent a service or tool.\n"
    "- Propagation, across ALL signatures: the reported symptom is usually DOWNSTREAM of the cause. "
    "Trace the anomaly BACKWARD along dependency edges. An upstream resource or latency step outranks a "
    "downstream latency symptom. The graph's ranked_services already orders candidates by likely origin; "
    "prefer the top-ranked services and their upstream.\n"
    "- edge is an optional localizer ([upstream, downstream]) for a latency/error that lives on one hop, "
    "not a class. Most metric-only faults need no edge.\n"
    "- tool_subset (3-8 names): the reader for the assigned signature PLUS a metrics ruling-out core so "
    "the worker can read the whole onset vector (resource, latency, error). Metrics-first. Add trace "
    "tools ONLY if the incident says traces are available.\n"
    "- No deploy/change events here; do not spend a hypothesis on 'a recent change' unless telemetry "
    "clearly implicates one.\n\n"
    "OUTPUT -- a single JSON object, no prose:\n"
    '{{"hypotheses":[{{"candidate_service":str,"signature":"resource"|"latency"|"error",'
    '"edge":[str,str] or null,"tool_subset":[str,...],"investigation_directive":str}}]}}  (2-4 items)\n\n'
    "EXAMPLE (metric-only, no traces)\n"
    'Incident: "orders p95 latency up ~4x from second 720"; ranked_services: [orders, payment, shipping]\n'
    "Output:\n"
    '{{"hypotheses":[\n'
    ' {{"candidate_service":"orders","signature":"latency","edge":null,\n'
    '  "tool_subset":["metrics_compare_baseline","metrics_top_movers","metrics_summary_all","metrics_resource_saturation"],\n'
    '  "investigation_directive":"Confirm orders own latency_p95_ms stepped at onset; check cpu/mem are flat and no errors -- an own-latency rise alone makes orders the delayed origin. Verify downstream (payment, shipping) rose LESS, so they are victims."}},\n'
    ' {{"candidate_service":"payment","signature":"resource","edge":null,\n'
    '  "tool_subset":["metrics_resource_saturation","metrics_compare_baseline","metrics_summary_all","metrics_top_movers"],\n'
    '  "investigation_directive":"Rule out payment as origin: check whether payment has its OWN cpu/mem/disk step at onset, not just inherited latency."}}\n'
    ']}}\n\n'
    "TOOL CATALOG\n{catalog}"
)

_SYNTH_SYSTEM = (
    "You are the SRE incident manager doing final synthesis. You receive the dependency graph (its "
    "ranked_services list is already ordered origin-first by the anomaly overlay) and worker verdicts, each "
    "with observed_signatures. Return a ranked list of up to 5 services, most likely root cause first, and "
    "set root_cause_service to ranked_services[0].\n\n"
    "PRIMARY signal is the graph's ranked_services order. Among services that have a SUPPORTED verdict (their "
    "own onset signature stepped), pick the one HIGHEST in ranked_services as the root cause. A downstream or "
    "request-entry service (e.g. a frontend/gateway) with large latency is a VICTIM that merely inherited the "
    "delay -- NEVER rank it above an upstream supported origin just because its latency magnitude is larger. "
    "Only depart from the graph order if a top-ranked service has no supported own-signature."
)


def plan(client: openai.OpenAI, preset: ModelPreset, *, incident: str, dep_graph: str,
         traces_available: bool, trace: TraceLogger, ctx: TraceContext) -> Plan:
    trace_note = ("Traces ARE available for this incident (you may add trace tools)."
                  if traces_available else
                  "NO traces are available; use metrics and logs only. Do not choose trace tools.")
    messages = [
        {"role": "system", "content": _PLAN_SYSTEM.format(catalog=catalog())},
        {"role": "user", "content": (
            f"INCIDENT\n{incident}\n\n{trace_note}\n\n"
            f"DEPENDENCY GRAPH (edges ranked by blast radius, origin-first)\n{dep_graph}")},
    ]
    plan_obj, usage, reasoning = structured(client, preset, messages, Plan)
    valid = set(all_tool_names())
    for h in plan_obj.hypotheses:
        kept = [t for t in h.tool_subset if t in valid]
        # always guarantee the ruling-out core; drop trace tools when traces absent
        core = [t for t in _RULING_OUT_CORE if t in valid]
        kept = list(dict.fromkeys(kept + core))
        if not traces_available:
            kept = [t for t in kept if not t.startswith("traces_")]
        h.tool_subset = kept or core
    trace.manager(ctx, step="plan", hypotheses=[h.model_dump() for h in plan_obj.hypotheses],
                  reasoning=reasoning, usage=usage)
    return plan_obj


def synthesize(client: openai.OpenAI, preset: ModelPreset, *, incident: str, verdicts: list[dict],
               dep_graph: str, trace: TraceLogger, ctx: TraceContext) -> Synthesis:
    messages = [
        {"role": "system", "content": _SYNTH_SYSTEM},
        {"role": "user", "content": (
            f"INCIDENT\n{incident}\n\nDEPENDENCY GRAPH (origin-first)\n{dep_graph}\n\n"
            f"WORKER VERDICTS\n{json.dumps(verdicts, indent=2)}")},
    ]
    synth, usage, reasoning = structured(client, preset, messages, Synthesis)
    if synth.ranked_services and synth.root_cause_service != synth.ranked_services[0]:
        synth.root_cause_service = synth.ranked_services[0]
    trace.manager(ctx, step="synthesize", result=synth.model_dump(), reasoning=reasoning, usage=usage)
    return synth
