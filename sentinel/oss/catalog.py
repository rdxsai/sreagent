"""Two-tier tool view for code mode.

catalog()      -> compact per-namespace method list, the manager's reasoning
                  surface (it never sees raw telemetry, only what tools exist).
sdk_for(names) -> the full typed Python SDK for a worker's tool subset, the code
                  the model writes against inside the sandbox.

Both are derived from the same frozen (alphabetical) code_tool_specs(), so the
prefix is byte-stable and provider prompt-caching stays valid.
"""

from __future__ import annotations

from collections import defaultdict

import sentinel.tools  # noqa: F401  (register the 52 tools into REGISTRY)
from sentinel.sandbox.client_gen import (
    code_tool_specs,
    generate_client_source,
)

_SPECS = code_tool_specs()  # 46: the 52 tools minus terminals and investigate_*
_BY_NAME = {s.name: s for s in _SPECS}

# The dependency-graph-first worker's subset (step 10).
TOPOLOGY_TOOLS: list[str] = [
    "traces_build_topology",
    "topology_dependencies",
    "topology_locate_origin",
    "topology_blast_radius",
    "topology_critical_path",
    "topology_compare",
]


def all_tool_names() -> list[str]:
    return list(_BY_NAME)


def _purpose(description: str, limit: int = 68) -> str:
    first = description.strip().split("\n", 1)[0].split(". ", 1)[0].strip()
    return first if len(first) <= limit else first[:limit].rstrip() + "…"


def catalog() -> str:
    """Compact catalog: tool name + purpose, grouped by namespace. The manager picks
    tool-name subsets from this and never sees arg signatures (those are the worker's
    SDK) or raw telemetry. Kept small so it stays the manager's whole reasoning surface."""
    by_ns: dict[str, list] = defaultdict(list)
    for s in _SPECS:
        by_ns[s.namespace].append(s)
    out = ["Tools by group (pick a tight subset per hypothesis). name -- purpose:"]
    for ns in sorted(by_ns):
        out.append(f"[{ns}]")
        for s in sorted(by_ns[ns], key=lambda x: x.name):
            out.append(f"  {s.name} -- {_purpose(s.description)}")
    return "\n".join(out)


def specs_for(names: list[str]) -> list:
    """Resolve a subset by tool name, preserving the frozen order; unknown names dropped."""
    want = set(names)
    return [s for s in _SPECS if s.name in want]


def sdk_for(names: list[str]) -> str:
    """Full typed SDK text for a worker's tool subset (the code-writing surface)."""
    return generate_client_source(specs_for(names))
