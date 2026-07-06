"""Window export writes fixture-format JSONL snapshots from a live store."""

import json
from pathlib import Path

from sentinel.fixtures.schemas import DerivedAlert
from sentinel.newrelic.export import export_window
from sentinel.newrelic.store import NewRelicStore

W = 1_751_700_000_000
END = W + 600_000
NOW = END + 120_000


class FakeClient:
    def __init__(self, rules):
        self.rules = rules

    def nrql(self, query):
        for needle, results in self.rules:
            if needle in query:
                return results
        return []


def test_export_window_writes_all_signal_files(tmp_path: Path):
    rules = [
        ("uniques(service.name", [{"uniques.service.name": ["payment"]}]),
        ("percentile(duration.ms, 95)", [{"beginTimeSeconds": W // 1000, "p95": 12.0}]),
        ("filter(count(*)", [{"beginTimeSeconds": W // 1000, "error_rate": 0.01}]),
        ("container.memory.usage.total", [{"beginTimeSeconds": W // 1000, "memory_mb": 100.0}]),
        ("FROM Span SINCE", [{
            "timestamp": W + 1000, "trace.id": "t1", "id": "s1", "duration.ms": 1.0,
            "name": "op", "service.name": "payment", "span.kind": "server",
        }]),
        ("FROM Log", [{
            "timestamp": W + 2000, "message": "hello", "severity.text": "INFO",
            "service.name": "payment",
        }]),
        ("FROM SentinelChange", [{
            "timestamp": W + 3000, "changeId": "chg_1", "service": "payment",
            "kind": "deploy", "summary": "s", "diffTouches": "a",
        }]),
    ]
    alert = DerivedAlert(
        alertname="A", severity="critical", starts_at_second=1, value=1.0,
        expr="e", fingerprint="f",
    )
    store = NewRelicStore(
        FakeClient(rules), window_start_ms=W, window_end_ms=END,
        alerts=[alert], now_ms=lambda: NOW,
    )
    counts = export_window(store, tmp_path)
    assert counts == {"spans": 1, "logs": 1, "metrics": 3, "changes": 1}
    span = json.loads((tmp_path / "traces.jsonl").read_text().splitlines()[0])
    assert span["span_id"] == "s1" and span["service"] == "payment"
    metric_rows = (tmp_path / "metrics.jsonl").read_text().splitlines()
    assert len(metric_rows) == 3
    assert (tmp_path / "logs.jsonl").exists() and (tmp_path / "changes.jsonl").exists()
