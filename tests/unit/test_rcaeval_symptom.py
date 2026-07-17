from pathlib import Path

from sentinel_rcaeval.normalize import load_metric_frame, make_window, melt_metrics
from sentinel_rcaeval.symptom import synthesize_symptom
from tests.unit.rcaeval_synth import write_synth_case


def test_symptom_prefers_error_rate_breach(tmp_path: Path):
    raw = tmp_path / "ob_cartservice_cpu_1"
    write_synth_case(raw)
    window = make_window(1000)
    rows = melt_metrics(load_metric_frame(raw / "metrics.json"), window)
    symptom, alert = synthesize_symptom(rows, window)
    assert "frontend" in symptom
    assert alert.severity == "critical"
    assert alert.starts_at_second == 180
    assert alert.value >= 0.2
    assert "frontend" in alert.labels.get("service", "")
    assert alert.fingerprint  # non-empty stable id


def test_symptom_fallback_ranks_by_relative_rise():
    from sentinel.fixtures.schemas import MetricRow

    window = make_window(1000)  # onset_second == 180
    rows: list[MetricRow] = []
    for t in (0, 60, 120):  # pre-onset
        rows.append(MetricRow(time=t, service="svcA", metric="cpu_utilization", value=0.10, unit="ratio"))
        rows.append(MetricRow(time=t, service="svcB", metric="memory_mb", value=120.0, unit="MB"))
    for t in (180, 240, 300):  # post-onset: svcA +850% relative, svcB +8% relative (but +10 absolute)
        rows.append(MetricRow(time=t, service="svcA", metric="cpu_utilization", value=0.95, unit="ratio"))
        rows.append(MetricRow(time=t, service="svcB", metric="memory_mb", value=130.0, unit="MB"))

    symptom, alert = synthesize_symptom(rows, window)
    assert "svcA" in symptom  # relative-rise winner, not the large-absolute memory metric
    assert alert.severity == "warning"
    assert alert.starts_at_second == 180
