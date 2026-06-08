"""Eval harness: run the manager + investigator agent on one scenario and grade it.

A thin wrapper over sentinel.agent.manager.run_manager. The hooked loop lives in
sentinel.agent.loop; the orchestration in sentinel.agent.manager. Credit
discipline: per-turn max_tokens, a per-run output budget and a tool-call budget
(the BudgetGuard hook), prompt caching on the stable prefix (in run_loop), and
usage accounted separately for the Opus manager and the Sonnet workers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import anthropic
import structlog

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
    calls: list[str] = field(default_factory=list)
    worker_calls: list[str] = field(default_factory=list)
    tool_errors: int = 0
    iterations: int = 0
    stop: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    worker_usage: dict[str, int] = field(default_factory=dict)
    feedback: str = ""
    denials: int = 0
    subagents: int = 0
    findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def call_count(self) -> int:
        return len(self.calls) + len(self.worker_calls)

    @property
    def est_cost_usd(self) -> float:
        return _cost(self.usage, _OPUS) + _cost(self.worker_usage, _SONNET)


def run_task(
    client: anthropic.Anthropic,
    scenario: Scenario,
    *,
    manager_model: str = DEFAULT_MODEL,
    worker_model: str = DEFAULT_WORKER_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> TaskResult:
    store = FixtureStore(scenario.public_dir)
    truth = load_truth(scenario.truth_path)
    result = run_manager(
        client,
        store,
        build_task_prompt(scenario),
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
        scenario_id=scenario.id,
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
