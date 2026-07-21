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
from sentinel.tools.stats import Z_MIN, family, step_z
from sentinel.tools.store import TelemetryStore

_TOPO_DIR = Path("rcaeval/topology")
_SYSTEM = {"ob": "online_boutique", "ss": "sock_shop", "tt": "train_ticket"}


class Graph(BaseModel):
    edges: list[list[str]] = Field(default_factory=list)      # directed [caller, callee]
    ranked_services: list[str] = Field(default_factory=list)  # blast-radius order, origin-first
    anomalous: list[str] = Field(default_factory=list)        # services with any onset signature step
    effects: dict[str, dict[str, float]] = Field(default_factory=dict)  # service -> family -> z
    source: str = "none"                                      # static | trace | causal | none
    traces_present: bool = False                              # spans exist -> trace tools usable
    notes: str = ""


def system_of(case_id: str) -> str:
    prefix = case_id.split("_", 1)[0]
    return _SYSTEM.get(prefix, prefix)


# -- the shared onset-step test (single implementation in sentinel.tools.stats, used by
# the ranking overlay here AND by the worker-side metric tools) --
#
# Live telemetry broke the old mean-ratio test two ways at once: near-zero noisy series
# (error rates) pass a 1.5x ratio on a handful of stray errors, while a genuine fault on
# a shared host lifts EVERY container's cpu a little, so binary flags mark ~everything
# anomalous and the ranking degenerates to graph position. Verified against the recorded
# adHighCpu live failure: ad cpu z=85 vs 5.7 for the loudest contention victim.


def onset_effects(store: TelemetryStore, service: str, onset: int) -> dict[str, float]:
    """Per-family robust effect size (max z over the family's metrics) at onset."""
    out = {"resource": 0.0, "latency": 0.0, "error": 0.0}
    for _svc, metric, _unit in store.list_metric_keys():
        if _svc != service:
            continue
        rows = store.metric_series(service, metric)
        pre = [r.value for r in rows if r.time < onset]
        post = [r.value for r in rows if r.time >= onset]
        fam = family(metric)
        out[fam] = max(out[fam], step_z(pre, post, metric))
    return out


def onset_signatures(store: TelemetryStore, service: str, onset: int) -> dict[str, bool]:
    """Which symptom signatures stepped at onset for this service, from metrics alone."""
    return {fam: z >= Z_MIN for fam, z in onset_effects(store, service, onset).items()}


# Structural accumulators and synthetic traffic sources: they inherit every fault's
# symptom (or generate the load) but are almost never the origin. Demoted, not excluded.
_SYNTHETIC_ROLES = {"load-generator", "loadgenerator", "frontend-proxy", "frontendproxy", "frontend-web"}


