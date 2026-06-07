"""Golden tests for the logs depth tools."""

from __future__ import annotations

from pathlib import Path

from sentinel.tools import logs
from sentinel.tools.models import ErrorClustersInput, FirstErrorInput, LevelHistogramInput
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
FAILURE = FixtureStore(ROOT / "fixtures" / "payment_failure_001" / "public")


def test_error_clusters_ranked_by_count() -> None:
    out = logs.logs_error_clusters(ErrorClustersInput(limit=10), FAILURE)
    assert out.clusters
    counts = [c.count for c in out.clusters]
    assert counts == sorted(counts, reverse=True)
    assert all("#" in c.template or c.template for c in out.clusters)  # templated


def test_level_histogram_has_buckets() -> None:
    out = logs.logs_level_histogram(LevelHistogramInput(bucket_seconds=60), FAILURE)
    assert out.buckets
    assert any(b.counts for b in out.buckets)
    starts = [b.start for b in out.buckets]
    assert starts == sorted(starts)


def test_first_error_sorted_by_time() -> None:
    out = logs.logs_first_error(FirstErrorInput(severity_min="error"), FAILURE)
    assert out.first
    times = [e.time for e in out.first]
    assert times == sorted(times)
