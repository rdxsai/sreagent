"""Golden tests for the correlate (cross-signal) and hypothesis tools."""

from __future__ import annotations

from pathlib import Path

from sentinel.tools import correlate, hypothesis
from sentinel.tools.models import (
    GatherEvidenceInput,
    Hypothesis,
    MetricToTracesInput,
    RuleOutInput,
    SignalsInput,
)
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
FAILURE = FixtureStore(ROOT / "fixtures" / "payment_failure_001" / "public")
UNREACHABLE = FixtureStore(ROOT / "fixtures" / "payment_unreachable_001" / "public")


def test_correlate_signals_flags_error_shift_on_payment() -> None:
    out = correlate.correlate_signals(SignalsInput(service="payment", onset_second=300), FAILURE)
    assert "request_error_rate" in out.shifted_metrics


def test_correlate_metric_to_traces_returns_exemplars() -> None:
    out = correlate.correlate_metric_to_traces(
        MetricToTracesInput(service="payment", onset_second=300, status="ERROR", limit=3), FAILURE
    )
    assert out.exemplar_trace_ids
    assert all(s.service == "payment" for s in out.sample)


def test_gather_evidence_supports_service_hypothesis() -> None:
    # onset is the trace-based first-error time (after the injection second), so the
    # culprit change at second 300 counts as "before onset".
    h = Hypothesis(id="h1", kind="service", service="payment", onset_second=330, rationale="suspect payment")
    out = hypothesis.hypothesis_gather_evidence(GatherEvidenceInput(hypothesis=h), FAILURE)
    assert out.supported
    assert out.confidence >= 0.8
    assert out.suspect_change_id == "chg_0003"  # nearest payment change before onset 330


def test_gather_evidence_supports_edge_hypothesis() -> None:
    h = Hypothesis(id="h1", kind="edge", caller="checkout", callee="payment", onset_second=420, rationale="unreachable")
    out = hypothesis.hypothesis_gather_evidence(GatherEvidenceInput(hypothesis=h), UNREACHABLE)
    assert out.supported  # client errors to payment, payment server clean


def test_rule_out_change_after_onset() -> None:
    out = hypothesis.hypothesis_rule_out(RuleOutInput(onset_second=330, change_id="chg_0004"), FAILURE)
    assert out.ruled_out  # chg_0004 at 350 is after onset 330


def test_rule_out_keeps_change_before_onset() -> None:
    out = hypothesis.hypothesis_rule_out(RuleOutInput(onset_second=330, change_id="chg_0003"), FAILURE)
    assert not out.ruled_out  # chg_0003 at 300 precedes onset


def test_rule_out_uninvolved_service() -> None:
    out = hypothesis.hypothesis_rule_out(RuleOutInput(onset_second=300, service="recommendation"), FAILURE)
    assert out.ruled_out  # recommendation is a decoy service with no own-fault signal


def test_rule_out_keeps_faulting_service() -> None:
    out = hypothesis.hypothesis_rule_out(RuleOutInput(onset_second=300, service="payment"), FAILURE)
    assert not out.ruled_out  # payment has its own server errors
