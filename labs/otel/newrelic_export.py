"""Generate the demo collector's extras config to stream OTLP to New Relic.

The demo merges otelcol-config-extras.yml over otelcol-config.yml and yaml
arrays are replaced wholesale, so this generator restates each pipeline's
receivers/processors/exporters with the New Relic exporter appended. The
rendered file contains the license key; it lives only inside the (gitignored)
demo checkout.
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


def build_extras(base_config: dict, license_key: str) -> dict:
    pipelines: dict = {}
    for name, pipeline in base_config["service"]["pipelines"].items():
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
    base_path = demo_root / CONFIG_DIR / "otelcol-config.yml"
    base_config = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    extras = build_extras(base_config, license_key)
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
