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
