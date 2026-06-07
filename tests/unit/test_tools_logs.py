"""Golden tests for the logs tools."""

from __future__ import annotations

from pathlib import Path

from sentinel.fixtures.replay import load_public_fixture
from sentinel.tools import logs
from sentinel.tools.models import LogsForTraceInput, LogsSearchInput
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "fixtures" / "payment_failure_001" / "public"
FAILURE = FixtureStore(PUBLIC)


def test_logs_for_trace_case_folds_and_filters() -> None:
    fixture = load_public_fixture(PUBLIC)
    trace_id = next(row.trace_id for row in fixture.logs if row.trace_id)
    out = logs.logs_for_trace(LogsForTraceInput(trace_id=trace_id), FAILURE)
    assert out.logs
    assert all(line.trace_id == trace_id for line in out.logs)
    assert all(line.severity == line.severity.lower() for line in out.logs)


def test_logs_search_severity_floor_and_truncation() -> None:
    out = logs.logs_search(LogsSearchInput(severity_min="error", limit=5), FAILURE)
    assert out.returned <= 5
    assert all(line.severity in {"error", "err", "critical", "fatal"} for line in out.logs)
    if out.total_matched > 5:
        assert out.truncated is True
        assert out.note
