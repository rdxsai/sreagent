from __future__ import annotations
import json
from pathlib import Path
import pytest
from labs.otel.alerting.coverage import (
    CoverageError, assert_coverage_invariants, build_coverage_matrix, write_coverage_matrix,
)


def test_healthy_matrix_passes():
    matrix = {"s1": ["A", "B"], "s2": ["A", "B"], "s3": ["A", "C"], "s4": ["A", "C"]}
    assert_coverage_invariants(matrix)  # no raise


def test_e1_empty_scenario_fails():
    with pytest.raises(CoverageError) as exc:
        assert_coverage_invariants({"s1": [], "s2": ["A"], "s3": ["A"]})
    assert "E1" in str(exc.value)


def test_e3_unique_set_fails_even_when_each_alert_is_shared():
    # A,B,C each fire for 3 scenarios (E2 ok), but every SET is unique (E3 fails).
    matrix = {"s1": ["A", "B"], "s2": ["A", "C"], "s3": ["B", "C"], "s4": ["A", "B", "C"]}
    with pytest.raises(CoverageError) as exc:
        assert_coverage_invariants(matrix)
    assert "E3" in str(exc.value)


def test_e2_unshared_alert_fails():
    with pytest.raises(CoverageError) as exc:
        assert_coverage_invariants({"s1": ["A", "B"], "s2": ["A", "B"], "s3": ["A", "C"]})
    assert "E2" in str(exc.value)  # C fires for only s3


def test_build_matrix_reads_manifests(tmp_path):
    for sid, names in {"s1": ["A", "B"], "s2": ["A"]}.items():
        d = tmp_path / sid / "public"
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({
            "scenario_id": sid,
            "alerts": [{"alertname": n} for n in names],
        }), encoding="utf-8")
    matrix = build_coverage_matrix([tmp_path / "s1", tmp_path / "s2"])
    assert matrix == {"s1": ["A", "B"], "s2": ["A"]}


def test_write_coverage_matrix_roundtrips(tmp_path):
    out = tmp_path / "coverage_matrix.json"
    write_coverage_matrix({"s1": ["A"]}, out)
    assert json.loads(out.read_text()) == {"s1": ["A"]}
