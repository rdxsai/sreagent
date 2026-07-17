from pathlib import Path

from sentinel_rcaeval.case import load_case
from sentinel_rcaeval.truth import RCAEvalTruth, build_truth
from tests.unit.rcaeval_synth import write_synth_case


def test_build_truth_from_case(tmp_path: Path):
    raw = tmp_path / "ob_cartservice_cpu_1"
    write_synth_case(raw)
    truth = build_truth(load_case(raw))
    assert truth.scenario_id == "ob_cartservice_cpu_1"
    assert truth.root_cause.kind == "service"
    assert truth.root_cause.service == "cartservice"
    assert "cpu" in truth.root_cause.type
    assert truth.accepted_services == ["cartservice"]
    assert truth.fault_category == "resource"
    assert truth.root_cause_indicator == "cpu_utilization"


def test_truth_roundtrips_json(tmp_path: Path):
    raw = tmp_path / "tt_ts-order-service_delay_2"
    write_synth_case(raw)
    truth = build_truth(load_case(raw))
    reloaded = RCAEvalTruth.model_validate_json(truth.model_dump_json())
    assert reloaded.fault_category == "network"
    assert reloaded.root_cause_indicator == "latency_p95_ms"
