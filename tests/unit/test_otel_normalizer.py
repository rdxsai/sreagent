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
            ]
        },
        metric_name="request_error_rate",
        unit="ratio",
        window_start_epoch_seconds=1000,
    )

    assert [row.time for row in rows] == [0, 10]
    assert rows[0].service == "checkout"
    assert rows[0].attributes == {"route": "/api/checkout"}


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


def test_normalize_opensearch_logs_maps_source() -> None:
    rows = normalize_opensearch_logs(
        [
            {
                "_source": {
                    "observedTimestamp": "1970-01-01T00:16:45Z",
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
        ],
        window_start_epoch_seconds=1000,
    )

    assert len(rows) == 1
    assert rows[0].time == 5
    assert rows[0].service == "checkout"
    assert rows[0].trace_id == "trace-1"
    assert rows[0].attributes["dependency"] == "payment"
    assert "feature_flag.key" not in rows[0].attributes
