"""Grader and task-builder tests (no API calls)."""

from __future__ import annotations

from sentinel_tool_eval.grader import grade, load_truth
from sentinel_tool_eval.tasks import SCENARIOS, build_task_prompt

FAILURE = next(s for s in SCENARIOS if s.id == "payment_failure_001")
UNREACHABLE = next(s for s in SCENARIOS if s.id == "payment_unreachable_001")


def test_task_prompt_states_symptom_and_alerts_without_naming_tools() -> None:
    prompt = build_task_prompt(FAILURE)
    assert "Symptom:" in prompt
    assert "UserFacingDegradation" in prompt
    assert "traces_" not in prompt and "metrics_" not in prompt  # selection is the model's job


def test_grade_correct_service_fault_report() -> None:
    truth = load_truth(FAILURE.truth_path)
    report = {
        "root_cause": {"kind": "service", "service": "payment", "type": "payment_charge_failure"},
        "culprit_change_id": "chg_0003",
        "ruled_out_change_ids": ["chg_0001", "chg_0002", "chg_0004", "chg_0005", "chg_0006"],
    }
    result = grade(report, truth)
    assert result["correct"]
    assert result["location_correct"]
    assert result["culprit_correct"]
    assert result["decoys_ruled_out"]
    assert result["type_match"]


def test_grade_correct_edge_fault_report() -> None:
    truth = load_truth(UNREACHABLE.truth_path)
    report = {
        "root_cause": {"kind": "edge", "caller": "checkout", "callee": "payment", "type": "dependency_unreachable"},
        "culprit_change_id": "chg_0003",
        "ruled_out_change_ids": ["chg_0001", "chg_0002"],
    }
    result = grade(report, truth)
    assert result["correct"]
    assert result["location_correct"]


def test_grade_wrong_location_and_culprit() -> None:
    truth = load_truth(FAILURE.truth_path)
    report = {
        "root_cause": {"kind": "service", "service": "checkout", "type": "something"},
        "culprit_change_id": "chg_0001",
        "ruled_out_change_ids": [],
    }
    result = grade(report, truth)
    assert not result["correct"]
    assert not result["location_correct"]
    assert not result["culprit_correct"]


def test_grade_no_report() -> None:
    truth = load_truth(FAILURE.truth_path)
    result = grade(None, truth)
    assert not result["correct"]
    assert result["reason"] == "no report submitted"


def test_accepted_services_widens_location_for_async_scenarios():
    from sentinel.fixtures.schemas import PrivateTruth

    truth = PrivateTruth.model_validate(
        {
            "scenario_id": "kafka_live",
            "injection": {"raw_flag_key": "kafkaQueueProblems", "variant": "on", "enabled_at_second": 300},
            "root_cause": {"kind": "service", "type": "queue_backpressure", "service": "kafka"},
            "culprit_change_id": "chg_2003",
            "expected_evidence": ["lag grows"],
            "decoy_change_ids": ["chg_2001"],
            "accepted_services": ["kafka", "fraud-detection", "checkout"],
        }
    )
    report = {
        "root_cause": {"kind": "service", "type": "latency", "service": "fraud-detection"},
        "culprit_change_id": "chg_2003",
        "ruled_out_change_ids": ["chg_2001"],
    }
    result = grade(report, truth)
    assert result["location_correct"] is True
    assert result["correct"] is True
    wrong = grade({**report, "root_cause": {"kind": "service", "service": "frontend", "type": "latency"}}, truth)
    assert wrong["location_correct"] is False


def test_without_accepted_services_location_stays_exact_match():
    from sentinel.fixtures.schemas import PrivateTruth

    truth = PrivateTruth.model_validate(
        {
            "scenario_id": "payment",
            "injection": {"raw_flag_key": "paymentFailure", "variant": "100%", "enabled_at_second": 300},
            "root_cause": {"kind": "service", "type": "payment_charge_failure", "service": "payment"},
            "culprit_change_id": "chg_0003",
            "expected_evidence": ["errors"],
            "decoy_change_ids": ["chg_0001"],
        }
    )
    report = {
        "root_cause": {"kind": "service", "type": "payment_charge_failure", "service": "checkout"},
        "culprit_change_id": "chg_0003",
        "ruled_out_change_ids": ["chg_0001"],
    }
    assert grade(report, truth)["location_correct"] is False
