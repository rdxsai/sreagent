from __future__ import annotations

import json
from pathlib import Path

from labs.otel.validator import QualityGates
from labs.otel.workflow import (
    MetricQuery,
    PrometheusMatrixCapture,
    RawTelemetryCapture,
    RecordingOptions,
    TelemetryClients,
    assemble_recorded_fixture,
    collect_raw_capture,
    load_scenario,
    load_scenarios,
)
from sentinel.fixtures import load_public_fixture
from sentinel.fixtures.schemas import PrivateTruth


CATALOG_PATH = Path(__file__).parents[2] / "labs" / "otel" / "scenarios.yaml"


def test_load_scenarios_reads_catalog() -> None:
    scenarios = load_scenarios(CATALOG_PATH)

    scenario_ids = {scenario.id for scenario in scenarios}
    assert scenario_ids == {
        "payment_failure_001",
        "payment_unreachable_001",
        "recommendation_cache_failure_001",
        "product_catalog_failure_001",
        "ad_high_cpu_001",
        "ad_manual_gc_001",
        "load_generator_flood_homepage_001",
        "kafka_queue_problems_001",
    }
    assert load_scenario(CATALOG_PATH, "payment_unreachable_001").raw_flag_key == (
        "paymentUnreachable"
    )


def test_load_scenario_selects_requested_id() -> None:
    scenario = load_scenario(CATALOG_PATH, "payment_unreachable_001")

    assert scenario.display_name == "Checkout payment dependency unreachable"


def test_collect_raw_capture_queries_backends() -> None:
    prometheus = FakePrometheus()
    jaeger = FakeJaeger()
    opensearch = FakeOpenSearch()
    capture = collect_raw_capture(
        TelemetryClients(
            prometheus=prometheus,
            jaeger=jaeger,
            opensearch=opensearch,
        ),
        window_start_epoch_seconds=1000,
        window_end_epoch_seconds=1120,
        options=RecordingOptions(
            metric_queries=(
                MetricQuery(
                    metric_name="request_error_rate",
                    query="rate(test_metric[1m])",
                    unit="ratio",
                ),
            ),
            trace_services=("checkout",),
            log_limit=10,
            validate_output=False,
        ),
    )

    assert prometheus.calls == [("rate(test_metric[1m])", 1000, 1120, "15s")]
    assert jaeger.calls == [("checkout", 100, 1000, 1120)]
    assert opensearch.calls == [(10, 1000, 1120)]
    assert capture.prometheus_matrices[0].metric_name == "request_error_rate"
    assert capture.jaeger_traces[0]["spans"][0]["spanID"] == "span-1"
    assert capture.opensearch_logs[0]["_source"]["body"] == "dependency call failed"


def test_assemble_recorded_fixture_writes_sealed_public_and_eval_files(tmp_path) -> None:
    scenario = load_scenario(CATALOG_PATH, "payment_unreachable_001")
    report = assemble_recorded_fixture(
        scenario,
        tmp_path,
        _capture(),
        flag_snapshot_before={"flags": {"paymentUnreachable": {"defaultVariant": "off"}}},
        flag_snapshot_after={"flags": {"paymentUnreachable": {"defaultVariant": "on"}}},
        options=RecordingOptions(
            validate_output=True,
            validation_gates=QualityGates(minimum_trace_count=1, minimum_log_count=1),
        ),
    )

    scenario_dir = tmp_path / "payment_unreachable_001"
    public = load_public_fixture(scenario_dir / "public")
    truth = _load_truth(scenario_dir)
    public_text = "\n".join(
        path.read_text(encoding="utf-8") for path in (scenario_dir / "public").iterdir()
    )

    assert report.passed, report.errors
    assert public.manifest.scenario_id == truth.scenario_id
    assert truth.injection.raw_flag_key == "paymentUnreachable"
    assert public.metrics[0].attributes == {"route": "/hipstershop.CheckoutService/PlaceOrder"}
    assert public.traces[0].status == "ERROR"
    assert "paymentUnreachable" not in public_text
    assert (scenario_dir / "eval_only" / "raw_flag_snapshot.before.json").exists()
    assert (scenario_dir / "eval_only" / "raw_flag_snapshot.after.json").exists()


def _capture() -> RawTelemetryCapture:
    return RawTelemetryCapture(
        window_start_epoch_seconds=1000,
        window_end_epoch_seconds=1900,
        prometheus_matrices=(
            PrometheusMatrixCapture(
                metric_name="request_error_rate",
                unit="ratio",
                result={
                    "result": [
                        {
                            "metric": {
                                "service_name": "checkout",
                                "route": "/hipstershop.CheckoutService/PlaceOrder",
                                "feature_flag.key": "paymentUnreachable",
                            },
                            "values": [[1100, "0.01"], [1320, "0.25"]],
                        }
                    ]
                },
            ),
        ),
        jaeger_traces=tuple(FakeJaeger().traces("checkout")),
        opensearch_logs=tuple(FakeOpenSearch().search_logs(size=10)),
    )


def _load_truth(scenario_dir: Path) -> PrivateTruth:
    with (scenario_dir / "eval_only" / "truth.json").open(encoding="utf-8") as handle:
        return PrivateTruth.model_validate(json.load(handle))


class FakePrometheus:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float, float, str]] = []

    def query_range(self, query: str, *, start: float, end: float, step: str) -> dict:
        self.calls.append((query, start, end, step))
        return {"result": []}


class FakeJaeger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, float | None, float | None]] = []

    def services(self) -> list[str]:
        return ["checkout"]

    def traces(
        self,
        service: str,
        *,
        limit: int = 100,
        start_epoch_seconds: float | None = None,
        end_epoch_seconds: float | None = None,
    ) -> list[dict]:
        self.calls.append((service, limit, start_epoch_seconds, end_epoch_seconds))
        return [
            {
                "processes": {"p1": {"serviceName": service}},
                "spans": [
                    {
                        "traceID": "trace-1",
                        "spanID": "span-1",
                        "processID": "p1",
                        "operationName": "PlaceOrder",
                        "startTime": 1_320_000_000,
                        "duration": 84_000,
                        "tags": [
                            {"key": "otel.status_code", "value": "ERROR"},
                            {"key": "feature_flag.key", "value": "paymentUnreachable"},
                        ],
                        "references": [],
                    }
                ],
            }
        ]


class FakeOpenSearch:
    def __init__(self) -> None:
        self.calls: list[tuple[int, float | None, float | None]] = []

    def search_logs(
        self,
        *,
        size: int = 100,
        start_epoch_seconds: float | None = None,
        end_epoch_seconds: float | None = None,
    ) -> list[dict]:
        self.calls.append((size, start_epoch_seconds, end_epoch_seconds))
        return [
            {
                "_source": {
                    "observedTimestamp": "1970-01-01T00:22:00Z",
                    "body": "dependency call failed",
                    "severityText": "ERROR",
                    "traceId": "trace-1",
                    "resource": {
                        "service.name": "checkout",
                        "feature_flag.key": "paymentUnreachable",
                    },
                    "attributes": {"dependency": "payment"},
                }
            }
        ]
