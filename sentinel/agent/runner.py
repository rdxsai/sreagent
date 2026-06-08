"""Production entry point for the agent: run the manager on a telemetry store.

This is the seam the API (and any other trigger) calls to kick off an
investigation. It builds the incident prompt from a symptom and the firing alert
names, runs the manager/investigator orchestration, and returns the result. Model
and budget knobs come from the environment so the same code runs in the eval and
behind FastAPI.
"""

from __future__ import annotations

import os

import anthropic

from sentinel.agent.events import EventSink
from sentinel.agent.manager import ManagerResult, run_manager
from sentinel.observability import get_logger
from sentinel.tools.store import TelemetryStore

log = get_logger("sentinel.runner")


def _defaults() -> dict[str, object]:
    return {
        "manager_model": os.environ.get("SENTINEL_MANAGER_MODEL", "claude-opus-4-6"),
        "worker_model": os.environ.get("SENTINEL_WORKER_MODEL", "claude-sonnet-4-6"),
        "effort": os.environ.get("SENTINEL_EFFORT", "medium"),
        "manager_max_iters": int(os.environ.get("SENTINEL_MANAGER_MAX_ITERS", "20")),
        "worker_max_iters": int(os.environ.get("SENTINEL_WORKER_MAX_ITERS", "14")),
        "max_tokens": int(os.environ.get("SENTINEL_MAX_TOKENS", "6000")),
        "output_budget": int(os.environ.get("SENTINEL_OUTPUT_BUDGET", "80000")),
        "manager_max_tool_calls": int(os.environ.get("SENTINEL_MANAGER_MAX_TOOL_CALLS", "40")),
        "worker_max_tool_calls": int(os.environ.get("SENTINEL_WORKER_MAX_TOOL_CALLS", "20")),
    }


def build_incident_prompt(symptom: str, alertnames: list[str] | None = None) -> str:
    lines = ["You are paged for an incident in a microservices application.", "", f"Symptom: {symptom}"]
    if alertnames:
        lines += ["", "Firing alerts: " + ", ".join(alertnames)]
    lines += ["", "Investigate the recorded telemetry and report the root cause."]
    return "\n".join(lines)


def investigate(
    client: anthropic.Anthropic,
    store: TelemetryStore,
    symptom: str,
    alertnames: list[str] | None = None,
    events: EventSink | None = None,
) -> ManagerResult:
    log.info("investigation_start", symptom=symptom, alerts=alertnames or [])
    result = run_manager(client, store, build_incident_prompt(symptom, alertnames), events=events, **_defaults())
    log.info("investigation_complete", subagents=result.subagents, reported=bool(result.report))
    return result
