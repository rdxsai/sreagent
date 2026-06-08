"""Golden tests for report-depth tools and the trace/onset extras."""

from __future__ import annotations

from pathlib import Path

from sentinel.tools import correlate, report, traces
from sentinel.tools.models import (
    BuildEvidenceInput,
    NoArgs,
    RootCause,
    RootCauseReport,
    ServiceInput,
)
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
FAILURE = FixtureStore(ROOT / "fixtures" / "payment_failure_001" / "public")


def test_build_evidence_assembles_signals_and_timeline() -> None:
    out = report.report_build_evidence(
        BuildEvidenceInput(onset_second=330, suspected_service="payment", culprit_change_id="chg_0003"), FAILURE
    )
    assert any("server spans error" in e for e in out.evidence)
    assert any("chg_0003" in e for e in out.evidence)
    assert any(e.kind == "onset" for e in out.timeline)


def test_self_check_passes_a_complete_report() -> None:
    rpt = RootCauseReport(
        root_cause=RootCause(kind="service", service="payment", type="payment_charge_failure"),
        culprit_change_id="chg_0003",
        ruled_out_change_ids=["chg_0001", "chg_0002"],
        evidence=["payment server spans error"],
    )
    out = report.report_self_check(rpt, FAILURE)
    assert out.ok
    assert out.issues == []


def test_self_check_flags_culprit_in_ruled_out_and_missing_evidence() -> None:
    rpt = RootCauseReport(
        root_cause=RootCause(kind="service", service="payment", type="x"),
        culprit_change_id="chg_0003",
        ruled_out_change_ids=["chg_0003"],  # culprit also ruled out
        evidence=[],  # missing
    )
    out = report.report_self_check(rpt, FAILURE)
    assert not out.ok
    assert any("ruled_out" in i for i in out.issues)
    assert any("evidence" in i for i in out.issues)


def test_service_summary_payment_has_own_server_errors() -> None:
    out = traces.traces_service_summary(ServiceInput(service="payment"), FAILURE)
    assert out.total_spans > 0
    assert out.server_error_spans > 0  # payment's own work failed
    assert out.error_rate > 0


def test_service_summary_uninvolved_service_clean() -> None:
    out = traces.traces_service_summary(ServiceInput(service="ad"), FAILURE)
    assert out.server_error_spans == 0  # ad is not involved in a payment fault


def test_onset_consensus_prefers_trace_onset() -> None:
    out = correlate.correlate_onset_consensus(NoArgs(), FAILURE)
    assert out.trace_onset_second is not None
    # logs error from the start (noise), so trace and log onsets disagree and the
    # consensus falls back to the trace onset.
    assert out.consensus_onset_second == out.trace_onset_second
