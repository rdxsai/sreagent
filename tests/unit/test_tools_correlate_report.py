"""Golden tests for the correlate tools and the terminal report tool."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sentinel.registry import REGISTRY
from sentinel.tools import correlate, report
from sentinel.tools.models import (
    AttributeFaultInput,
    ChangeEvent,
    FaultObservation,
    ReportAck,
    RootCause,
    RootCauseReport,
    TimelineInput,
)

_CHANGES = [
    ChangeEvent(id="chg_0001", time=270, service="frontend", kind="deploy", summary="a"),
    ChangeEvent(id="chg_0002", time=285, service="recommendation", kind="config", summary="b"),
    ChangeEvent(id="chg_0003", time=300, service="payment", kind="config", summary="c"),
]


def test_attribute_fault_service_when_server_errors() -> None:
    out = correlate.correlate_attribute_fault(
        AttributeFaultInput(
            observations=[
                FaultObservation(service="payment", role="server", error_count=36),
                FaultObservation(service="checkout", role="client", callee="payment", error_count=36),
            ]
        ),
        store=None,
    )
    assert out.kind == "service"
    assert out.service == "payment"


def test_attribute_fault_edge_when_only_client_errors() -> None:
    out = correlate.correlate_attribute_fault(
        AttributeFaultInput(
            observations=[
                FaultObservation(service="checkout", role="client", callee="payment", error_count=37),
            ]
        ),
        store=None,
    )
    assert out.kind == "edge"
    assert out.caller == "checkout"
    assert out.callee == "payment"


def test_timeline_orders_and_flags_changes_before_onset() -> None:
    out = correlate.correlate_timeline(
        TimelineInput(onset_second=405, changes=_CHANGES), store=None
    )
    assert [e.second for e in out.entries] == sorted(e.second for e in out.entries)
    assert "chg_0003" in out.changes_before_onset
    assert any(e.kind == "onset" for e in out.entries)


def test_report_root_cause_accepts_valid_report() -> None:
    rpt = RootCauseReport(
        root_cause=RootCause(kind="service", type="payment_charge_failure", service="payment"),
        culprit_change_id="chg_0003",
        ruled_out_change_ids=["chg_0001", "chg_0002"],
        evidence=["payment server spans error after onset"],
    )
    out = report.report_root_cause(rpt, store=None)
    assert isinstance(out, ReportAck)
    assert out.accepted is True


def test_report_tool_input_requires_kind_and_type() -> None:
    with pytest.raises(ValidationError):
        RootCauseReport(
            root_cause=RootCause(service="payment"),  # missing kind + type
            culprit_change_id="chg_0003",
        )


def test_report_tool_is_registered() -> None:
    assert "report_root_cause" in REGISTRY.names()
