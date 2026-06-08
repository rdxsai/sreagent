"""The generic hooked agentic loop, shared by the manager and the investigators.

Both tiers run the same loop: call the model, run each requested tool through the
hooks (PreToolUse deny/modify, then dispatch, then PostToolUse redact/observe),
and stop when the model submits its terminal tool (the manager's report or an
investigator's finding) or hits a guard. The only differences between tiers are
the model, system prompt, exposed tools, terminal contract, and the dispatch
function, all passed in.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from sentinel.agent.hooks import HookRunner, RunContext, ToolCall

Dispatch = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass
class LoopResult:
    terminal: dict[str, Any] | None = None
    calls: list[str] = field(default_factory=list)
    tool_errors: int = 0
    denials: int = 0
    iterations: int = 0
    stop: str = "max_iters"
    usage: dict[str, int] = field(default_factory=dict)
    feedback: str = ""


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
def create_message(client: anthropic.Anthropic, **kwargs: Any) -> Any:
    return client.messages.create(**kwargs)


def extract_feedback(text: str) -> str:
    start = text.find("<feedback>")
    end = text.find("</feedback>")
    if start != -1 and end != -1 and end > start:
        return text[start + len("<feedback>") : end].strip()
    return ""


def run_loop(
    client: anthropic.Anthropic,
    *,
    model: str,
    effort: str,
    system_prompt: str,
    tools_schema: list[dict[str, Any]],
    initial_user: str,
    terminal_tool: str,
    terminal_model: type[BaseModel],
    dispatch: Dispatch,
    hooks: HookRunner,
    ctx: RunContext,
    max_iters: int,
    max_tokens: int,
    output_budget: int,
) -> LoopResult:
    system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict[str, Any]] = [{"role": "user", "content": initial_user}]
    usage: Counter[str] = Counter()
    calls: list[str] = []
    feedback_parts: list[str] = []
    terminal: dict[str, Any] | None = None
    tool_errors = 0
    denials = 0
    stop = "max_iters"
    iterations = 0

    create_kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "tools": tools_schema}
    if effort and effort != "default":
        create_kwargs["output_config"] = {"effort": effort}

    for iterations in range(1, max_iters + 1):
        resp = create_message(client, messages=messages, **create_kwargs)
        u = resp.usage
        usage["input"] += getattr(u, "input_tokens", 0) or 0
        usage["output"] += getattr(u, "output_tokens", 0) or 0
        usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0

        for block in resp.content:
            if getattr(block, "type", None) == "text":
                fb = extract_feedback(block.text)
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
            call = ToolCall(name=tu.name, input=tu.input if isinstance(tu.input, dict) else {})
            decision = hooks.pre_tool_use(call, ctx)
            if decision.action == "deny":
                denials += 1
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps({"error": {"code": "blocked_by_hook", "message": decision.reason}}),
                        "is_error": True,
                    }
                )
                continue
            tool_input = (
                decision.modified_input
                if decision.action == "modify" and decision.modified_input is not None
                else call.input
            )
            if tu.name == terminal_tool:
                try:
                    parsed = terminal_model.model_validate(tool_input)
                    terminal = parsed.model_dump(mode="json")
                    content = json.dumps({"accepted": True})
                    done = True
                except ValidationError as exc:
                    tool_errors += 1
                    content = json.dumps({"error": {"code": "invalid_input", "message": str(exc)[:500]}})
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content})
            else:
                out = dispatch(tu.name, tool_input)
                out = hooks.post_tool_use(call, out, ctx)
                ctx.tool_calls += 1
                is_error = isinstance(out, dict) and "error" in out
                if is_error:
                    tool_errors += 1
                results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(out), "is_error": is_error}
                )

        messages.append({"role": "user", "content": results})
        if done:
            stop = "reported"
            break
        if usage["output"] > output_budget:
            stop = "budget_exceeded"
            break

    return LoopResult(
        terminal=terminal,
        calls=calls,
        tool_errors=tool_errors,
        denials=denials,
        iterations=iterations,
        stop=stop,
        usage=dict(usage),
        feedback="\n\n".join(feedback_parts),
    )
