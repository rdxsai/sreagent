from sentinel.fixtures.schemas import RootCause
from sentinel_rcaeval.truth import RCAEvalTruth
from sentinel_tool_eval.rcaeval_grader import grade_localization


def _truth() -> RCAEvalTruth:
    return RCAEvalTruth(
        scenario_id="ob_cartservice_cpu_1",
        root_cause=RootCause(kind="service", type="cpu exhaustion", service="cartservice"),
        accepted_services=["cartservice"],
        root_cause_indicator="cpu_utilization",
        fault_category="resource",
    )


def test_correct_service_is_ac1_hit():
    report = {"root_cause": {"kind": "service", "service": "cartservice", "type": "cpu"},
              "evidence": ["cpu_utilization rose to 0.95"]}
    g = grade_localization(report, _truth())
    assert g["correct"] and g["location_correct"]
    assert g["type_match"] and g["indicator_correct"]


def test_wrong_service_is_miss():
    report = {"root_cause": {"kind": "service", "service": "frontend", "type": "latency"}, "evidence": []}
    g = grade_localization(report, _truth())
    assert not g["correct"] and not g["location_correct"]


def test_no_report():
    g = grade_localization(None, _truth())
    assert g["correct"] is False and g["location_correct"] is False


def test_edge_report_with_both_endpoints_accepted():
    truth = _truth()
    truth = truth.model_copy(update={"accepted_services": ["cartservice", "frontend"]})
    report = {"root_cause": {"kind": "edge", "caller": "frontend", "callee": "cartservice"}, "evidence": []}
    assert grade_localization(report, truth)["location_correct"]
