"""Eval harness: run the manager + investigator agent on one scenario and grade it.

A thin wrapper over sentinel.agent.manager.run_manager. The hooked loop lives in
sentinel.agent.loop; the orchestration in sentinel.agent.manager. Credit
discipline: per-turn max_tokens, a per-run output budget and a tool-call budget
(the BudgetGuard hook), prompt caching on the stable prefix (in run_loop), and
usage accounted separately for the Opus manager and the Sonnet workers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import anthropic
import structlog

from sentinel.agent.codeagent import code_agent_tools_schema, run_code_agent
from sentinel.agent.manager import run_manager
from sentinel.tools.store import FixtureStore
from sentinel_tool_eval.grader import grade, load_truth
from sentinel_tool_eval.tasks import Scenario, build_task_prompt

log = structlog.get_logger("sentinel_tool_eval")

DEFAULT_MODEL = os.environ.get("SENTINEL_EVAL_MODEL", "claude-opus-4-6")
DEFAULT_WORKER_MODEL = os.environ.get("SENTINEL_EVAL_WORKER_MODEL", "claude-sonnet-4-6")
DEFAULT_EFFORT = os.environ.get("SENTINEL_EVAL_EFFORT", "medium")
DEFAULT_MANAGER_MAX_ITERS = int(os.environ.get("SENTINEL_EVAL_MAX_ITERS", "20"))
DEFAULT_WORKER_MAX_ITERS = int(os.environ.get("SENTINEL_EVAL_WORKER_MAX_ITERS", "14"))
DEFAULT_OUTPUT_BUDGET = int(os.environ.get("SENTINEL_EVAL_OUTPUT_BUDGET", "80000"))
DEFAULT_MAX_TOKENS = int(os.environ.get("SENTINEL_EVAL_MAX_TOKENS", "6000"))
DEFAULT_MANAGER_MAX_TOOL_CALLS = int(os.environ.get("SENTINEL_EVAL_MAX_TOOL_CALLS", "40"))
DEFAULT_WORKER_MAX_TOOL_CALLS = int(os.environ.get("SENTINEL_EVAL_WORKER_MAX_TOOL_CALLS", "20"))
DEFAULT_TOOL_MODE = os.environ.get("SENTINEL_EVAL_TOOL_MODE", os.environ.get("SENTINEL_TOOL_MODE", "native"))

# per-1M-token pricing
_OPUS = {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25}
_SONNET = {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}


def _cost(usage: dict[str, int], price: dict[str, float]) -> float:
    return (
        usage.get("input", 0) * price["input"]
        + usage.get("output", 0) * price["output"]
        + usage.get("cache_read", 0) * price["cache_read"]
        + usage.get("cache_write", 0) * price["cache_write"]
    ) / 1_000_000


@dataclass
class TaskResult:
    scenario_id: str
    report: dict[str, Any] | None
    grade: dict[str, Any]
    tool_mode: str = "native"
    manager_exposed_tools: int = 0
    manager_schema_chars: int = 0
    worker_exposed_tools: int = 0
    worker_schema_chars: int = 0
    calls: list[str] = field(default_factory=list)
    worker_calls: list[str] = field(default_factory=list)
    internal_calls: list[str] = field(default_factory=list)
    tool_errors: int = 0
    iterations: int = 0
    stop: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    worker_usage: dict[str, int] = field(default_factory=dict)
    feedback: str = ""
    denials: int = 0
    subagents: int = 0
    findings: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    @property
    def call_count(self) -> int:
        return len(self.calls) + len(self.worker_calls)

    @property
    def internal_call_count(self) -> int:
        return len(self.internal_calls)

    @property
    def est_cost_usd(self) -> float:
        return _cost(self.usage, _OPUS) + _cost(self.worker_usage, _SONNET)


def _build_code_result(scenario_id, result, grade_dict) -> TaskResult:
    schema = code_agent_tools_schema()
    loop = result.loop
    return TaskResult(
        scenario_id=scenario_id,
        report=result.report,
        grade=grade_dict,
        tool_mode="code",
        manager_exposed_tools=len(schema),
        manager_schema_chars=len(json.dumps(schema)),
        worker_exposed_tools=0,
        worker_schema_chars=0,
        calls=loop.calls,
        internal_calls=result.internal_calls,
        trace=result.internal_events,
        tool_errors=loop.tool_errors,
        iterations=loop.iterations,
        stop=loop.stop,
        usage=loop.usage,
        feedback=loop.feedback,
        denials=loop.denials,
        subagents=0,
    )


def run_task_with(
    client: anthropic.Anthropic,
    store,
    truth,
    prompt: str,
    scenario_id: str,
    *,
    manager_model: str = DEFAULT_MODEL,
    worker_model: str = DEFAULT_WORKER_MODEL,
    effort: str = DEFAULT_EFFORT,
    tool_mode: str = DEFAULT_TOOL_MODE,
) -> TaskResult:
    if tool_mode == "code":
        backend = os.environ.get("SENTINEL_CODE_BACKEND", "docker")
        result = run_code_agent(
            client, store, prompt,
            model=manager_model, effort=effort,
            max_iters=DEFAULT_MANAGER_MAX_ITERS, max_tokens=DEFAULT_MAX_TOKENS,
            output_budget=DEFAULT_OUTPUT_BUDGET, max_tool_calls=DEFAULT_MANAGER_MAX_TOOL_CALLS,
            executor_backend=backend,
        )
        return _build_code_result(scenario_id, result, grade(result.report, truth))
    result = run_manager(
        client,
        store,
        prompt,
        manager_model=manager_model,
        worker_model=worker_model,
        effort=effort,
        manager_max_iters=DEFAULT_MANAGER_MAX_ITERS,
        worker_max_iters=DEFAULT_WORKER_MAX_ITERS,
        max_tokens=DEFAULT_MAX_TOKENS,
        output_budget=DEFAULT_OUTPUT_BUDGET,
        manager_max_tool_calls=DEFAULT_MANAGER_MAX_TOOL_CALLS,
        worker_max_tool_calls=DEFAULT_WORKER_MAX_TOOL_CALLS,
    )
    loop = result.manager_loop
    return TaskResult(
        scenario_id=scenario_id,
        report=result.report,
        grade=grade(result.report, truth),
        calls=loop.calls,
        worker_calls=result.worker_calls,
        tool_errors=loop.tool_errors,
        iterations=loop.iterations,
        stop=loop.stop,
        usage=loop.usage,
        worker_usage=result.worker_usage,
        feedback=loop.feedback,
        denials=loop.denials,
        subagents=result.subagents,
        findings=result.findings,
    )


def run_task(
    client: anthropic.Anthropic,
    scenario: Scenario,
    *,
    manager_model: str = DEFAULT_MODEL,
    worker_model: str = DEFAULT_WORKER_MODEL,
    effort: str = DEFAULT_EFFORT,
    tool_mode: str = DEFAULT_TOOL_MODE,
) -> TaskResult:
    return run_task_with(
        client,
        FixtureStore(scenario.public_dir),
        load_truth(scenario.truth_path),
        build_task_prompt(scenario),
        scenario.id,
        manager_model=manager_model,
        worker_model=worker_model,
        effort=effort,
        tool_mode=tool_mode,
    )
