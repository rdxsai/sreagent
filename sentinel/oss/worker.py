"""One code-mode worker: a bounded REPL over a tight tool subset.

The model writes Python against the subset SDK in the hardened sandbox, prints the
facts it needs, and calls finish(verdict) when done. The harness parses the verdict
from stdout, validates it against the result schema, and on a malformed one or a
traceback feeds that back for self-correction, up to MAX_CODE_ITERS. Returns the
verdict plus a harness_fail flag so the trace can tell "crashed" from "wrong".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import openai
from pydantic import BaseModel, ValidationError

from sentinel.agent.hooks import BudgetGuard, HookRunner, Observer, RunContext, SealGuard
from sentinel.oss.catalog import sdk_for, specs_for
from sentinel.oss.llm import chat, reasoning_of, usage_of
from sentinel.oss.trace import TraceContext, TraceLogger
from sentinel.providers import ModelPreset
from sentinel.sandbox.executor import make_executor
from sentinel.sandbox.presentation import present
from sentinel.sandbox.rpc import RpcHandler
from sentinel.tools.store import TelemetryStore

MAX_CODE_ITERS = 6
_VERDICT_TAG = "__VERDICT__"
_FINISH_SRC = (
    "\n\ndef finish(verdict):\n"
    "    import json as _j\n"
    f"    print('{_VERDICT_TAG}' + _j.dumps(verdict, default=str))\n"
)

_RUN_CODE_TOOL = {
    "type": "function",
    "function": {
        "name": "run_code",
        "description": (
            "Execute Python in an isolated sandbox. The SRE API namespaces and finish(verdict) "
            "are preloaded. Compose results in code and print() only the facts you need; only "
            "stdout returns. Kernel state persists across calls. No network, no filesystem."
        ),
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source to run."}},
            "required": ["code"],
        },
    },
}

_SYSTEM = (
    "You are an SRE investigator subagent. You test ONE hypothesis about an incident by "
    "WRITING PYTHON in a sandbox, not by guessing.\n\n"
    "CRITICAL RULES:\n"
    "1. The API namespaces (metrics, traces, logs, topology, changes, correlate, hypothesis, "
    "runbook) are ALREADY defined as global objects. NEVER import them. Do not write "
    "'import metrics' or 'from metrics import ...'. Just call them directly, e.g. "
    "sat = metrics.resource_saturation(onset_second={onset}).\n"
    "2. Printing is NOT finishing. The MOMENT you have enough to judge the hypothesis, you MUST "
    "call finish(verdict) from inside run_code with a dict matching the schema below. If you "
    "never call finish(), your entire investigation is DISCARDED and counts as a failure.\n"
    "3. Keep code tight; you have at most {iters} run_code calls. Do not send empty code.\n\n"
    "Verdict schema (the dict you pass to finish):\n{schema}\n\n"
    "Example of ONE complete run_code turn:\n"
    "  sat = metrics.resource_saturation(onset_second={onset})\n"
    "  print(sat['risers'][:3])\n"
    "  finish({{'hypothesis': 'recommendationservice cpu saturation', 'supported': True, "
    "'root_cause_service': 'recommendationservice', 'fault_type': 'cpu', 'confidence': 0.8, "
    "'evidence': ['cpu 2.4->18.3']}})\n\n"
    "Preloaded API (call directly, do NOT import):\n{sdk}\n"
)


@dataclass
class WorkerRun:
    verdict: dict[str, Any] | None
    harness_fail: bool
    iters: int
    usage: dict[str, int]


def _extract_verdict(stdout: str) -> dict | None:
    for line in stdout.splitlines():
        if line.startswith(_VERDICT_TAG):
            try:
                return json.loads(line[len(_VERDICT_TAG):])
            except json.JSONDecodeError:
                return None
    return None


def run_worker(
    client: openai.OpenAI,
    preset: ModelPreset,
    store: TelemetryStore,
    *,
    incident: str,
    hypothesis: str,
    tool_subset: list[str],
    result_schema: type[BaseModel],
    ctx: TraceContext,
    trace: TraceLogger,
    onset: int = 720,
    backend: str = "docker",
    timeout_s: float = 25.0,
    max_iters: int = MAX_CODE_ITERS,
) -> WorkerRun:
    specs = specs_for(tool_subset)
    allowed = {s.name for s in specs}
    inner_ctx = RunContext(store=store, agent_id=ctx.agent_id, max_tool_calls=200)
    handler = RpcHandler(store, HookRunner([SealGuard(), BudgetGuard(), Observer()]), inner_ctx, allowed)
    source = sdk_for(tool_subset) + _FINISH_SRC
    executor = make_executor(handler, source, backend=backend, timeout_s=timeout_s)

    system = _SYSTEM.format(iters=max_iters, onset=onset,
                            schema=json.dumps(result_schema.model_json_schema()), sdk=sdk_for(tool_subset))
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Incident:\n{incident}\n\nHypothesis to test:\n{hypothesis}"},
    ]
    trace.worker(ctx, hypothesis=hypothesis, tool_subset=tool_subset)
    usage = {"input": 0, "output": 0}
    verdict: dict | None = None
    iters = 0

    try:
        executor.start()
        for iters in range(1, max_iters + 1):
            resp = chat(client, preset, messages, tools=[_RUN_CODE_TOOL], tool_choice="auto")
            msg = resp.choices[0].message
            u = usage_of(resp)
            usage["input"] += u["input"]
            usage["output"] += u["output"]
            reasoning = reasoning_of(msg)
            calls = msg.tool_calls or []
            if not calls:
                messages.append({"role": "assistant", "content": msg.content or ""})
                messages.append({"role": "user", "content": "Call run_code with Python; do not answer in prose."})
                trace.code_iter(ctx, iters, code="", stdout=msg.content or "", traceback=None, reasoning=reasoning)
                continue

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in calls
                ],
            })
            got_stdout = False
            for c in calls:
                if c.function.name != "run_code":
                    messages.append({"role": "tool", "tool_call_id": c.id, "content": "unknown tool"})
                    continue
                try:
                    code = json.loads(c.function.arguments).get("code", "")
                except json.JSONDecodeError:
                    code = ""
                res = executor.run(code)
                out = present(res.stdout, res.error, res.duration_ms)
                trace.code_iter(ctx, iters, code=code, stdout=res.stdout, traceback=res.error, reasoning=reasoning)
                found = _extract_verdict(res.stdout)
                if found is not None:
                    verdict = found
                if res.stdout.strip() and res.error is None:
                    got_stdout = True
                messages.append({"role": "tool", "tool_call_id": c.id, "content": out})
            if verdict is not None:
                break
            if got_stdout:
                # produced facts but did not finish -> nudge (the #1 failure with the weaker model)
                messages.append({"role": "user", "content": (
                    "You produced output but did NOT call finish(). If you now have enough to judge the "
                    "hypothesis, call finish(verdict) inside run_code right now; otherwise investigate more.")})
    finally:
        executor.close()

    if verdict is None:
        trace.log(ctx, "harness_fail", reason="no verdict after max iters", iters=iters)
        return WorkerRun(verdict=None, harness_fail=True, iters=iters, usage=usage)

    try:
        validated = result_schema.model_validate(verdict).model_dump(mode="json")
    except ValidationError as exc:
        trace.log(ctx, "harness_fail", reason=f"verdict failed schema: {exc}"[:300], iters=iters)
        return WorkerRun(verdict=verdict, harness_fail=True, iters=iters, usage=usage)

    trace.log(ctx, "verdict", verdict=validated, iters=iters)
    return WorkerRun(verdict=validated, harness_fail=False, iters=iters, usage=usage)
