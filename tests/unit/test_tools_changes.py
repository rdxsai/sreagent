"""Golden tests for the changes tools."""

from __future__ import annotations

from pathlib import Path

from sentinel.tools import changes
from sentinel.tools.models import ChangesLookbackInput, ChangesSearchInput
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
FAILURE = FixtureStore(ROOT / "fixtures" / "payment_failure_001" / "public")


def test_changes_search_returns_all_three() -> None:
    out = changes.changes_search(ChangesSearchInput(), FAILURE)
    assert {c.id for c in out.changes} == {"chg_0001", "chg_0002", "chg_0003"}


def test_lookback_nearest_change_before_onset_is_culprit() -> None:
    out = changes.changes_lookback(ChangesLookbackInput(onset_second=405), FAILURE)
    assert out.changes  # all strictly before onset, nearest first
    assert out.changes[0].id == "chg_0003"
    assert all(c.time < 405 for c in out.changes)


def test_lookback_excludes_changes_at_or_after_onset() -> None:
    out = changes.changes_lookback(ChangesLookbackInput(onset_second=290), FAILURE)
    ids = {c.id for c in out.changes}
    assert ids == {"chg_0001", "chg_0002"}  # chg_0003 at t=300 is not before 290


def test_lookback_service_filter() -> None:
    out = changes.changes_lookback(
        ChangesLookbackInput(onset_second=405, service="payment"), FAILURE
    )
    assert {c.id for c in out.changes} == {"chg_0003"}
