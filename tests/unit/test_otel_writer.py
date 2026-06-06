from __future__ import annotations

import pytest

from labs.otel.redactor import RedactionError
from labs.otel.writer import write_fixture
from sentinel.fixtures import load_public_fixture
from sentinel.fixtures.schemas import (
    ChangeEvent,
    InjectionMetadata,
    LogRow,
    MetricRow,
    PrivateTruth,
    PublicManifest,
    RootCause,
    TimeWindow,
    Topology,
    TraceRow,
)


def test_write_fixture_separates_public_and_eval_truth(tmp_path) -> None:
    scenario_dir = tmp_path / "scenario"

    write_fixture(
        scenario_dir,
        manifest=_manifest(),
        topology=Topology(services=["checkout", "payment"], edges=[]),
        metrics=[
            MetricRow(
                time=10,
                service="checkout",
                metric="request_error_rate",
                value=0.01,
                unit="ratio",
            )
        ],
        logs=[
            LogRow(
                time=12,
                service="checkout",
                severity="ERROR",
                message="dependency call failed",
            )
        ],
        traces=[
            TraceRow(
                trace_id="trace-1",
                span_id="span-1",
                time=12,
                service="checkout",
                operation="PlaceOrder",
                duration_ms=42,
                status="ERROR",
            )
        ],
        changes=[_change("chg_0001", "checkout")],
        truth=_truth(),
        injection_log={"raw_flag_key": "paymentUnreachable"},
        eval_only_json={"raw_flag_snapshot.before.json": {"flags": {}}},
    )

    public = load_public_fixture(scenario_dir / "public")

    assert public.manifest.scenario_id == "scenario_001"
    assert (scenario_dir / "eval_only" / "truth.json").exists()
    assert (scenario_dir / "eval_only" / "raw_flag_snapshot.before.json").exists()
    assert "paymentUnreachable" not in (scenario_dir / "public" / "manifest.json").read_text(
        encoding="utf-8"
    )


def test_write_fixture_rejects_public_flag_leaks(tmp_path) -> None:
    with pytest.raises(RedactionError):
        write_fixture(
            tmp_path / "scenario",
            manifest=_manifest(symptom="paymentUnreachable happened"),
            topology=Topology(services=["checkout"], edges=[]),
            metrics=[],
            logs=[],
            traces=[],
            changes=[_change("chg_0001", "checkout")],
            truth=_truth(),
        )


def _manifest(symptom: str = "Checkout failures increased.") -> PublicManifest:
    return PublicManifest(
        scenario_id="scenario_001",
        source="opentelemetry-demo",
        time_unit="second",
        window=TimeWindow(start=0, end=120),
        symptom=symptom,
        available_signals=["metrics", "logs", "traces", "topology", "changes"],
    )


def _truth() -> PrivateTruth:
    return PrivateTruth(
        scenario_id="scenario_001",
        injection=InjectionMetadata(
            raw_flag_key="paymentUnreachable",
            variant="on",
            enabled_at_second=60,
        ),
        root_cause=RootCause(
            kind="edge",
            caller="checkout",
            callee="payment",
            type="dependency_unreachable",
        ),
        culprit_change_id="chg_0001",
        expected_evidence=["checkout errors rise"],
        decoy_change_ids=["chg_0002"],
    )


def _change(change_id: str, service: str) -> ChangeEvent:
    return ChangeEvent(
        id=change_id,
        time=60,
        service=service,
        kind="runtime_config_change",
        summary="dependency routing changed",
    )
