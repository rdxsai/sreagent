"""The agentic eval loop: hand the model the tool registry and one incident,
let it choose and call tools over a recorded fixture, capture the structured
report it submits, and record per-task metrics (tool calls, tokens, errors).

Credit discipline is built in: a small per-turn max_tokens, a per-task output
token budget that aborts the loop, a max-iteration guard, prompt caching on the
stable tool+system prefix, and full usage accounting. The model is
claude-opus-4-6 in normal mode (no adaptive thinking).
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import anthropic
import structlog
from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from sentinel.registry import REGISTRY
from sentinel.tools.models import RootCauseReport
from sentinel.tools.report import REPORT_TOOL
from sentinel.tools.store import FixtureStore
from sentinel_tool_eval.grader import grade, load_truth
from sentinel_tool_eval.tasks import Scenario, build_task_prompt

log = structlog.get_logger("sentinel_tool_eval")

DEFAULT_MODEL = os.environ.get("SENTINEL_EVAL_MODEL", "claude-opus-4-6")
DEFAULT_EFFORT = os.environ.get("SENTINEL_EVAL_EFFORT", "medium")
DEFAULT_MAX_ITERS = int(os.environ.get("SENTINEL_EVAL_MAX_ITERS", "18"))
DEFAULT_OUTPUT_BUDGET = int(os.environ.get("SENTINEL_EVAL_OUTPUT_BUDGET", "50000"))
DEFAULT_MAX_TOKENS = int(os.environ.get("SENTINEL_EVAL_MAX_TOKENS", "6000"))
_MIN_INTERVAL = float(os.environ.get("SENTINEL_EVAL_MIN_INTERVAL", "0"))

# opus-4-6 pricing per 1M tokens
_PRICE = {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25}

SYSTEM_PROMPT = (
    "You are Sentinel, an autonomous SRE incident-response agent. You receive an "
    "incident alert and read-only access to recorded telemetry (metrics, logs, "
    "traces, recent changes) through tools.\n\n"
    "Investigate methodically: orient with the service dependency graph, find the "
    "true onset, locate where the failure originates, attribute it to a single "
    "service or to a caller->callee dependency, correlate it to the recent change "
    "that caused it, and rule out the other changes. Attribution should be based on "
    "trace evidence, not on a single error-rate number. Reason briefly before each "
    "tool call and keep calls purposeful.\n\n"
    "When you are confident, call report_root_cause exactly once with your "
    "structured conclusion. Immediately before that call, include a "
    "<feedback>...</feedback> block giving frank notes on the tools themselves: "
    "which were essential, which were confusing, which returned too much or too "
    "little, and anything missing. Be token-efficient."
)


class _RateLimiter:
    def __init__(self, min_interval: float) -> None:
        self._min = min_interval
        self._last = 0.0

    def wait(self) -> None:
        if self._min <= 0:
            return
        gap = time.monotonic() - self._last
        if gap < self._min:
            time.sleep(self._min - gap)
        self._last = time.monotonic()


@dataclass
class TaskResult:
    scenario_id: str
    report: dict[str, Any] | None
    grade: dict[str, Any]
    calls: list[str] = field(default_factory=list)
    tool_errors: int = 0
    iterations: int = 0
    stop: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    feedback: str = ""

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def est_cost_usd(self) -> float:
        u = self.usage
        return (
            u.get("input", 0) * _PRICE["input"]
            + u.get("output", 0) * _PRICE["output"]
            + u.get("cache_read", 0) * _PRICE["cache_read"]
            + u.get("cache_write", 0) * _PRICE["cache_write"]
        ) / 1_000_000


@retry(
    retry=retry_if_exception_type(
        (
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
        )
    ),
    wait=wait_exponential(multiplier=1, max=20),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _create(client: anthropic.Anthropic, **kwargs: Any) -> Any:
    return client.messages.create(**kwargs)


def _extract_feedback(text: str) -> str:
    start = text.find("<feedback>")
    end = text.find("</feedback>")
    if start != -1 and end != -1 and end > start:
        return text[start + len("<feedback>") : end].strip()
    return ""


def run_task(
    client: anthropic.Anthropic,
    scenario: Scenario,
    *,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    max_iters: int = DEFAULT_MAX_ITERS,
    output_budget: int = DEFAULT_OUTPUT_BUDGET,
) -> TaskResult:
    store = FixtureStore(scenario.public_dir)
    truth = load_truth(scenario.truth_path)
    tools = REGISTRY.anthropic_schemas()
    system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": build_task_prompt(scenario)}
    ]
    limiter = _RateLimiter(_MIN_INTERVAL)
    usage: Counter[str] = Counter()
    calls: list[str] = []
    feedback_parts: list[str] = []
    report: dict[str, Any] | None = None
    tool_errors = 0
    stop = "max_iters"
    iterations = 0

    create_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "system": system,
        "tools": tools,
    }
    if effort and effort != "default":
        create_kwargs["output_config"] = {"effort": effort}

    for iterations in range(1, max_iters + 1):
        limiter.wait()
        resp = _create(client, messages=messages, **create_kwargs)
        u = resp.usage
        usage["input"] += getattr(u, "input_tokens", 0) or 0
        usage["output"] += getattr(u, "output_tokens", 0) or 0
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0

        for block in resp.content:
            if getattr(block, "type", None) == "text":
                fb = _extract_feedback(block.text)
                if fb:
                    feedback_parts.append(fb)

        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            stop = resp.stop_reason or "end_turn"
            break

        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        results: list[dict[str, Any]] = []
        done = False
        for tu in tool_uses:
            calls.append(tu.name)
            if tu.name == REPORT_TOOL:
                try:
                    parsed = RootCauseReport.model_validate(tu.input)
                    report = parsed.model_dump(mode="json")
                    content = json.dumps({"accepted": True})
                    done = True
                except ValidationError as exc:
                    tool_errors += 1
                    content = json.dumps(
                        {"error": {"code": "invalid_input", "message": str(exc)[:500]}}
                    )
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content})
            else:
                out = REGISTRY.dispatch(tu.name, tu.input, store)
                is_error = isinstance(out, dict) and "error" in out
                if is_error:
                    tool_errors += 1
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(out),
                        "is_error": is_error,
                    }
                )
            log.debug("tool_call", scenario=scenario.id, tool=tu.name)

        messages.append({"role": "user", "content": results})

        if done:
            stop = "reported"
            break
        if usage["output"] > output_budget:
            stop = "budget_exceeded"
            break

    return TaskResult(
        scenario_id=scenario.id,
        report=report,
        grade=grade(report, truth),
        calls=calls,
        tool_errors=tool_errors,
        iterations=iterations,
        stop=stop,
        usage=dict(usage),
        feedback="\n\n".join(feedback_parts),
    )
