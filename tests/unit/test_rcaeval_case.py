from pathlib import Path

from sentinel_rcaeval.case import RCAEvalCase, load_case, parse_case_name, read_inject_time
from tests.unit.rcaeval_synth import INJECT_TIME, write_synth_case


def test_parse_case_name_simple():
    assert parse_case_name("ob_cartservice_cpu_1") == ("ob", "cartservice", "cpu", "1")


def test_parse_case_name_multitoken_service():
    assert parse_case_name("tt_ts-order-service_delay_3") == ("tt", "ts-order-service", "delay", "3")


def test_load_case_and_inject_time(tmp_path: Path):
    raw = tmp_path / "ob_cartservice_cpu_1"
    write_synth_case(raw)
    case = load_case(raw)
    assert isinstance(case, RCAEvalCase)
    assert (case.system, case.service, case.fault) == ("ob", "cartservice", "cpu")
    assert case.metrics_path.name == "metrics.json"
    assert read_inject_time(case.inject_time_path) == INJECT_TIME
