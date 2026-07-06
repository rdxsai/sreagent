"""Generate the demo collector's extras config to stream OTLP to New Relic.

The demo collector is started with a chain of --config files (base, full,
observability, extras). The collector's confmap merges maps recursively but
replaces lists wholesale, so the extras file must restate each pipeline as
merged from every earlier layer, with the New Relic exporter appended. The
NR exporter is added to traces/metrics/logs pipelines only: New Relic's OTLP
endpoint does not accept the profiles signal. The rendered file contains the
license key; it lives only inside the (gitignored) demo checkout.
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

import yaml

from sentinel.newrelic.client import load_env_var

EXPORTER_NAME = "otlphttp/newrelic"
DELTA_PROCESSOR = "cumulativetodelta"
CONFIG_DIR = Path("src") / "otel-collector"
CONFIG_LAYERS = (
    "otelcol-config.yml",
    "otelcol-config-full.yml",
    "otelcol-config-observability.yml",
)
_NR_SIGNALS = ("traces", "metrics", "logs")


def merge_configs(layers: list[dict]) -> dict:
    """Merge config layers with collector confmap semantics: maps merge
    recursively, lists and scalars are replaced by the later layer."""

    def merge(base: object, override: object) -> object:
        if isinstance(base, dict) and isinstance(override, dict):
            out = dict(base)
            for key, value in override.items():
                out[key] = merge(out.get(key), value) if key in out else value
            return out
        return override

    merged: dict = {}
    for layer in layers:
        merged = merge(merged, layer)  # type: ignore[assignment]
    return merged


def build_extras(merged_config: dict, license_key: str) -> dict:
    pipelines: dict = {}
    for name, pipeline in merged_config["service"]["pipelines"].items():
        if not name.startswith(_NR_SIGNALS):
            continue
        restated = copy.deepcopy(pipeline)
        exporters = list(restated.get("exporters", []))
        if EXPORTER_NAME not in exporters:
            exporters.append(EXPORTER_NAME)
        restated["exporters"] = exporters
        if name.startswith("metrics"):
            processors = list(restated.get("processors", []))
            if DELTA_PROCESSOR not in processors:
                processors.append(DELTA_PROCESSOR)
            restated["processors"] = processors
        pipelines[name] = restated
    return {
        "exporters": {
            EXPORTER_NAME: {
                "endpoint": "https://otlp.nr-data.net",
                "headers": {"api-key": license_key},
            }
        },
        "processors": {DELTA_PROCESSOR: {}},
        "service": {"pipelines": pipelines},
    }


def apply(demo_root: Path) -> Path:
    license_key = load_env_var("NEW_RELIC_LICENSE_KEY")
    if not license_key:
        raise SystemExit("NEW_RELIC_LICENSE_KEY not set (env or .env)")
    layers = [
        yaml.safe_load((demo_root / CONFIG_DIR / layer).read_text(encoding="utf-8"))
        for layer in CONFIG_LAYERS
        if (demo_root / CONFIG_DIR / layer).exists()
    ]
    extras = build_extras(merge_configs(layers), license_key)
    out_path = demo_root / CONFIG_DIR / "otelcol-config-extras.yml"
    out_path.write_text(yaml.safe_dump(extras, sort_keys=False), encoding="utf-8")
    out_path.chmod(0o600)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-root", type=Path, required=True)
    args = parser.parse_args()
    out = apply(args.demo_root)
    print(f"wrote {out}")
    print("restart the collector: docker compose up -d --force-recreate otel-collector")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
