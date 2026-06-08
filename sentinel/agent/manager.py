"""The manager agent: triage, parallel fan-out to investigators, reconcile, report.

The manager runs the shared loop on the full tool set (minus the workers' terminal
tools). Its dispatch intercepts the investigate_* tools and runs investigator
subagents instead of REGISTRY.dispatch, because spawning a worker needs the LLM
client and worker model the runtime holds. investigate_parallel fans out across
candidates with a thread pool (the loop is synchronous; the Anthropic client and
the read-only store are safe to share across threads). Each returned finding is
validated through the SubagentStop hook before the manager reconciles it.
"""

from __future__ import annotations

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import anthropic

from sentinel.agent.events import EventSink
from sentinel.agent.hooks import HookRunner, RunContext, default_hooks
from sentinel.agent.investigator import (
    manager_tool_names,
    run_change_investigator,
    run_investigator,
)
from sentinel.agent.loop import LoopResult, run_loop
from sentinel.observability import get_logger
from sentinel.registry import REGISTRY
from sentinel.tools.models import RootCauseReport
from sentinel.tools.store import TelemetryStore

log = get_logger("sentinel.manager")

MANAGER_SYSTEM = (
    "You are Sentinel, the manager agent for autonomous SRE incident response. You coordinate isolated "
    "investigator subagents over recorded, read-only telemetry.\n\n"
    "Work in three phases:\n"
    "1) TRIAGE yourself (cheaply, no subagents): orient with the dependency graph and a metrics snapshot, find "
    "the onset, and localize where the failure likely originates (error origin, latency origin, or resource "
    "saturation). From that, pick a SHORT list of 2-4 candidate services in the blast radius. Do NOT investigate "
    "every service.\n"
    "2) DELEGATE: call investigate_parallel with those candidates. Each spawns an isolated subagent that returns a "
    "ServiceFinding (is_origin, fault_type, suspect change, evidence). Use investigate_service for one targeted "
    "follow-up, or investigate_change to confirm a suspect change, if needed.\n"
    "3) RECONCILE: from the findings choose the origin (the service whose own work failed, not a victim that calls "
    "a failing dependency), then confirm the culprit change and call report_root_cause exactly once.\n\n"
    "When picking the culprit change, trust an investigator's content-based suspect: match the change's diff_touches "
    "to the observed failure. A same-service change that is NEARER to onset is frequently a decoy, proximity is not "
    "causation, so do not pick a change by timing alone over one whose touched area actually explains the fault. "
    "Attribution is trace-based, not a single error-rate number. Immediately before report_root_cause, include a "
    "<feedback>...</feedback> block with frank notes on the tools and the orchestration. Be token-efficient."
)


@dataclass
class ManagerResult:
    report: dict[str, Any] | None
    manager_loop: LoopResult
    worker_usage: dict[str, int] = field(default_factory=dict)
    worker_calls: list[str] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    subagents: int = 0
    rejected_findings: int = 0
    trace: list[dict[str, Any]] = field(default_factory=list)


def run_manager(
    client: anthropic.Anthropic,
    store: TelemetryStore,
    initial_user: str,
    *,
    manager_model: str,
    worker_model: str,
    effort: str,
    manager_max_iters: int,
    worker_max_iters: int,
    max_tokens: int,
    output_budget: int,
    manager_max_tool_calls: int,
    worker_max_tool_calls: int,
    events: EventSink | None = None,
) -> ManagerResult:
    worker_usage: Counter[str] = Counter()
    worker_calls: list[str] = []
    worker_events: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    lock = threading.Lock()
    counters = {"subagents": 0, "rejected": 0}

    manager_hooks = HookRunner(default_hooks())
    manager_ctx = RunContext(store=store, agent_id="manager", max_tool_calls=manager_max_tool_calls)

    def _accumulate(loop_result: LoopResult) -> None:
        with lock:
            for key, value in loop_result.usage.items():
                worker_usage[key] += value
            worker_calls.extend(loop_result.calls)
            worker_events.extend(loop_result.events)
            counters["subagents"] += 1

    def _investigate_service(service: str) -> dict[str, Any]:
        log.info("spawn_investigator", service=service)
        if events is not None:
            events.emit("subagent_spawn", agent="manager", service=service)
        finding, loop_result = run_investigator(
            client, service, store,
            model=worker_model, effort=effort, max_iters=worker_max_iters,
            max_tokens=max_tokens, output_budget=output_budget, max_tool_calls=worker_max_tool_calls,
            events=events,
        )
        _accumulate(loop_result)
        verdict = manager_hooks.subagent_stop(f"investigator:{service}", finding, manager_ctx)
        with lock:
            data = finding.model_dump(mode="json")
            if not verdict.accept:
                counters["rejected"] += 1
                data["_validation"] = verdict.feedback
            findings.append(data)
        if events is not None:
            events.emit("finding", agent="manager", service=service, is_origin=finding.is_origin,
                        fault_type=finding.fault_type, confidence=finding.confidence,
                        suspect_change_id=finding.suspect_change_id)
            events.emit("subagent_done", agent="manager", service=service)
        return finding.model_dump(mode="json")

    def dispatch(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        if name == "investigate_service":
            return _investigate_service(tool_input["service"])
        if name == "investigate_parallel":
            services = tool_input["services"]
            with ThreadPoolExecutor(max_workers=min(5, max(1, len(services)))) as pool:
                results = list(pool.map(_investigate_service, services))
            return {"findings": results}
        if name == "investigate_change":
            if events is not None:
                events.emit("subagent_spawn", agent="manager", service=f"change:{tool_input['change_id']}")
            verdict, loop_result = run_change_investigator(
                client, tool_input["change_id"], tool_input["service"],
                tool_input.get("incident_summary", "the observed incident"), store,
                model=worker_model, effort=effort, max_iters=worker_max_iters,
                max_tokens=max_tokens, output_budget=output_budget, max_tool_calls=worker_max_tool_calls,
                events=events,
            )
            _accumulate(loop_result)
            return verdict.model_dump(mode="json")
        return REGISTRY.dispatch(name, tool_input, store)

    manager_loop = run_loop(
        client,
        model=manager_model,
        effort=effort,
        system_prompt=MANAGER_SYSTEM,
        tools_schema=REGISTRY.anthropic_schemas(names=manager_tool_names()),
        initial_user=initial_user,
        terminal_tool="report_root_cause",
        terminal_model=RootCauseReport,
        dispatch=dispatch,
        hooks=manager_hooks,
        ctx=manager_ctx,
        max_iters=manager_max_iters,
        max_tokens=max_tokens,
        output_budget=output_budget,
        events=events,
    )

    return ManagerResult(
        report=manager_loop.terminal,
        manager_loop=manager_loop,
        worker_usage=dict(worker_usage),
        worker_calls=worker_calls,
        findings=findings,
        subagents=counters["subagents"],
        rejected_findings=counters["rejected"],
        trace=manager_loop.events + worker_events,
    )
