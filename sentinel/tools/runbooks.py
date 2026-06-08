"""Runbook tools: a small, leak-safe SRE knowledge base over symptom classes.

Runbooks are general investigation procedures, not incident-specific answers. The
catalog is global (the same for every incident) and deliberately contains no
service names, change ids, fault types, or tool names, only the method, so it
guides the agent without revealing the root cause or short-circuiting tool
selection. The agent maps each step to tools itself.
"""

from __future__ import annotations

from sentinel.registry import tool
from sentinel.tools.models import (
    Runbook,
    RunbookGetInput,
    RunbookGetOutput,
    RunbookMatch,
    RunbookSearchInput,
    RunbookSearchOutput,
)

_RUNBOOKS: list[dict] = [
    {
        "id": "rb-degradation",
        "title": "User-facing degradation",
        "when": "A user-facing service shows elevated errors or latency since an onset time.",
        "keywords": ["degradation", "user-facing", "incident", "general", "page", "slo", "alert"],
        "steps": [
            "Establish the true onset from the first failing trace span, not the alert time, which lags.",
            "Build the service dependency graph to scope the blast radius around the symptom.",
            "Localize where the failure originates: a service whose own work fails, a caller->callee call that never completes, or a resource-saturated service.",
            "List the recent changes before onset on the implicated service; a cause precedes its symptom.",
            "Disambiguate same-service changes by content: which one touches the failing area.",
            "Rule out the other changes and the unaffected services before concluding.",
        ],
    },
    {
        "id": "rb-errors",
        "title": "Elevated error rate",
        "when": "Requests are failing: error spans or error logs have increased.",
        "keywords": ["error", "errors", "failing", "failure", "5xx", "exception", "unavailable"],
        "steps": [
            "Distinguish a service fault (the service's own server work errors) from an edge fault (a caller's calls to a clean downstream fail).",
            "Do not infer node vs edge from a single per-service error-rate number; use the span hierarchy and span kind.",
            "Anchor onset on the first error span, then correlate the nearest preceding change on the implicated service.",
        ],
    },
    {
        "id": "rb-latency",
        "title": "Latency or slowdown",
        "when": "A service or request path is slow but not necessarily erroring.",
        "keywords": ["latency", "slow", "slowdown", "p95", "timeout", "gc", "duration", "pause"],
        "steps": [
            "Rank services by their own self-time (duration minus downstream waits) to find the one that is slow itself, not waiting on a dependency.",
            "Confirm the latency shift against a pre-onset baseline; a slow path may have no error spans at all.",
            "Correlate the recent change on the slow service, for example a memory/GC or request-handler configuration change.",
        ],
    },
    {
        "id": "rb-saturation",
        "title": "Resource saturation",
        "when": "A service is consuming abnormal CPU or resources, possibly without elevated latency or errors.",
        "keywords": ["cpu", "saturation", "resource", "memory", "cores", "throttle", "load"],
        "steps": [
            "Check per-service CPU cores in use; a saturated service can peg cores while its request latency stays normal, so error and latency tools alone will miss it.",
            "Compare the post-onset cores against the service's own pre-onset baseline.",
            "Correlate the recent change on the saturated service that plausibly increases its CPU work.",
        ],
    },
    {
        "id": "rb-change",
        "title": "Correlating a change to an incident",
        "when": "You have a candidate set of recent changes and need to identify the culprit.",
        "keywords": ["change", "deploy", "culprit", "correlate", "rollback", "config", "decoy"],
        "steps": [
            "Keep only changes whose time precedes the onset; a cause precedes its symptom.",
            "Prefer changes on the service the telemetry implicates over changes elsewhere.",
            "When two changes are on the same service before onset, decide by content: which one's touched area matches the observed failure.",
        ],
    },
]

_BY_ID = {rb["id"]: rb for rb in _RUNBOOKS}


@tool(namespace="runbook")
def runbook_search(params: RunbookSearchInput, store: object) -> RunbookSearchOutput:
    """Search the SRE runbook knowledge base by symptom keywords.

    Returns the runbooks whose symptom class matches your query (e.g. 'errors',
    'latency', 'cpu saturation', 'change correlation'), most relevant first. These
    are general procedures, not the answer to this incident; open one with
    runbook_get to read its steps and map them to tools.
    """
    terms = {t for t in params.query.lower().split() if len(t) >= 3}
    scored: list[tuple[int, dict]] = []
    for rb in _RUNBOOKS:
        keywords = rb["keywords"]
        score = sum(1 for t in terms if any(t == k or t in k or k in t for k in keywords))
        if score:
            scored.append((score, rb))
    scored.sort(key=lambda s: s[0], reverse=True)
    chosen = [rb for _s, rb in scored[: params.limit]] or [_BY_ID["rb-degradation"]]
    return RunbookSearchOutput(
        matches=[RunbookMatch(id=rb["id"], title=rb["title"], when=rb["when"]) for rb in chosen]
    )


@tool(namespace="runbook")
def runbook_get(params: RunbookGetInput, store: object) -> RunbookGetOutput:
    """Fetch a runbook's diagnostic steps by id (from runbook_search).

    Returns the ordered, generic investigation steps for that symptom class. Use it
    as a checklist; you still decide which tools satisfy each step and which finding
    applies to this incident.
    """
    rb = _BY_ID.get(params.runbook_id)
    if rb is None:
        return RunbookGetOutput(note=f"no runbook with id {params.runbook_id!r}; call runbook_search first")
    return RunbookGetOutput(
        runbook=Runbook(id=rb["id"], title=rb["title"], when=rb["when"], steps=list(rb["steps"]))
    )
