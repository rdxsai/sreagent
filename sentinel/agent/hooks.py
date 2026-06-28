"""Deterministic control hooks for the agent loop.

Hooks are deterministic code that runs at fixed lifecycle points of the
non-deterministic agent: before a tool runs (PreToolUse), after it runs
(PostToolUse), and when a subagent finishes (SubagentStop). They validate, block,
redact, gate, and trace, without trusting the model to do any of it. The control
semantics mirror the Claude Code / Agent SDK hooks (allow/deny/modify on
PreToolUse; transform/inject on PostToolUse; validate-and-retry on SubagentStop)
and the OpenAI guardrail tripwire idea, implemented in-process so they run inside
the sealed eval.

References:
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/agent-sdk/hooks
- https://openai.github.io/openai-agents-python/guardrails/
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from sentinel.tools.models import ServiceFinding

# Tokens that must never reach the agent's context. The fixtures are already sealed
# at build time, so SealGuard's redaction is defense in depth: a deterministic
# runtime guarantee that no raw flag key, eval-only marker, or truth slug leaks,
# even if a future tool or data change introduced one.
BANNED_TOKENS: tuple[str, ...] = (
    "feature_flag",
    "eval_only",
    "truth.json",
    "paymentFailure",
    "paymentUnreachable",
    "productCatalogFailure",
    "cartFailure",
    "adManualGc",
    "adHighCpu",
    "recommendationCacheFailure",
    "kafkaQueueProblems",
    "loadGeneratorFloodHomepage",
)

_FORBIDDEN_INPUT_REFS = ("eval_only", "truth.json", "raw_flag")


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]


@dataclass
class ToolDecision:
    action: Literal["allow", "deny", "modify"] = "allow"
    reason: str = ""
    modified_input: dict[str, Any] | None = None


@dataclass
class FindingVerdict:
    accept: bool = True
    feedback: str = ""


@dataclass
class RunContext:
    """Per-run mutable state the hooks read and write."""

    store: Any = None
    agent_id: str = "manager"
    max_tool_calls: int = 60
    tool_calls: int = 0
    redactions: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)


class Hook:
    """Base hook: override the lifecycle methods you care about (default no-op)."""

    name = "hook"

    def pre_tool_use(self, call: ToolCall, ctx: RunContext) -> ToolDecision | None:
        return None

    def post_tool_use(self, call: ToolCall, output: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        return output

    def subagent_stop(self, agent_id: str, finding: Any, ctx: RunContext) -> FindingVerdict | None:
        return None


class SealGuard(Hook):
    """Leak-safety: deny tool inputs that reference eval-only resources, and redact
    banned tokens from tool outputs before the model sees them."""

    name = "seal_guard"

    def pre_tool_use(self, call: ToolCall, ctx: RunContext) -> ToolDecision | None:
        blob = json.dumps(call.input).lower()
        for ref in _FORBIDDEN_INPUT_REFS:
            if ref in blob:
                return ToolDecision(
                    action="deny",
                    reason=f"input references the forbidden resource {ref!r}; tools may read only public telemetry",
                )
        return None

    def post_tool_use(self, call: ToolCall, output: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        text = json.dumps(output)
        redacted = text
        for token in BANNED_TOKENS:
            if token and token in redacted:
                redacted = redacted.replace(token, "[redacted]")
        if redacted == text:
            return output
        ctx.redactions += 1
        return json.loads(redacted)


_TERMINAL_TOOLS = ("report_root_cause", "report_finding", "report_change_verdict")


class BudgetGuard(Hook):
    """Cost/runaway control: deny further tool calls past the per-run budget,
    steering the agent to conclude (terminal report tools are always allowed)."""

    name = "budget_guard"

    def pre_tool_use(self, call: ToolCall, ctx: RunContext) -> ToolDecision | None:
        if call.name in _TERMINAL_TOOLS:
            return None
        if ctx.tool_calls >= ctx.max_tool_calls:
            return ToolDecision(
                action="deny",
                reason=(
                    f"tool-call budget of {ctx.max_tool_calls} reached; conclude now by calling "
                    "report_root_cause with your best current conclusion"
                ),
            )
        return None


class ReportGate(Hook):
    """Terminal-answer gate: deny a malformed/contradictory final report (manager) or
    an incomplete finding (investigator) so the agent fixes it before the run ends."""

    name = "report_gate"

    def pre_tool_use(self, call: ToolCall, ctx: RunContext) -> ToolDecision | None:
        if call.name == "report_root_cause":
            issues = report_issues(call.input, ctx.store)
        elif call.name == "report_finding":
            issues = finding_issues(call.input)
        else:
            return None
        if issues:
            return ToolDecision(action="deny", reason="report not ready: " + "; ".join(issues) + ". Fix and resubmit.")
        return None


class DecoyCompletenessGate(Hook):
    """Terminal completeness: deny a final report that leaves any known non-culprit
    change unaccounted. All changes are public, so requiring the model to rule out
    every decoy leaks nothing; it only enforces a thorough conclusion."""

    name = "decoy_completeness_gate"

    def pre_tool_use(self, call: ToolCall, ctx: RunContext) -> ToolDecision | None:
        if call.name != "report_root_cause" or ctx.store is None:
            return None
        known = {c.id for c in ctx.store.list_changes()}
        culprit = call.input.get("culprit_change_id")
        ruled_out = set(call.input.get("ruled_out_change_ids") or [])
        missing = sorted(known - {culprit} - ruled_out)
        if missing:
            return ToolDecision(
                action="deny",
                reason=(
                    "report incomplete: account for every known change. Missing from "
                    f"ruled_out_change_ids: {', '.join(missing)}. Rule them out and resubmit."
                ),
            )
        return None


class Observer(Hook):
    """Observability: record every tool call (which agent, tool, error) for the
    trace and the cookbook metrics."""

    name = "observer"

    def post_tool_use(self, call: ToolCall, output: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        ctx.events.append(
            {"agent": ctx.agent_id, "tool": call.name, "error": isinstance(output, dict) and "error" in output}
        )
        return output


class FindingValidator(Hook):
    """Contract enforcement: validate an investigator's ServiceFinding on
    SubagentStop and send it back to retry on a violation."""

    name = "finding_validator"

    def subagent_stop(self, agent_id: str, finding: Any, ctx: RunContext) -> FindingVerdict | None:
        if finding is None:
            return FindingVerdict(accept=False, feedback="no ServiceFinding returned; submit one with the required fields")
        data = finding.model_dump() if isinstance(finding, ServiceFinding) else finding
        issues: list[str] = []
        if not data.get("service"):
            issues.append("service is required")
        if data.get("is_origin") and not data.get("evidence"):
            issues.append("is_origin=true requires at least one evidence line")
        if data.get("is_origin") and not data.get("fault_type"):
            issues.append("is_origin=true requires a fault_type")
        conf = data.get("confidence")
        if conf is None or not (0.0 <= conf <= 1.0):
            issues.append("confidence must be between 0 and 1")
        if issues:
            return FindingVerdict(accept=False, feedback="; ".join(issues))
        return FindingVerdict(accept=True)


def report_issues(report_input: dict[str, Any], store: Any) -> list[str]:
    """Structural/consistency checks for a draft root-cause report (used by ReportGate)."""
    issues: list[str] = []
    rc = report_input.get("root_cause") or {}
    kind = rc.get("kind")
    if kind == "service" and not rc.get("service"):
        issues.append("root_cause kind=service requires a service")
    if kind == "edge" and not (rc.get("caller") and rc.get("callee")):
        issues.append("root_cause kind=edge requires caller and callee")
    if kind not in ("service", "edge"):
        issues.append("root_cause kind must be 'service' or 'edge'")
    culprit = report_input.get("culprit_change_id")
    if not culprit:
        issues.append("culprit_change_id is empty")
    elif store is not None:
        known = {c.id for c in store.list_changes()}
        if culprit not in known:
            issues.append(f"culprit_change_id {culprit} is not a known change")
    if culprit and culprit in (report_input.get("ruled_out_change_ids") or []):
        issues.append("culprit_change_id also appears in ruled_out_change_ids")
    return issues


def finding_issues(finding_input: dict[str, Any]) -> list[str]:
    """Completeness checks for an investigator's ServiceFinding (used by ReportGate)."""
    issues: list[str] = []
    if not finding_input.get("service"):
        issues.append("service is required")
    if finding_input.get("is_origin") and not finding_input.get("evidence"):
        issues.append("is_origin=true requires at least one evidence line")
    if finding_input.get("is_origin") and not finding_input.get("fault_type"):
        issues.append("is_origin=true requires a fault_type")
    return issues


