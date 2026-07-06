"""The extras config must restate pipelines as merged across config layers
(collector confmap: maps merge, lists replace), append the New Relic exporter
to traces/metrics/logs only, and add delta conversion to metrics only."""

from labs.otel.newrelic_export import build_extras, merge_configs

BASE = {
    "exporters": {"otlp": {"endpoint": "jaeger:4317"}},
    "processors": {"batch": {}},
    "service": {
        "pipelines": {
            "traces": {"receivers": ["otlp"], "processors": ["batch"], "exporters": ["debug"]},
            "metrics": {"receivers": ["otlp"], "processors": ["batch"], "exporters": ["debug"]},
            "logs": {"receivers": ["otlp"], "processors": ["batch"], "exporters": ["debug"]},
            "profiles": {"receivers": ["otlp"], "processors": ["batch"], "exporters": ["debug"]},
        }
    },
}
FULL = {
    "service": {
        "pipelines": {
            "metrics": {"receivers": ["otlp", "kafkametrics"]},
        }
    },
}
OBSERVABILITY = {
    "service": {
        "pipelines": {
            "traces": {"exporters": ["debug", "otlp_grpc/jaeger"]},
            "metrics": {"exporters": ["debug", "otlp_http/prometheus"]},
            "logs": {"exporters": ["debug", "opensearch"]},
        }
    },
}


def merged():
    return merge_configs([BASE, FULL, OBSERVABILITY])


def test_merge_configs_replaces_lists_and_merges_maps():
    config = merged()
    metrics = config["service"]["pipelines"]["metrics"]
    assert metrics["receivers"] == ["otlp", "kafkametrics"]  # full replaced the list
    assert metrics["processors"] == ["batch"]  # inherited from base
    assert metrics["exporters"] == ["debug", "otlp_http/prometheus"]  # observability replaced


def test_build_extras_appends_exporter_to_merged_pipelines():
    extras = build_extras(merged(), "lic-key")
    pipelines = extras["service"]["pipelines"]
    assert pipelines["traces"]["exporters"] == ["debug", "otlp_grpc/jaeger", "otlphttp/newrelic"]
    assert pipelines["metrics"]["exporters"] == ["debug", "otlp_http/prometheus", "otlphttp/newrelic"]
    assert pipelines["logs"]["exporters"] == ["debug", "opensearch", "otlphttp/newrelic"]
    assert pipelines["metrics"]["receivers"] == ["otlp", "kafkametrics"]


def test_build_extras_skips_profiles_pipeline():
    extras = build_extras(merged(), "lic-key")
    assert "profiles" not in extras["service"]["pipelines"]


def test_build_extras_configures_endpoint_and_key():
    exporter = build_extras(merged(), "lic-key")["exporters"]["otlphttp/newrelic"]
    assert exporter["endpoint"] == "https://otlp.nr-data.net"
    assert exporter["headers"]["api-key"] == "lic-key"


def test_build_extras_adds_delta_conversion_to_metrics_only():
    extras = build_extras(merged(), "lic-key")
    assert extras["processors"]["cumulativetodelta"] == {}
    pipelines = extras["service"]["pipelines"]
    assert pipelines["metrics"]["processors"] == ["batch", "cumulativetodelta"]
    assert pipelines["traces"]["processors"] == ["batch"]


def test_build_extras_preserves_receivers():
    extras = build_extras(merged(), "lic-key")
    assert extras["service"]["pipelines"]["traces"]["receivers"] == ["otlp"]
