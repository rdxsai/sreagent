from __future__ import annotations

import json
from pathlib import Path

import pytest

from labs.otel.fingerprint import (
    fingerprint_fixture,
    fingerprints_are_trivially_distinct,
)
from labs.otel.redactor import assert_no_banned_tokens
from labs.otel.validator import QualityGates, validate_fixture
from sentinel.fixtures import FixtureAccessError, load_public_fixture
from sentinel.fixtures.schemas import PrivateTruth


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "otel"


def test_public_fixtures_have_no_banned_tokens() -> None:
    for scenario_dir in _scenario_dirs():
        assert_no_banned_tokens(scenario_dir / "public")


def test_tools_cannot_read_eval_only() -> None:
    scenario_dir = _scenario_dirs()[0]
    with pytest.raises(FixtureAccessError):
        load_public_fixture(scenario_dir / "eval_only")
    with pytest.raises(FixtureAccessError):
        load_public_fixture(scenario_dir)


def test_truth_exists_for_every_public_fixture() -> None:
    for scenario_dir in _scenario_dirs():
        truth = _load_truth(scenario_dir)
        public = load_public_fixture(scenario_dir / "public")
        assert truth.scenario_id == public.manifest.scenario_id


def test_every_fixture_has_decoy_change() -> None:
    for scenario_dir in _scenario_dirs():
        truth = _load_truth(scenario_dir)
        public = load_public_fixture(scenario_dir / "public")
        changes = {change.id: change for change in public.changes}
        culprit = changes[truth.culprit_change_id]
        assert truth.decoy_change_ids
        for decoy_id in truth.decoy_change_ids:
            assert decoy_id in changes
            assert changes[decoy_id].model_dump() != culprit.model_dump()


def test_replay_is_deterministic() -> None:
    for scenario_dir in _scenario_dirs():
        first = load_public_fixture(scenario_dir / "public")
        second = load_public_fixture(scenario_dir / "public")
        assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_recorded_fixture_passes_quality_gates() -> None:
    gates = QualityGates(minimum_trace_count=1, minimum_log_count=1)
    for scenario_dir in _scenario_dirs():
        report = validate_fixture(scenario_dir, gates)
        assert report.passed, report.errors


def test_confusable_pair_fingerprints_are_not_trivially_distinct() -> None:
    left = fingerprint_fixture(
        FIXTURE_ROOT / "payment_unreachable_001" / "public",
        onset_second=60,
    )
    right = fingerprint_fixture(
        FIXTURE_ROOT / "payment_failure_001" / "public",
        onset_second=60,
    )
    assert not fingerprints_are_trivially_distinct(left, right)


def test_public_manifest_does_not_include_raw_flag_key() -> None:
    for scenario_dir in _scenario_dirs():
        manifest = (scenario_dir / "public" / "manifest.json").read_text(
            encoding="utf-8"
        )
        assert "raw_flag_key" not in manifest
        assert _load_truth(scenario_dir).injection.raw_flag_key not in manifest


def _scenario_dirs() -> list[Path]:
    return sorted(path for path in FIXTURE_ROOT.iterdir() if path.is_dir())


def _load_truth(scenario_dir: Path) -> PrivateTruth:
    with (scenario_dir / "eval_only" / "truth.json").open(encoding="utf-8") as handle:
        return PrivateTruth.model_validate(json.load(handle))
