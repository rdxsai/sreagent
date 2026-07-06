"""The extras config must restate full pipelines (yaml merge replaces arrays)
with the New Relic exporter appended, and delta conversion on metrics only."""

from labs.otel.newrelic_export import build_extras

BASE = {
    "exporters": {"otlp": {"endpoint": "jaeger:4317"}},
    "processors": {"batch": {}},
    "service": {
        "pipelines": {
            "traces": {"receivers": ["otlp"], "processors": ["batch"], "exporters": ["otlp"]},
            "metrics": {"receivers": ["otlp"], "processors": ["batch"], "exporters": ["prometheus"]},
            "logs": {"receivers": ["otlp"], "processors": ["batch"], "exporters": ["opensearch"]},
        }
    },
}


def test_build_extras_appends_exporter_to_all_pipelines():
    extras = build_extras(BASE, "lic-key")
    pipelines = extras["service"]["pipelines"]
    assert pipelines["traces"]["exporters"] == ["otlp", "otlphttp/newrelic"]
    assert pipelines["metrics"]["exporters"] == ["prometheus", "otlphttp/newrelic"]
    assert pipelines["logs"]["exporters"] == ["opensearch", "otlphttp/newrelic"]


def test_build_extras_configures_endpoint_and_key():
    exporter = build_extras(BASE, "lic-key")["exporters"]["otlphttp/newrelic"]
    assert exporter["endpoint"] == "https://otlp.nr-data.net"
    assert exporter["headers"]["api-key"] == "lic-key"


def test_build_extras_adds_delta_conversion_to_metrics_only():
    extras = build_extras(BASE, "lic-key")
    assert extras["processors"]["cumulativetodelta"] == {}
    pipelines = extras["service"]["pipelines"]
    assert pipelines["metrics"]["processors"] == ["batch", "cumulativetodelta"]
    assert pipelines["traces"]["processors"] == ["batch"]


def test_build_extras_preserves_receivers():
    extras = build_extras(BASE, "lic-key")
    assert extras["service"]["pipelines"]["traces"]["receivers"] == ["otlp"]
