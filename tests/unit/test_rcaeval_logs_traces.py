from pathlib import Path

from sentinel_rcaeval.normalize import make_window, map_logs, map_traces
from tests.unit.rcaeval_synth import write_synth_case


def test_map_logs_windows_and_rebases(tmp_path: Path):
    raw = tmp_path / "ob_cartservice_cpu_1"
    write_synth_case(raw)
    window = make_window(1000)
    logs = map_logs(raw / "logs.csv", window)
    assert [l.time for l in logs] == [30, 210]  # 850->30, 1030->210; 1600 dropped
    err = [l for l in logs if l.severity == "ERROR"]
    assert err and err[0].service == "cartservice" and err[0].trace_id == "t2"


def test_map_traces_fields(tmp_path: Path):
    raw = tmp_path / "ob_cartservice_cpu_1"
    write_synth_case(raw)
    window = make_window(1000)
    spans = map_traces(raw / "traces.csv", window)
    child = [s for s in spans if s.span_id == "s2"][0]
    assert child.parent_span_id == "s1"
    assert child.service == "cartservice"
    assert child.status == "ERROR"
    assert child.duration_ms == 800.0
    assert child.time == 210


def test_downsample_keeps_priority(tmp_path: Path):
    raw = tmp_path / "ob_cartservice_cpu_1"
    write_synth_case(raw)
    window = make_window(1000)
    logs = map_logs(raw / "logs.csv", window, cap=1)
    assert len(logs) == 1
    assert logs[0].severity == "ERROR"  # error kept over info under a tight cap
