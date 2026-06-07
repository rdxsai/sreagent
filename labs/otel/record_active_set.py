"""Record the six active scenarios under self-driven load, in one pass.

Drives steady browse + checkout traffic (see loadgen.TrafficDriver) for the whole
run so faults surface against a clean baseline, records each scenario through the
normal sealed pipeline, and handles productCatalogFailure's gated-off targeting.

Usage:
    python -m labs.otel.record_active_set                 # all six
    python -m labs.otel.record_active_set ad_high_cpu_001 # one (e.g. a dry-run)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from labs.otel.flagd import FlagdClient
from labs.otel.loadgen import (
    TrafficDriver,
    remove_product_catalog_targeting,
    restore_product_catalog_targeting,
)
from labs.otel.recorder import RecorderError
from labs.otel.workflow import (
    RecordingOptions,
    TelemetryClients,
    load_scenario,
    record_scenario_definition,
)

CONFIG = Path("labs/otel/scenarios.yaml")
OUTPUT_DIR = Path("fixtures")
FLAG_BASE_URL = "http://localhost:18080/feature"
RAMP_SECONDS = 60

ACTIVE = [
    "payment_failure_001",
    "payment_unreachable_001",
    "product_catalog_failure_001",
    "cart_failure_001",
    "ad_manual_gc_001",
    "ad_high_cpu_001",
]


def _preflight(tc: TelemetryClients) -> None:
    """Fail fast if a telemetry backend is unreachable, before any long recording."""
    tc.prometheus.query_range("vector(1)", start=time.time() - 60, end=time.time(), step="15s")
    services = tc.jaeger.services()
    if not services:
        raise RecorderError("Jaeger returned no services")
    tc.opensearch.search_logs(size=1)
    FlagdClient(base_url=FLAG_BASE_URL).read()


def record_one(sid: str, tc: TelemetryClients) -> tuple[bool, list[str]]:
    scenario = load_scenario(CONFIG, sid)
    fc = FlagdClient(base_url=FLAG_BASE_URL)
    saved = None
    if scenario.raw_flag_key == "productCatalogFailure":
        saved = remove_product_catalog_targeting(fc)
    try:
        report = record_scenario_definition(
            scenario,
            OUTPUT_DIR,
            flag_client=fc,
            telemetry_clients=tc,
            options=RecordingOptions(),
        )
    finally:
        if saved is not None:
            restore_product_catalog_targeting(fc, saved)
    return report.passed, list(report.errors)


def main(argv: list[str] | None = None) -> int:
    ids = list(argv) if argv else ACTIVE
    unknown = [s for s in ids if s not in ACTIVE]
    if unknown:
        print(f"unknown scenario id(s): {unknown}; known: {ACTIVE}", file=sys.stderr)
        return 2

    tc = TelemetryClients.from_environment()
    print("preflight: checking telemetry backends ...", flush=True)
    _preflight(tc)
    print("preflight ok", flush=True)

    driver = TrafficDriver(browse_threads=6, checkout_threads=3)
    driver.start()
    print(f"traffic started; ramping {RAMP_SECONDS}s", flush=True)
    time.sleep(RAMP_SECONDS)

    results: dict[str, tuple[bool, list[str]]] = {}
    try:
        for sid in ids:
            print(f"\n=== recording {sid} ===", flush=True)
            try:
                passed, errors = record_one(sid, tc)
            except Exception as exc:  # one scenario failing must not abort the batch
                passed, errors = False, [f"{type(exc).__name__}: {exc}"]
            results[sid] = (passed, errors)
            print(f"  {sid}: passed={passed} errors={errors}", flush=True)
    finally:
        driver.stop()

    ok = sum(1 for passed, _ in results.values() if passed)
    print(f"\nRECORDED {ok}/{len(results)} scenarios passed validation", flush=True)
    for sid, (passed, errors) in results.items():
        print(f"  {sid}: {'OK' if passed else 'FAIL ' + str(errors)}", flush=True)
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
