"""The investigate namespace: the manager's subagent-orchestration tools.

These are the only tools that spawn subagents. They are registered here so the
manager sees their schema and selects them model-driven, but they are executed by
the agent runtime (sentinel.agent.manager), not by REGISTRY.dispatch, because
running a subagent needs the LLM client and worker model the runtime holds. The
bodies below are defensive: if one is ever dispatched directly (e.g. exposed to a
non-manager agent by mistake), it returns a clear error instead of running.

Investigators get a scoped tool set with this namespace stripped, so they cannot
recurse.
"""

from __future__ import annotations

from sentinel.errors import ToolError
from sentinel.registry import tool
from sentinel.tools.models import (
    ChangeVerdict,
    InvestigateChangeInput,
    InvestigateParallelInput,
    InvestigateServiceInput,
    ParallelFindings,
    ServiceFinding,
)
from sentinel.tools.store import TelemetryStore

_ORCHESTRATION_ONLY = (
    "this is an orchestration tool executed by the manager runtime, not by direct dispatch"
)


@tool(namespace="investigate")
def investigate_service(params: InvestigateServiceInput, store: TelemetryStore) -> ServiceFinding:
    """Spawn an isolated investigator subagent to deep-dive ONE candidate service.

    The subagent runs in a fresh context with a scoped tool set and returns a typed
    ServiceFinding: whether this service is the fault origin (its own server errors,
    or its own latency/CPU shift, versus merely calling a failing dependency), the
    suspect change on it, a confidence, and evidence. Use it for one targeted
    suspect; use investigate_parallel to do several at once.
    """
    raise ToolError(_ORCHESTRATION_ONLY, code="orchestration_only")


@tool(namespace="investigate")
def investigate_parallel(params: InvestigateParallelInput, store: TelemetryStore) -> ParallelFindings:
    """Fan out: investigate several candidate services in parallel, one isolated subagent each.

    This is the main delegation after triage. Pass the 2-4 suspects the triage
    surfaced (the blast radius, not the whole topology); each runs in its own fresh
    context and returns a ServiceFinding. Reconcile the findings to pick the origin
    (the one whose own work failed), then correlate the culprit change.
    """
    raise ToolError(_ORCHESTRATION_ONLY, code="orchestration_only")


@tool(namespace="investigate")
def investigate_change(params: InvestigateChangeInput, store: TelemetryStore) -> ChangeVerdict:
    """Spawn a subagent to assess whether one specific change is the culprit.

    The subagent compares the change's diff_touches against the observed failure and
    its timing against onset, returning a ChangeVerdict. Use it to confirm a suspect
    change when same-service candidates can't be separated by timing alone.
    """
    raise ToolError(_ORCHESTRATION_ONLY, code="orchestration_only")
