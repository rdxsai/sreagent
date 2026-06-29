"""The single code-mode agent: one loop, two native tools, one sandbox.

The model writes Python against a generated client (the 50 SRE tools), executed
in an isolated sandbox. Proxy calls land on an RpcHandler that runs the inner
hook plane (SealGuard, BudgetGuard, Observer) against one RunContext shared for
the whole investigation, so the proxied-call budget and the trace accumulate
across every run_code turn. The outer loop gates only run_code and the terminal
report (ReportGate + DecoyCompletenessGate).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import anthropic

from sentinel.agent.events import EventSink
from sentinel.agent.hooks import (
    BudgetGuard,
    DecoyCompletenessGate,
    HookRunner,
    Observer,
    ReportGate,
    RunContext,
    SealGuard,
)
from sentinel.agent.loop import LoopResult, run_loop
from sentinel.registry import REGISTRY
from sentinel.sandbox.client_gen import (
    code_tool_specs,
    generate_client_digest,
    generate_client_source,
)
from sentinel.sandbox.executor import make_executor
from sentinel.sandbox.presentation import present
from sentinel.sandbox.rpc import RpcHandler
from sentinel.tools.models import RootCauseReport
from sentinel.tools.store import TelemetryStore

RUN_CODE_TOOL = "run_code"

_SYSTEM_HEAD = (
    "You are Sentinel, an autonomous SRE incident responder. You investigate recorded, read-only "
    "telemetry by WRITING PYTHON, not by calling many tools. You have two tools:\n"
    "1) run_code(code): execute Python in an isolated sandbox. The SRE API is preloaded as namespace "
    "objects; call its methods, compose results in code (loops, conditionals, slicing), and print() only "
    "the concise facts you need. Only stdout returns to you. There is no network and no file access; the "
    "API is your only capability. Kernel state persists across run_code calls.\n"
    "2) report_root_cause(...): submit the final report exactly once.\n\n"
    "Method: orient (topology, a metrics snapshot, onset), localize the origin (the service whose own work "
    "failed, not a victim of a failing dependency), then find the culprit change by matching its diff_touches "
    "to the observed fault. Prefer one script that chains dependent steps over many tiny scripts. Before "
    "reporting, rule out EVERY other known change. Immediately before report_root_cause, include a "
    "<feedback>...</feedback> block with frank notes on the API and the code-mode surface.\n\n"
)


def run_code_schema() -> dict[str, Any]:
    return {
        "name": RUN_CODE_TOOL,
        "description": (
            "Execute Python in an isolated sandbox to investigate the incident. The SRE telemetry API is "
            "preloaded as namespace objects (see the system prompt for signatures). Compose results in code "
            "and print() the facts you need; only stdout is returned. No network, no filesystem."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source to execute."}},
            "required": ["code"],
        },
    }


def code_agent_tools_schema() -> list[dict[str, Any]]:
    return [run_code_schema()] + REGISTRY.anthropic_schemas(names={"report_root_cause"})


def _system_prompt() -> str:
    return _SYSTEM_HEAD + generate_client_digest(code_tool_specs())


@dataclass
class CodeAgentResult:
    report: dict[str, Any] | None
    loop: LoopResult
    internal_calls: list[str] = field(default_factory=list)
    internal_events: list[dict[str, Any]] = field(default_factory=list)


def run_code_agent(
    client: anthropic.Anthropic,
    store: TelemetryStore,
    initial_user: str,
    *,
    model: str,
    effort: str,
    max_iters: int,
    max_tokens: int,
    output_budget: int,
    max_tool_calls: int,
    executor_backend: str = "local",
    timeout_s: float = 20.0,
    events: EventSink | None = None,
) -> CodeAgentResult:
    # inner plane: one shared context for the whole run (budget + trace accumulate)
    inner_ctx = RunContext(store=store, agent_id="code", max_tool_calls=max_tool_calls)
    inner_hooks = HookRunner([SealGuard(), BudgetGuard(), Observer()])
    allowed = {s.name for s in code_tool_specs()}
    handler = RpcHandler(store, inner_hooks, inner_ctx, allowed)
    client_source = generate_client_source(code_tool_specs())
    executor = make_executor(handler, client_source, backend=executor_backend, timeout_s=timeout_s)

    # outer plane: gate run_code and the terminal report
    outer_ctx = RunContext(store=store, agent_id="code", max_tool_calls=max_iters * 2)
    outer_hooks = HookRunner([SealGuard(), ReportGate(), DecoyCompletenessGate(), Observer()])

    def dispatch(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        if name == RUN_CODE_TOOL:
            code = tool_input.get("code")
            if not isinstance(code, str):
                return {"output": "[stderr]\n[error] run_code: code must be a string\n[exit:2 | 0ms]"}
            res = executor.run(code)
            if events is not None:
                events.emit("code_result", agent="code", error=bool(res.error))
            return {"output": present(res.stdout, res.error, res.duration_ms)}
        return REGISTRY.dispatch(name, tool_input, store)

    try:
        executor.start()
        loop = run_loop(
            client,
            model=model,
            effort=effort,
            system_prompt=_system_prompt(),
            tools_schema=code_agent_tools_schema(),
            initial_user=initial_user,
            terminal_tool="report_root_cause",
            terminal_model=RootCauseReport,
            dispatch=dispatch,
            hooks=outer_hooks,
            ctx=outer_ctx,
            max_iters=max_iters,
            max_tokens=max_tokens,
            output_budget=output_budget,
            events=events,
        )
    finally:
        executor.close()

    return CodeAgentResult(
        report=loop.terminal,
        loop=loop,
        internal_calls=[e["tool"] for e in inner_ctx.events if "tool" in e],
        internal_events=list(inner_ctx.events),
    )
