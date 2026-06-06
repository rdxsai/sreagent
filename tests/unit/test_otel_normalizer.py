from __future__ import annotations

from labs.otel.normalizer import (
    normalize_jaeger_traces,
    normalize_opensearch_logs,
    normalize_prometheus_matrix,
)


def test_normalize_prometheus_matrix_drops_feature_flag_labels() -> None:
    rows = normalize_prometheus_matrix(
        {
            "result": [
                {
                    "metric": {
                        "__name__": "calls_total",
                        "service_name": "checkout",
                        "route": "/api/checkout",
                        "feature_flag_key": "paymentUnreachable",
                    },
                    "values": [[1000, "1"], [1010, "3"]],
                }
            ],
        },
        metric_name="request_error_rate",
        unit="ratio",
        window_start_epoch_seconds=1000,
    )

    assert [row.time for row in rows] == [0, 10]
    assert rows[0].service == "checkout"
    assert rows[0].attributes == {"route": "/api/checkout"}


def test_normalize_prometheus_matrix_drops_non_finite_values() -> None:
    rows = normalize_prometheus_matrix(
        {
            "result": [
                {
                    "metric": {"service_name": "checkout"},
                    "values": [[1000, "NaN"], [1010, "1"]],
                }
            ]
        },
        metric_name="request_error_rate",
        unit="ratio",
        window_start_epoch_seconds=1000,
    )

    assert [row.time for row in rows] == [10]


def test_normalize_jaeger_traces_maps_spans() -> None:
    rows = normalize_jaeger_traces(
        [
            {
                "processes": {"p1": {"serviceName": "checkout"}},
                "spans": [
                    {
                        "traceID": "trace-1",
                        "spanID": "span-1",
                        "processID": "p1",
                        "operationName": "PlaceOrder",
                        "startTime": 1_000_000_000,
                        "duration": 42_000,
                        "tags": [
                            {"key": "otel.status_code", "value": "ERROR"},
                            {"key": "feature_flag.key", "value": "paymentUnreachable"},
                        ],
                        "references": [],
                    }
                ],
            }
        ],
        window_start_epoch_seconds=1000,
    )

    assert len(rows) == 1
    assert rows[0].time == 0
    assert rows[0].duration_ms == 42
    assert rows[0].status == "ERROR"
    assert "feature_flag.key" not in rows[0].attributes


def test_normalize_jaeger_traces_marks_nonzero_grpc_status_as_error() -> None:
    rows = normalize_jaeger_traces(
        [
            {
                "processes": {"p1": {"serviceName": "payment"}},
                "spans": [
                    {
                        "traceID": "trace-1",
                        "spanID": "span-1",
                        "processID": "p1",
                        "operationName": "Charge",
                        "startTime": 1_000_000_000,
                        "duration": 42_000,
                        "tags": [{"key": "rpc.grpc.status_code", "value": 14}],
                        "references": [],
                    }
                ],
            }
        ],
        window_start_epoch_seconds=1000,
    )

    assert rows[0].status == "ERROR"


def test_normalize_jaeger_traces_drops_nested_feature_flag_attributes() -> None:
    rows = normalize_jaeger_traces(
        [
            {
                "processes": {"p1": {"serviceName": "recommendation"}},
                "spans": [
                    {
                        "traceID": "trace-1",
                        "spanID": "span-1",
                        "processID": "p1",
                        "operationName": "ListRecommendations",
                        "startTime": 1_000_000_000,
                        "duration": 42_000,
                        "tags": [
                            {"key": "demo.feature_flag.recommendation_cache", "value": False},
                            {"key": "demo.product.count", "value": 10},
                        ],
                        "references": [],
                    }
                ],
            }
        ],
        window_start_epoch_seconds=1000,
    )

    assert rows[0].attributes == {"demo.product.count": 10}


