"""Tests for the manager/investigator tool scoping (isolation boundaries)."""

from __future__ import annotations

import sentinel.tools  # noqa: F401  (register the catalog)
from sentinel.agent.investigator import (
    change_investigator_tool_names,
    investigator_tool_names,
    manager_tool_names,
)

_ORCHESTRATION = {"investigate_service", "investigate_parallel", "investigate_change"}
_WORKER_TERMINALS = {"report_finding", "report_change_verdict"}


def test_manager_has_orchestration_but_not_worker_terminals() -> None:
    names = manager_tool_names()
    assert _ORCHESTRATION <= names  # manager can delegate
    assert "report_root_cause" in names  # manager owns the final report
    assert not (_WORKER_TERMINALS & names)  # but not the workers' terminals


def test_investigator_cannot_recurse_or_own_the_report() -> None:
    names = investigator_tool_names()
    assert "report_finding" in names  # its own terminal
    assert not (_ORCHESTRATION & names)  # cannot spawn more subagents
    assert "report_root_cause" not in names  # cannot end the whole run
    assert {"traces_error_origin", "metrics_summary_all", "changes_search"} <= names  # has analysis tools


def test_change_investigator_is_scoped_to_changes_and_traces() -> None:
    names = change_investigator_tool_names()
    assert "report_change_verdict" in names
    assert {"changes_search", "changes_rank_culprit"} <= names
    assert not (_ORCHESTRATION & names)
    assert "report_root_cause" not in names
    assert "metrics_summary_all" not in names  # out of scope for a change assessment