class HookRunner:
    """Runs the registered hooks at each lifecycle point and combines their results.

    PreToolUse: deny short-circuits (deny beats everything, per the SDK precedence);
    a modify is applied. PostToolUse: outputs are chained through each hook.
    SubagentStop: the first reject wins.
    """

    def __init__(self, hooks: list[Hook]) -> None:
        self._hooks = hooks

    def pre_tool_use(self, call: ToolCall, ctx: RunContext) -> ToolDecision:
        decision = ToolDecision(action="allow")
        for hook in self._hooks:
            result = hook.pre_tool_use(call, ctx)
            if result is None:
                continue
            if result.action == "deny":
                return result
            if result.action == "modify":
                decision = result
        return decision

    def post_tool_use(self, call: ToolCall, output: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        for hook in self._hooks:
            output = hook.post_tool_use(call, output, ctx)
        return output

    def subagent_stop(self, agent_id: str, finding: Any, ctx: RunContext) -> FindingVerdict:
        for hook in self._hooks:
            verdict = hook.subagent_stop(agent_id, finding, ctx)
            if verdict is not None and not verdict.accept:
                return verdict
        return FindingVerdict(accept=True)


def default_hooks() -> list[Hook]:
    """The standard control set: seal, budget, report gate, observability, finding contract."""
    return [SealGuard(), BudgetGuard(), ReportGate(), Observer(), FindingValidator()]
