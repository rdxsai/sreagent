"""Topology as a maintained INPUT, not a per-incident trace computation.

In production an SRE opens a maintained service map (Kiali, Datadog, a mesh) during
an incident; they do not rebuild the architecture from that incident's telemetry. This
resolves a graph from the first source that can build one, so a metric-only fault (no
traces) still has a graph to reason over. A trace source stays in the chain so
trace-rich scenarios still sharpen. Ranking is a metrics anomaly overlay applied on top
of whatever source produced the edges, so the origin (the deepest anomalous node) sorts
first regardless of which telemetry the fault emitted.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from sentinel.tools import traces
from sentinel.tools.models import NoArgs
from sentinel.tools.store import TelemetryStore

_TOPO_DIR = Path("rcaeval/topology")
_SYSTEM = {"ob": "online_boutique", "ss": "sock_shop", "tt": "train_ticket"}


class Graph(BaseModel):
    edges: list[list[str]] = Field(default_factory=list)      # directed [caller, callee]
    ranked_services: list[str] = Field(default_factory=list)  # blast-radius order, origin-first
    anomalous: list[str] = Field(default_factory=list)        # services with any onset signature step
    source: str = "none"                                      # static | trace | causal | none
    traces_present: bool = False                              # spans exist -> trace tools usable
    notes: str = ""


def system_of(case_id: str) -> str:
    prefix = case_id.split("_", 1)[0]
    return _SYSTEM.get(prefix, prefix)


# -- the shared onset-step test (used by ranking here and mirrored by the worker's tools) --

def _mean(rows, lo: int, hi: int) -> float:
    vals = [r.value for r in rows if lo <= r.time < hi]
    return sum(vals) / len(vals) if vals else 0.0


def onset_signatures(store: TelemetryStore, service: str, onset: int) -> dict[str, bool]:
    """Which symptom signatures stepped at onset for this service, from metrics alone."""
    out = {"resource": False, "latency": False, "error": False}
    for _svc, metric, _unit in store.list_metric_keys():
        if _svc != service:
            continue
        rows = store.metric_series(service, metric)
        pre, post = _mean(rows, 0, onset), _mean(rows, onset, 10**9)
        if not (post > pre * 1.5 and post - pre > 1e-9):
            continue
        m = metric.lower()
        if "latency" in m or "duration" in m:
            out["latency"] = True
        elif "error" in m:
            out["error"] = True
        else:  # cpu / mem / disk / socket / anything else resource-shaped
            out["resource"] = True
    return out


def _rank(edges: list[list[str]], store: TelemetryStore, onset: int) -> list[str]:
    """Anomaly overlay. In a latency-propagation chain the origin is the anomalous
    node most connected to other anomalous nodes (it touches all its victims), while
    the request-entry root is a structural accumulator that is always anomalous but
    never the origin. So rank anomalous services by degree within the anomalous
    subgraph, with entry roots (in-degree 0 in the full graph) pushed down. The manager
    then does the final origin-vs-victim reasoning over this prior plus the graph."""
    services = {s for e in edges for s in e}
    anomalous = {s for s in services if any(onset_signatures(store, s, onset).values())}
    neigh: dict[str, set] = defaultdict(set)
    indeg: dict[str, int] = defaultdict(int)
    for c, e in edges:
        neigh[c].add(e)
        neigh[e].add(c)
        indeg[e] += 1
    entry = {s for s in services if indeg[s] == 0}  # request roots: never the origin

    def score(s: str) -> tuple:
        deg_anom = len(neigh[s] & anomalous)
        return (0 if s in anomalous else 1,   # anomalous first
                1 if s in entry else 0,        # entry roots last among anomalous
                -deg_anom)                     # most-connected-in-anomalous first

    return sorted(services, key=score)


# -- sources --

class TopologySource(Protocol):
    name: str
    def build(self, store: TelemetryStore, *, onset: int) -> Graph | None: ...


class StaticSource:
    name = "static"

    def __init__(self, system: str) -> None:
        self._system = system

    def build(self, store: TelemetryStore, *, onset: int) -> Graph | None:
        path = _TOPO_DIR / f"{self._system}.json"
        if not path.exists():
            return None
        edges = json.loads(path.read_text()).get("edges", [])
        if not edges:
            return None
        return Graph(edges=[list(e) for e in edges], source="static",
                     notes=f"maintained topology for {self._system}")


class TraceSource:
    name = "trace"

    def build(self, store: TelemetryStore, *, onset: int) -> Graph | None:
        topo = traces.traces_build_topology(NoArgs(), store)
        edges = [[e.caller, e.callee] for e in topo.edges]
        if not edges:
            return None
        return Graph(edges=edges, source="trace", notes="derived from spans this incident")


class CausalSource:
    name = "causal"

    def build(self, store: TelemetryStore, *, onset: int) -> Graph | None:
        # stub: every benchmark system has a static artifact, so causal is not needed yet.
        return None


def resolve_topology(store: TelemetryStore, *, system: str, onset: int) -> Graph:
    """Static (maintained) is preferred for structure since it is always available; the
    trace source is still probed so trace-rich scenarios expose the edge tools to workers
    (traces_present), independent of which source built the edges. Ranking overlay on top."""
    static = StaticSource(system).build(store, onset=onset)
    trace = TraceSource().build(store, onset=onset)  # probe: are spans present?
    base = static if (static and static.edges) else trace
    if not (base and base.edges):
        return Graph(edges=[], ranked_services=[], source="none", notes="no topology source available")
    base.ranked_services = _rank(base.edges, store, onset)
    services = {s for e in base.edges for s in e}
    base.anomalous = sorted(s for s in services if any(onset_signatures(store, s, onset).values()))
    base.traces_present = trace is not None
    return base
