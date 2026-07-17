from pathlib import Path

from sentinel_rcaeval.convert import convert_case
from sentinel_tool_eval.rcaeval import aggregate_scorecard, discover_cases
from tests.unit.rcaeval_synth import write_synth_case


def test_discover_cases(tmp_path: Path):
    raw = tmp_path / "ob_cartservice_cpu_1"
    write_synth_case(raw)
    convert_case(raw, tmp_path / "converted")
    scenarios = discover_cases(tmp_path / "converted")
    assert [s.id for s in scenarios] == ["ob_cartservice_cpu_1"]
    assert scenarios[0].public_dir.name == "public"


def test_discover_cases_missing_root_returns_empty(tmp_path):
    assert discover_cases(tmp_path / "does_not_exist") == []


def test_aggregate_scorecard_breakdowns():
    graded = [
        ("ob_cartservice_cpu_1", {"correct": True}),
        ("ob_frontend_delay_1", {"correct": False}),
        ("tt_ts-order-service_loss_2", {"correct": True}),
    ]
    card = aggregate_scorecard(graded)
    assert card["n"] == 3
    assert card["overall_ac1"] == 2 / 3
    assert card["by_system"]["ob"] == {"hits": 1, "n": 2, "ac1": 0.5}
    assert card["by_category"]["network"]["n"] == 2
