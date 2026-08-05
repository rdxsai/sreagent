"""TelemetryReader: FACET TIMESERIES NRQL -> chart series, journaled per poll for
replay; ingest freshness; the cpu_now health callable used for recovery checks."""
from __future__ import annotations

import json

from sentinel.api.livelab.telemetry import ReplayTelemetryReader, TelemetryReader


def make_nrql(rows_by_metric: dict):
    queries: list[str] = []

    def nrql(q: str) -> list[dict]:
        queries.append(q)
        if "container.cpu.utilization" in q:
            return rows_by_metric.get("cpu", [])
        if "container.memory.percent" in q:
            return rows_by_metric.get("mem", [])
        return rows_by_metric.get("other", [])

    nrql.queries = queries
    return nrql


def facet_row(t_s: int, service: str, key: str, value: float, *, facet_as_list: bool) -> dict:
    return {"beginTimeSeconds": t_s, "endTimeSeconds": t_s + 15,
            "facet": [service] if facet_as_list else service, key: value}


def test_series_folds_facet_rows_into_per_service_points(tmp_path) -> None:
    rows = {"cpu": [facet_row(100, "shipping", "cpu", 12.5, facet_as_list=False),
                    facet_row(115, "shipping", "cpu", 300.0, facet_as_list=True),
                    facet_row(100, "payment", "cpu", 5.0, facet_as_list=False)],
            "mem": [facet_row(100, "shipping", "mem", 42.0, facet_as_list=False)]}
    reader = TelemetryReader(make_nrql(rows), journal_dir=tmp_path, clock=lambda: 200.0)
    out = reader.series(["shipping", "payment"], since_ms=100_000, until_ms=200_000)
    assert out["series"]["cpu"]["shipping"] == [[100_000, 12.5], [115_000, 300.0]]
    assert out["series"]["cpu"]["payment"] == [[100_000, 5.0]]
    assert out["series"]["mem"]["shipping"] == [[100_000, 42.0]]
    assert out["fetched_at_ms"] == 200_000


def test_series_is_journaled(tmp_path) -> None:
    reader = TelemetryReader(make_nrql({}), journal_dir=tmp_path, clock=lambda: 1.0)
    reader.series(["shipping"], since_ms=0, until_ms=1000)
    lines = (tmp_path / "telemetry.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["fetched_at_ms"] == 1000


def test_ingest_age_from_latest_metric_timestamp() -> None:
    nrql = make_nrql({"other": [{"latest.timestamp": 90_000}]})
    reader = TelemetryReader(nrql, journal_dir=None, clock=lambda: 100.0)
    assert reader.ingest_age_s() == 10.0


def test_ingest_age_none_when_no_data() -> None:
    reader = TelemetryReader(make_nrql({"other": []}), journal_dir=None)
    assert reader.ingest_age_s() is None


def test_cpu_now_reads_recent_average() -> None:
    nrql = make_nrql({"cpu": [{"p": 250.0}]})
    reader = TelemetryReader(nrql, journal_dir=None)
    assert reader.cpu_now("shipping") == 250.0
    assert "container.name = 'shipping'" in nrql.queries[-1]


def test_replay_reader_serves_last_journaled_frame_at_or_before(tmp_path) -> None:
    frames = [{"series": {"cpu": {"shipping": [[0, 1.0]]}}, "fetched_at_ms": 1000},
              {"series": {"cpu": {"shipping": [[0, 1.0], [15, 2.0]]}}, "fetched_at_ms": 2000},
              {"series": {"cpu": {"shipping": [[0, 1.0], [15, 2.0], [30, 9.0]]}}, "fetched_at_ms": 3000}]
    path = tmp_path / "telemetry.jsonl"
    path.write_text("\n".join(json.dumps(f) for f in frames) + "\n")
    replay = ReplayTelemetryReader(path)
    assert replay.at(2500)["fetched_at_ms"] == 2000
    assert replay.at(500)["fetched_at_ms"] == 1000     # nothing earlier: first frame
    assert replay.at(99_999)["fetched_at_ms"] == 3000  # past the end: last frame


def test_series_includes_span_error_rate(tmp_path) -> None:
    def nrql(q: str) -> list[dict]:
        if "FROM Span" in q:
            return [{"beginTimeSeconds": 100, "endTimeSeconds": 115,
                     "facet": "payment", "p": 0.42, "service.name": "payment"}]
        return []

    reader = TelemetryReader(nrql, journal_dir=None, clock=lambda: 1.0)
    out = reader.series(["payment"], since_ms=0, until_ms=200_000)
    assert out["series"]["err"]["payment"] == [[100_000, 0.42]]
