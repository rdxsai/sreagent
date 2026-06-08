"""Golden tests for the metrics tools."""

from __future__ import annotations

from pathlib import Path

from sentinel.tools import metrics
from sentinel.tools.models import (
    CompareBaselineInput,
    DetectShiftInput,
    MetricSeriesInput,
    NoArgs,
)
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
FAILURE = FixtureStore(ROOT / "fixtures" / "payment_failure_001" / "public")


def test_list_series() -> None:
    out = metrics.metrics_list_series(NoArgs(), FAILURE)
    keys = {(s.service, s.metric) for s in out.series}
    assert ("payment", "request_error_rate") in keys
    assert ("checkout", "latency_p95_ms") in keys


def test_metric_series_summary_and_downsample() -> None:
    out = metrics.metrics_series(
        MetricSeriesInput(service="payment", metric="request_error_rate", max_points=5),
        FAILURE,
    )
    assert out.unit == "ratio"
    assert out.summary["max"] > 0.0
    assert len(out.points) <= 5
    assert out.truncated == (out.summary["count"] > 5)


def test_compare_baseline_detects_post_onset_shift() -> None:
    out = metrics.metrics_compare_baseline(
        CompareBaselineInput(
            service="payment",
            metric="request_error_rate",
            baseline_start=0,
            baseline_end=299,
            compare_start=300,
            compare_end=900,
        ),
        FAILURE,
    )
    assert out.compare_mean > out.baseline_mean
    assert out.shifted is True


def test_detect_shift_finds_rise() -> None:
    out = metrics.metrics_detect_shift(
        DetectShiftInput(service="payment", metric="request_error_rate"), FAILURE
    )
    assert out.shift_second is not None
    assert out.after_mean > out.before_mean
    assert out.magnitude > 0.0