def _rank(edges: list[list[str]], effects: dict[str, dict[str, float]]) -> list[str]:
    """Anomaly overlay, regime-aware. The robust z decides MEMBERSHIP (who stepped at
    onset), and the dominant signal family decides which structure localizes the origin,
    because the three families propagate differently:

    - resource (cpu/mem) does not propagate through calls: host contention lifts every
      container a little (z ~5) while the origin's own step is huge (z ~85 live), so
      magnitude ranks. This is the live-noise fix: binary flags made these equal.
    - latency propagates BOTH directions (an injected delay slows the service's whole
      NIC), so the anomalous set forms a star around the origin; variance-normalized z
      is unreliable across the star (a stable victim out-scores a noisy origin, and a
      quiet origin can under-score its slowed callees, e.g. orders z=6.5 vs shipping
      z=108). The origin is the star CENTER: most anomalous neighbors, entry roots and
      synthetic sources demoted on ties.
    - error propagates UPSTREAM only (callers of a lossy service fail; its own callees
      do not), so the origin is the DEEPEST anomalous service: no anomalous-error
      callee, then largest error z.
    """
    services = {s for e in edges for s in e}
    callees: dict[str, set] = defaultdict(set)
    neigh: dict[str, set] = defaultdict(set)
    indeg: dict[str, int] = defaultdict(int)
    for c, e in edges:
        callees[c].add(e)
        neigh[c].add(e)
        neigh[e].add(c)
        indeg[e] += 1
    demoted = {s for s in services if indeg[s] == 0} | (_SYNTHETIC_ROLES & services)

    def zf(s: str, fam: str) -> float:
        return effects.get(s, {}).get(fam, 0.0)

    def z_tot(s: str) -> float:
        return max(effects.get(s, {}).values() or [0.0])

    dom, dom_z = "resource", 0.0
    for s in services:
        for fam, z in effects.get(s, {}).items():
            if z > dom_z:
                dom, dom_z = fam, z

    if dom_z < Z_MIN:  # nothing stepped: stable order only
        return sorted(services, key=lambda s: (-z_tot(s), 1 if s in demoted else 0, s))

    if dom == "resource":
        key = lambda s: (-zf(s, "resource"), 1 if s in _SYNTHETIC_ROLES else 0, -z_tot(s), s)  # noqa: E731
    elif dom == "latency":
        anom = {s for s in services if zf(s, "latency") >= Z_MIN}
        total = sum(zf(s, "latency") for s in anom) or 1.0
        # Star coverage: the fault sits where the closed neighborhood accounts for
        # ~all the anomalous latency mass (raw degree is brittle: one noise member
        # adjacent to the entry root can out-degree a leaf origin). Near-ties go
        # against entry roots and synthetic sources.
        cov = {s: sum(zf(n, "latency") for n in (anom & ({s} | neigh[s]))) / total
               for s in anom}
        best = max(cov.values(), default=0.0)
        key = lambda s: (0 if s in anom else 1,                    # noqa: E731
                         0 if cov.get(s, 0.0) >= best - 0.05 else 1,  # near-best coverage
                         1 if s in demoted else 0,                    # accumulators lose ties
                         -cov.get(s, 0.0), -z_tot(s), s)
    else:  # error
        anom = {s for s in services if zf(s, "error") >= Z_MIN}
        key = lambda s: (0 if s in anom else 1,                    # noqa: E731
                         0 if s in anom and not (callees[s] & anom) else 1,  # deepest first
                         1 if s in demoted else 0,
                         -zf(s, "error"), s)
        order = sorted(services, key=key)
        # Loss physics: callers of a lossy service ERROR, but the origin itself shows
        # only transit distress (latency/resource step, no own errors). If the deepest
        # error-anomalous service has a callee with such a step, that callee is the
        # origin -- descend one hop instead of blaming the deepest failing caller.
        if order and order[0] in anom:
            deepest = order[0]
            transit = [c for c in callees[deepest]
                       if max(zf(c, "latency"), zf(c, "resource")) >= Z_MIN and c not in anom]
            if transit:
                origin = max(transit, key=lambda c: max(zf(c, "latency"), zf(c, "resource")))
                order.remove(origin)
                order.insert(0, origin)
        return order
    return sorted(services, key=key)


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
        try:
            topo = traces.traces_build_topology(NoArgs(), store)
        except Exception:
            return None  # empty/degenerate span set -> no trace topology, fall back
        edges = [[e.caller, e.callee] for e in topo.edges]
        if not edges:
            return None
        return Graph(edges=edges, source="trace", notes="derived from spans this incident")


class CausalSource:
    name = "causal"

    def build(self, store: TelemetryStore, *, onset: int) -> Graph | None:
        # stub: every benchmark system has a static artifact, so causal is not needed yet.
        return None


def resolve_topology(store: TelemetryStore, *, system: str, onset: int,
                     prefer_trace: bool = False) -> Graph:
    """Static (maintained) is preferred for structure since it is always available; the
    trace source is still probed so trace-rich scenarios expose the edge tools to workers
    (traces_present), independent of which source built the edges. Ranking overlay on top.

    prefer_trace: for a LIVE system whose real service names differ from any frozen static
    artifact (e.g. the running OTel demo vs the RCAEval OB recording), build from the live
    spans so the graph carries the live vocabulary; fall back to static if no spans."""
    static = StaticSource(system).build(store, onset=onset)
    trace = TraceSource().build(store, onset=onset)  # probe: are spans present?
    if prefer_trace:
        base = trace if (trace and trace.edges) else static
    else:
        base = static if (static and static.edges) else trace
    if not (base and base.edges):
        return Graph(edges=[], ranked_services=[], source="none", notes="no topology source available")
    services = {s for e in base.edges for s in e}
    effects = {s: onset_effects(store, s, onset) for s in services}
    base.effects = effects
    base.ranked_services = _rank(base.edges, effects)
    base.anomalous = sorted(s for s in services if max(effects[s].values() or [0.0]) >= Z_MIN)
    base.traces_present = trace is not None
    return base
