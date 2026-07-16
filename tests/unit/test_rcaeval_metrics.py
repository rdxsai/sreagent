from pathlib import Path

from sentinel_rcaeval.normalize import (
    canonical_column,
    load_metric_frame,
    make_window,
    melt_metrics,
)
from tests.unit.rcaeval_synth import write_synth_case


def test_canonical_column_maps_red_and_resource():
    assert canonical_column("frontend_error_rate") == ("frontend", "request_error_rate", "ratio")
    assert canonical_column("frontend_latency") == ("frontend", "latency_p95_ms", "ms")
    assert canonical_column("cartservice_cpu") == ("cartservice", "cpu_utilization", "ratio")
    assert canonical_column("cartservice_mem") == ("cartservice", "memory_mb", "MB")


def test_canonical_column_passthrough():
    assert canonical_column("checkout_queue_depth") == ("checkout_queue", "depth", "count")


def test_load_and_melt(tmp_path: Path):
    raw = tmp_path / "ob_cartservice_cpu_1"
    write_synth_case(raw)
    frame = load_metric_frame(raw / "metrics.json")
    window = make_window(1000)
    rows = melt_metrics(frame, window)
    cpu = [r for r in rows if r.service == "cartservice" and r.metric == "cpu_utilization"]
    assert cpu and all(0 <= r.time <= 480 for r in cpu)
    assert max(r.value for r in cpu) == 0.95
    assert cpu[0].unit == "ratio"
