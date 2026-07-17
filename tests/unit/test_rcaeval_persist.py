import json
from types import SimpleNamespace

from sentinel_tool_eval.harness import TaskResult
from sentinel_tool_eval.tasks import Scenario


def _fake_result(scenario_id: str, correct: bool) -> TaskResult:
    return TaskResult(
        scenario_id=scenario_id,
        report={"root_cause": {"kind": "service", "service": "cartservice"}, "evidence": ["x"]},
        grade={"correct": correct, "location_correct": correct},
        calls=["metrics_series"],
        worker_calls=["logs_search"],
        trace=[{"type": "tool_call", "tool": "metrics_series"}],
        findings=[{"service": "cartservice"}],
        iterations=3,
        stop="reported",
        usage={"input": 100, "output": 50},
        worker_usage={"input": 10, "output": 5},
        tool_errors=0,
        denials=0,
        subagents=1,
    )


def test_persist_case_writes_full_artifacts(tmp_path):
    from sentinel_tool_eval.rcaeval import persist_case

    persist_case(_fake_result("ob_cartservice_cpu_1", True), tmp_path / "case")
    result_j = json.loads((tmp_path / "case" / "result.json").read_text())
    trace_j = json.loads((tmp_path / "case" / "trace.json").read_text())
    metrics_j = json.loads((tmp_path / "case" / "metrics.json").read_text())
    assert result_j["report"]["root_cause"]["service"] == "cartservice"
    assert trace_j["trace"] == [{"type": "tool_call", "tool": "metrics_series"}]
    assert metrics_j["diagnosis"]["correct"] is True
    assert metrics_j["cost"]["tool_calls"] == 2


def test_run_sweep_stops_at_cost_ceiling(tmp_path, monkeypatch):
    import sentinel_tool_eval.rcaeval as rc

    scenarios = [
        Scenario(id=f"ob_cart_cpu_{i}", public_dir=tmp_path, truth_path=tmp_path / "t")
        for i in range(3)
    ]
    monkeypatch.setattr(
        rc, "run_case",
        lambda client, s: SimpleNamespace(scenario_id=s.id, grade={"correct": True}, est_cost_usd=15.0),
    )
    monkeypatch.setattr(rc, "persist_case", lambda result, case_dir: None)
    card = rc.run_sweep(client=None, scenarios=scenarios, out_dir=tmp_path / "out", max_cost=20.0)
    assert card["stopped_early"] is True
    assert card["n"] == 2
    assert card["not_run"] == ["ob_cart_cpu_2"]
    assert card["total_cost_usd"] == 30.0
