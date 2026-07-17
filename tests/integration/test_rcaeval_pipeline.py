from pathlib import Path

from sentinel.tools.store import FixtureStore
from sentinel_rcaeval.convert import convert_case
from sentinel_rcaeval.truth import RCAEvalTruth
from sentinel_tool_eval.rcaeval_grader import grade_localization
from sentinel_tool_eval.tasks import Scenario, build_task_prompt
from tests.unit.rcaeval_synth import write_synth_case


def test_convert_prompt_and_grade_end_to_end(tmp_path: Path):
    raw = tmp_path / "ob_cartservice_cpu_1"
    write_synth_case(raw)
    out = convert_case(raw, tmp_path / "converted")

    scenario = Scenario(id=out.name, public_dir=out / "public", truth_path=out / "eval_only" / "truth.json")
    prompt = build_task_prompt(scenario)
    assert "Symptom:" in prompt and "Firing alerts:" in prompt

    store = FixtureStore(scenario.public_dir)
    assert store.metric_series("cartservice", "cpu_utilization")

    truth = RCAEvalTruth.model_validate_json(scenario.truth_path.read_text())
    good = grade_localization(
        {"root_cause": {"kind": "service", "service": "cartservice", "type": "cpu"},
         "evidence": ["cpu_utilization spiked"]},
        truth,
    )
    bad = grade_localization(
        {"root_cause": {"kind": "service", "service": "frontend", "type": "latency"}, "evidence": []},
        truth,
    )
    assert good["correct"] is True
    assert bad["correct"] is False
