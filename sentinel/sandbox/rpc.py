"""Host endpoint for sandbox proxy calls: the inner hook plane plus dispatch.

Every call the sandboxed code makes arrives here as (tool, args). We run the
inner hooks (SealGuard deny/redact, BudgetGuard cap, Observer record) against a
RunContext shared across the whole investigation, then REGISTRY.dispatch. The
return envelope is {"ok": True, "result": ...} or {"ok": False, "error": {...}};
the runtime turns a non-ok envelope into a Python exception so the model sees it.
"""

from __future__ import annotations

from typing import Any

from sentinel.agent.hooks import HookRunner, RunContext, ToolCall
from sentinel.registry import REGISTRY


class RpcHandler:
    def __init__(self, store: Any, hooks: HookRunner, ctx: RunContext, allowed: set[str]) -> None:
        self.store = store
        self.hooks = hooks
        self.ctx = ctx
        self.allowed = allowed

    def handle(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool not in self.allowed:
            return {"ok": False, "error": {"code": "unknown_tool", "message": f"unknown tool: {tool}"}}
        call = ToolCall(name=tool, input=args if isinstance(args, dict) else {})
        decision = self.hooks.pre_tool_use(call, self.ctx)
        if decision.action == "deny":
            return {"ok": False, "error": {"code": "blocked", "message": decision.reason}}
        tool_input = (
            decision.modified_input
            if decision.action == "modify" and decision.modified_input is not None
            else call.input
        )
        out = REGISTRY.dispatch(tool, tool_input, self.store)
        out = self.hooks.post_tool_use(call, out, self.ctx)
        self.ctx.tool_calls += 1
        if isinstance(out, dict) and "error" in out:
            return {"ok": False, "error": out["error"]}
        return {"ok": True, "result": out}