def test_normalize_jaeger_traces_drops_raw_flag_attribute_values() -> None:
    rows = normalize_jaeger_traces(
        [
            {
                "processes": {"p1": {"serviceName": "payment"}},
                "spans": [
                    {
                        "traceID": "trace-1",
                        "spanID": "span-1",
                        "processID": "p1",
                        "operationName": "Charge",
                        "startTime": 1_000_000_000,
                        "duration": 42_000,
                        "tags": [
                            {"key": "demo.flag_name", "value": "paymentFailure"},
                            {"key": "rpc.system", "value": "grpc"},
                        ],
                        "references": [],
                    }
                ],
            }
        ],
        window_start_epoch_seconds=1000,
    )

    assert rows[0].attributes == {"rpc.system": "grpc"}


def test_normalize_jaeger_traces_keeps_rich_cues_when_stripping_flag_identity() -> None:
    rows = normalize_jaeger_traces(
        [
            {
                "processes": {"p1": {"serviceName": "payment"}},
                "spans": [
                    {
                        "traceID": "trace-1",
                        "spanID": "span-1",
                        "processID": "p1",
                        "operationName": "Charge",
                        "startTime": 1_000_000_000,
                        "duration": 42_000,
                        "tags": [
                            {"key": "feature_flag.key", "value": "paymentFailure"},
                            {"key": "feature_flag.result.variant", "value": "on"},
                            {"key": "rpc.grpc.status_code", "value": 2},
                            {"key": "exception.message", "value": "charge declined"},
                            {"key": "app.payment.card_type", "value": "visa"},
                        ],
                        "references": [],
                    }
                ],
            }
        ],
        window_start_epoch_seconds=1000,
    )

    # The fault-injection flag identity is removed, but the symptom cues an SRE
    # would actually reason over are all preserved.
    assert rows[0].status == "ERROR"
    assert rows[0].operation == "Charge"
    assert rows[0].duration_ms == 42.0
    assert rows[0].attributes == {
        "rpc.grpc.status_code": 2,
        "exception.message": "charge declined",
        "app.payment.card_type": "visa",
    }


def test_normalize_opensearch_logs_maps_source() -> None:
    rows = normalize_opensearch_logs(
        [
            {
                "_source": {
                    "observedTimestamp": "1970-01-01T00:16:45.123456789Z",
                    "body": "dependency call failed",
                    "severity": {"text": "ERROR", "number": 17},
                    "traceId": "trace-1",
                    "resource": {
                        "service.name": "checkout",
                        "feature_flag.key": "paymentUnreachable",
                    },
                    "attributes": {"dependency": "payment"},
                }
            }
        ],
        window_start_epoch_seconds=1000,
    )

    assert len(rows) == 1
    assert rows[0].time == 5
    assert rows[0].service == "checkout"
    assert rows[0].trace_id == "trace-1"
    assert rows[0].attributes["dependency"] == "payment"
    assert "feature_flag.key" not in rows[0].attributes


def test_normalize_opensearch_logs_parses_variable_precision_timestamps() -> None:
    # OpenSearch emits variable fractional-second precision; all must parse on 3.10.
    samples = {
        "2026-06-06T00:16:45.84349+00:00": 5,  # 5-digit fraction (the crash case)
        "2026-06-06T00:16:45.567955584Z": 5,  # 9-digit nanoseconds
        "2026-06-06T00:16:45.5Z": 5,  # 1-digit fraction
        "2026-06-06T00:16:45Z": 5,  # no fraction
    }
    for timestamp in samples:
        rows = normalize_opensearch_logs(
            [
                {
                    "_source": {
                        "observedTimestamp": timestamp,
                        "body": "charge declined",
                        "resource": {"service.name": "payment"},
                    }
                }
            ],
            window_start_epoch_seconds=1780791400.0,
        )
        assert len(rows) == 1, timestamp
        assert rows[0].time >= 0, timestamp


def test_normalize_opensearch_logs_drops_raw_flag_messages() -> None:
    rows = normalize_opensearch_logs(
        [
            {
                "_source": {
                    "observedTimestamp": "1970-01-01T00:16:45Z",
                    "body": "FeatureFlag 'kafkaQueueProblems' is enabled",
                    "resource": {"service.name": "fraud-detection"},
                }
            }
        ],
        window_start_epoch_seconds=1000,
    )

    assert rows == []
