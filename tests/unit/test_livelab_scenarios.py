"""The unified scenario catalog: every dashboard run is one Scenario, whichever
lab it lives on."""
from __future__ import annotations

from sentinel.api.livelab.scenarios import SCENARIOS, Scenario, scenario_by_id


def test_sockshop_scenarios_cover_the_vetted_targets() -> None:
    ss = [s for s in SCENARIOS if s.lab == "sock_shop"]
    assert {s.truth_service for s in ss} == {"shipping", "catalogue", "payment", "orders"}
    for s in ss:
        assert s.fault_kind == "cpu_hog"
        assert s.system == "sock_shop"
        assert s.prefer_trace is False
        assert s.hero_metric == "cpu"
        assert s.truth_service in s.accepted
        assert s.health_nrql is not None and s.truth_service in s.health_nrql
        assert s.recovered_below == 50.0


def test_otel_scenarios_come_from_the_live_specs() -> None:
    otel = {s.id: s for s in SCENARIOS if s.lab == "otel_demo"}
    assert set(otel) == {"otel-ad_high_cpu_live_001", "otel-payment_failure_live_001"}

    ad = otel["otel-ad_high_cpu_live_001"]
    assert ad.fault_kind == "adHighCpu"
    assert ad.truth_service == "ad"
    assert ad.system == "online_boutique"
    assert ad.prefer_trace is True
    assert ad.hero_metric == "cpu"
    assert ad.recovered_below is None  # relative recovery: demo cpu scale unpinned
    assert "container.name = 'ad'" in ad.health_nrql

    pay = otel["otel-payment_failure_live_001"]
    assert pay.fault_kind == "paymentFailure"
    assert pay.truth_service == "payment"
    assert pay.hero_metric == "error"
    assert pay.recovered_below == 0.05
    assert "Span" in pay.health_nrql and "payment" in pay.health_nrql
    assert pay.symptom  # comes verbatim from labs/otel/live_incident.SPECS


def test_scenario_by_id_roundtrip_and_unknown() -> None:
    s = scenario_by_id("sockshop-cpu-shipping")
    assert isinstance(s, Scenario) and s.truth_service == "shipping"
    assert scenario_by_id("nope") is None


def test_ids_are_unique() -> None:
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))


def test_catalog_flag_injected_elects_remove_impairment() -> None:
    from sentinel.actions.catalog import elect_primary, suggest_actions

    actions = suggest_actions(target_service="ad", fault_type="flag_injected",
                              signature="resource")
    primary = elect_primary(actions)
    assert primary is not None
    assert primary.kind == "remove_impairment"
    assert primary.reversible is True
    assert "injected fault" in primary.description
