"""Investigator subagents: isolated workers the manager fans out to.

Each investigator runs the shared hooked loop (sentinel.agent.loop) in a fresh
context with a scoped, recursion-free tool set (the analysis namespaces plus its
own terminal tool; the investigate orchestration tools are stripped). It returns a
typed result the manager reconciles. Workers run on a cheaper model (Sonnet) than
the manager (Opus).
"""

from __future__ import annotations

import anthropic

from sentinel.agent.events import EventSink
from sentinel.agent.hooks import HookRunner, RunContext, default_hooks
from sentinel.agent.loop import LoopResult, run_loop
from sentinel.registry import REGISTRY
from sentinel.tools.models import ChangeVerdict, ServiceFinding
from sentinel.tools.store import TelemetryStore

_ANALYSIS_NAMESPACES = {"traces", "metrics", "logs", "changes", "correlate", "topology", "hypothesis"}
_WORKER_TERMINALS = {"report_finding", "report_change_verdict"}

INVESTIGATOR_SYSTEM = (
    "You are an investigator subagent in an SRE incident response. You are assigned ONE service and a "
    "scoped set of read-only telemetry tools. You have no memory of the manager's work; rely only on the tools.\n\n"
    "Decide whether THIS service is the fault ORIGIN: its own work failed (its own server spans error, or its own "
    "p95 latency / CPU cores rose after onset), as opposed to a victim that merely calls a failing dependency. "
    "Then find the recent change on this service most likely responsible by comparing each change's diff_touches to "
    "the observed failure. Reason briefly before each tool call and keep calls purposeful.\n\n"
    "When done, call report_finding exactly once with a complete ServiceFinding: set is_origin truthfully, and when "
    "it is true include a fault_type and at least one evidence line; name the suspect change; give a calibrated confidence."
)

CHANGE_INVESTIGATOR_SYSTEM = (
    "You are an investigator subagent assessing whether ONE change caused an SRE incident. You have read-only tools "
    "(changes, traces, metrics, correlate). You have no memory of the manager's work.\n\n"
    "You are told the OBSERVED fault. Judge whether this change plausibly causes THAT fault: compare its diff_touches "
    "and summary to the fault, and confirm its time precedes onset (a cause precedes its symptom). The fault may be an "
    "error, a latency/GC slowdown, or CPU/resource saturation, and may have NO error spans, so do not assume errors; "
    "verify the actual signal with metrics/traces when needed. Be concise.\n\n"
    "When done, call report_change_verdict exactly once with is_culprit, a calibrated confidence, and your reason."
)


def investigator_tool_names() -> set[str]:
    return {s.name for s in REGISTRY.specs() if s.namespace in _ANALYSIS_NAMESPACES} | {"report_finding"}


def change_investigator_tool_names() -> set[str]:
    return {
        s.name for s in REGISTRY.specs() if s.namespace in {"changes", "traces", "metrics", "correlate"}
    } | {"report_change_verdict"}


def manager_tool_names() -> set[str]:
    return {s.name for s in REGISTRY.specs() if s.name not in _WORKER_TERMINALS}


def run_investigator(
    client: anthropic.Anthropic,
    service: str,
    store: TelemetryStore,
    *,
    model: str,
    effort: str,
    max_iters: int,
    max_tokens: int,
    output_budget: int,
    max_tool_calls: int,
    events: EventSink | None = None,
) -> tuple[ServiceFinding, LoopResult]:
    ctx = RunContext(store=store, agent_id=f"investigator:{service}", max_tool_calls=max_tool_calls)
    goal = (
        f"Investigate the service '{service}'. Determine whether it is the fault origin and which of its recent "
        f"changes is the culprit, then submit a ServiceFinding."
    )
    result = run_loop(
        client,
        model=model,
        effort=effort,
        system_prompt=INVESTIGATOR_SYSTEM,
        tools_schema=REGISTRY.anthropic_schemas(names=investigator_tool_names()),
        initial_user=goal,
        terminal_tool="report_finding",
        terminal_model=ServiceFinding,
        dispatch=lambda name, tool_input: REGISTRY.dispatch(name, tool_input, store),
        hooks=HookRunner(default_hooks()),
        ctx=ctx,
        max_iters=max_iters,
        max_tokens=max_tokens,
        output_budget=output_budget,
        events=events,
    )
    if result.terminal is not None:
        finding = ServiceFinding.model_validate(result.terminal)
    else:
        finding = ServiceFinding(
            service=service, is_origin=False, confidence=0.0,
            evidence=[f"investigator for {service} returned no finding within budget ({result.stop})"],
        )
    return finding, result


def run_change_investigator(
    client: anthropic.Anthropic,
    change_id: str,
    service: str,
    incident_summary: str,
    store: TelemetryStore,
    *,
    model: str,
    effort: str,
    max_iters: int,
    max_tokens: int,
    output_budget: int,
    max_tool_calls: int,
    events: EventSink | None = None,
) -> tuple[ChangeVerdict, LoopResult]:
    ctx = RunContext(store=store, agent_id=f"change:{change_id}", max_tool_calls=max_tool_calls)
    goal = (
        f"Assess whether change '{change_id}' on service '{service}' is the culprit. "
        f"Observed fault: {incident_summary}."
    )
    result = run_loop(
        client,
        model=model,
        effort=effort,
        system_prompt=CHANGE_INVESTIGATOR_SYSTEM,
        tools_schema=REGISTRY.anthropic_schemas(names=change_investigator_tool_names()),
        initial_user=goal,
        terminal_tool="report_change_verdict",
        terminal_model=ChangeVerdict,
        dispatch=lambda name, tool_input: REGISTRY.dispatch(name, tool_input, store),
        hooks=HookRunner(default_hooks()),
        ctx=ctx,
        max_iters=max_iters,
        max_tokens=max_tokens,
        output_budget=output_budget,
        events=events,
    )
    if result.terminal is not None:
        verdict = ChangeVerdict.model_validate(result.terminal)
    else:
        verdict = ChangeVerdict(change_id=change_id, is_culprit=False, confidence=0.0, reason=f"no verdict within budget ({result.stop})")
    return verdict, result
