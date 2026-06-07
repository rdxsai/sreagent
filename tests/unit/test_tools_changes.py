"""Golden tests for the changes tools."""

from __future__ import annotations

from pathlib import Path

from sentinel.tools import changes
from sentinel.tools.models import ChangesLookbackInput, ChangesSearchInput
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
FAILURE = FixtureStore(ROOT / "fixtures" / "payment_failure_001" / "public")


def test_changes_search_returns_all_six() -> None:
    out = changes.changes_search(ChangesSearchInput(), FAILURE)
    assert {c.id for c in out.changes} == {
        "chg_0001", "chg_0002", "chg_0003", "chg_0004", "chg_0005", "chg_0006",
    }


def test_lookback_returns_changes_before_onset_nearest_first() -> None:
    out = changes.changes_lookback(ChangesLookbackInput(onset_second=405), FAILURE)
    assert out.changes  # all strictly before onset, nearest first
    assert all(c.time < 405 for c in out.changes)
    # The nearest change before onset is the same-service decoy chg_0004 (@350), NOT
    # the culprit chg_0003 (@300): the richer decoy set defeats a naive "nearest
    # change is the cause" heuristic, so the agent must reason with attribution too.
    assert out.changes[0].id == "chg_0004"
    assert "chg_0003" in {c.id for c in out.changes}


def test_lookback_excludes_changes_at_or_after_onset() -> None:
    out = changes.changes_lookback(ChangesLookbackInput(onset_second=290), FAILURE)
    ids = {c.id for c in out.changes}
    assert ids == {"chg_0001", "chg_0002", "chg_0005"}  # only changes strictly before 290


def test_lookback_service_filter() -> None:
    out = changes.changes_lookback(
        ChangesLookbackInput(onset_second=405, service="payment"), FAILURE
    )
    # Both payment changes before onset: culprit chg_0003 (@300) and decoy chg_0004 (@350).
    assert {c.id for c in out.changes} == {"chg_0003", "chg_0004"}
