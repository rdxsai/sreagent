"""Golden + leak-safety tests for the runbook tools."""

from __future__ import annotations

from sentinel.tools import runbooks
from sentinel.tools.models import RunbookGetInput, RunbookSearchInput


def test_search_matches_saturation() -> None:
    out = runbooks.runbook_search(RunbookSearchInput(query="cpu saturation"), None)
    assert out.matches and out.matches[0].id == "rb-saturation"


def test_search_matches_errors_and_latency() -> None:
    assert runbooks.runbook_search(RunbookSearchInput(query="errors failing"), None).matches[0].id == "rb-errors"
    assert runbooks.runbook_search(RunbookSearchInput(query="latency slowdown"), None).matches[0].id == "rb-latency"


def test_search_falls_back_to_umbrella() -> None:
    out = runbooks.runbook_search(RunbookSearchInput(query="zzzznomatch"), None)
    assert out.matches and out.matches[0].id == "rb-degradation"


def test_get_returns_steps() -> None:
    out = runbooks.runbook_get(RunbookGetInput(runbook_id="rb-saturation"), None)
    assert out.runbook is not None and out.runbook.steps


def test_get_unknown_returns_note() -> None:
    out = runbooks.runbook_get(RunbookGetInput(runbook_id="rb-nope"), None)
    assert out.runbook is None and out.note


def test_runbooks_are_leak_safe() -> None:
    # generic procedures only: no service names, change ids, fault-type slugs, or tool names
    banned = (
        "payment", "checkout", "product-catalog", "recommendation", "cart_",
        "chg_", "traces_", "metrics_", "logs_", "report_root_cause", "_origin",
        "payment_charge_failure", "cpu_saturation",
    )
    for rb in runbooks._RUNBOOKS:
        text = (rb["title"] + " " + rb["when"] + " " + " ".join(rb["steps"])).lower()
        for token in banned:
            assert token not in text, f"runbook {rb['id']} leaks {token!r}"
