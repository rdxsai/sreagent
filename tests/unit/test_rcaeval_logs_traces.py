import csv
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


def test_map_logs_skips_blank_timestamp_and_defaults_blank_message(tmp_path):
    p = tmp_path / "logs.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "service", "level", "message", "trace_id"])
        w.writerow([1030, "cartservice", "ERROR", "", "t2"])    # blank message -> placeholder
        w.writerow(["", "cartservice", "INFO", "orphan", "t3"])  # blank timestamp -> skipped
    logs = map_logs(p, make_window(1000))
    assert len(logs) == 1
    assert logs[0].message == "(no message)"


def test_map_traces_skips_rows_missing_ids(tmp_path):
    p = tmp_path / "traces.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["trace_id", "span_id", "parent_id", "start_time",
                    "service", "operation", "duration_ms", "status"])
        w.writerow(["t2", "s2", "s1", 1030, "cartservice", "AddItem", 800.0, "ERROR"])  # valid
        w.writerow(["", "s3", "s1", 1040, "cartservice", "Op", 10.0, "OK"])             # blank trace_id -> skipped
        w.writerow(["t4", "", "s1", 1050, "cartservice", "Op", 10.0, "OK"])             # blank span_id -> skipped
    spans = map_traces(p, make_window(1000))
    assert [s.span_id for s in spans] == ["s2"]
