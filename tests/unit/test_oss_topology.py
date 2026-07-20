"""Unit tests for the metrics-first RCA fixes: topology resolver, onset-step test,
ranking, and the signature/verdict/synthesis schemas. Hermetic: a synthetic store
plus the committed static topology artifacts, no docker, no live stores."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sentinel.fixtures.schemas import MetricRow
from sentinel.oss import topology
from sentinel.oss.schemas import Hypothesis, Synthesis, WorkerVerdict


class FakeStore:
    """Minimal TelemetryStore surface the topology resolver touches."""

    def __init__(self, series: dict[tuple[str, str], list[tuple[int, float]]], spans=None):
        self._series = series
        self._spans = spans or []

    def list_metric_keys(self):
        return sorted({(s, m, "count") for (s, m) in self._series})

    def list_services(self):
        return sorted({s for (s, _m) in self._series})

    def metric_series(self, service, metric):
        return [MetricRow(time=t, service=service, metric=metric, value=v, unit="count")
                for t, v in self._series.get((service, metric), [])]

    def all_spans(self):
        return list(self._spans)


def test_system_of():
    assert topology.system_of("ob_recommendationservice_cpu_1") == "online_boutique"
    assert topology.system_of("ss_orders_delay_1") == "sock_shop"
    assert topology.system_of("tt_x_delay_1") == "train_ticket"


def test_onset_step_flags_stepped_ignores_flat():
    store = FakeStore({
        ("a", "latency_p95_ms"): [(0, 10.0), (600, 10.0), (800, 100.0), (1000, 100.0)],  # stepped
        ("b", "latency_p95_ms"): [(0, 10.0), (600, 10.0), (800, 10.0), (1000, 10.0)],     # flat
        ("a", "cpu_utilization"): [(0, 0.3), (800, 0.3)],                                  # flat
    })
    assert topology.onset_signatures(store, "a", onset=720) == {"resource": False, "latency": True, "error": False}
    assert topology.onset_signatures(store, "b", onset=720) == {"resource": False, "latency": False, "error": False}


def test_static_source_loads_committed_artifacts():
    ob = topology.StaticSource("online_boutique").build(FakeStore({}), onset=720)
    ss = topology.StaticSource("sock_shop").build(FakeStore({}), onset=720)
    assert ob is not None and len(ob.edges) == 9 and ob.source == "static"
    assert ss is not None and ["front-end", "orders"] in ss.edges
    assert topology.StaticSource("nonesuch").build(FakeStore({}), onset=720) is None


def test_trace_source_none_without_spans():
    # services exist (metric-only scenario) but no spans -> no edges -> None
    store = FakeStore({("orders", "latency_p95_ms"): [(0, 10.0), (800, 100.0)]}, spans=[])
    assert topology.TraceSource().build(store, onset=720) is None


def test_causal_source_is_stub():
    assert topology.CausalSource().build(FakeStore({}), onset=720) is None


def test_rank_origin_above_victim_metric_only():
    # a -> b -> c ; the delay is on b, so b and its caller a step, c stays flat.
    # b (most-connected anomalous, non-entry) should outrank the entry root a.
    edges = [["a", "b"], ["b", "c"]]
    store = FakeStore({
        ("a", "latency_p95_ms"): [(0, 10.0), (800, 120.0)],
        ("b", "latency_p95_ms"): [(0, 10.0), (800, 200.0)],
        ("c", "latency_p95_ms"): [(0, 10.0), (800, 10.0)],
    })
    ranked = topology._rank(edges, store, onset=720)
    assert ranked[0] == "b"                 # origin (non-entry, connected) first
    assert ranked.index("b") < ranked.index("a")   # above the entry-root victim
    assert ranked.index("c") == len(ranked) - 1     # flat service last


def test_resolve_topology_metric_only_is_static_and_ranked():
    # online_boutique static graph + a stepped productcatalog metric, no spans.
    store = FakeStore({("productcatalogservice", "latency_p95_ms"): [(0, 5.0), (800, 400.0)]}, spans=[])
    g = topology.resolve_topology(store, system="online_boutique", onset=720)
    assert g.source == "static" and g.edges and g.ranked_services
    assert g.ranked_services[0] == "productcatalogservice"
    # the anomalous set gates refutation: an anomalous origin cannot be refuted by a
    # mis-signature worker. productcatalog stepped -> it is anomalous.
    assert "productcatalogservice" in g.anomalous
    assert g.traces_present is False  # no spans


def test_worker_verdict_carries_observed_signatures():
    v = WorkerVerdict(hypothesis="h", supported=True, root_cause_service="x",
                      signature="latency", observed_signatures={"latency": True}, confidence=0.8)
    assert v.observed_signatures["latency"] is True
    with pytest.raises(ValidationError):
        WorkerVerdict(hypothesis="h", supported=True, confidence=2.0)  # confidence out of range


def test_synthesis_ranked_and_hypothesis_signature():
    s = Synthesis(ranked_services=["a", "b", "c"], root_cause_service="a", justification="j")
    assert s.root_cause_service == s.ranked_services[0]
    h = Hypothesis(candidate_service="a", signature="latency", tool_subset=["metrics_summary_all"])
    assert h.signature == "latency"
    with pytest.raises(ValidationError):
        Hypothesis(candidate_service="a", signature="bogus", tool_subset=[])  # not a Signature
