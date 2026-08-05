"""Scenario-bound side effects, per lab: how a fault is injected, cleared, and how
its recovery health (badness) is read. The state machine only ever sees closures,
so both labs (and the test fakes) plug in identically.

  sock_shop: docker-exec CPU hogs, cleared with pkill sweeps
  otel_demo: flagd feature flags, cleared by resetting every flag to off
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from sentinel.api.livelab.lab import Lab
from sentinel.api.livelab.scenarios import Scenario

_FLAGD_URL_ENV = "SENTINEL_OTEL_FLAGD_UI_BASE_URL"
_FLAGD_DEFAULT = "http://localhost:8081/feature"

# the demo's app services at the pinned checkout (infra like flagd/collector excluded)
OTEL_APP_SERVICES: tuple[str, ...] = (
    "load-generator", "frontend-proxy", "frontend", "image-provider", "ad", "cart",
    "checkout", "currency", "product-catalog", "product-reviews", "recommendation",
    "shipping", "quote", "email", "payment",
)


def otel_demo_dir(env=os.environ) -> Path:
    return Path(env.get("SENTINEL_OTEL_DEMO_DIR", str(Path.home() / "otel-demo-sentinel")))


def make_lab(lab_key: str, *, run=subprocess.run, env=os.environ) -> Lab:
    if lab_key == "sock_shop":
        return Lab(run=run)
    demo = otel_demo_dir(env)
    return Lab(run=run, compose_file=demo / "compose.yaml", services=OTEL_APP_SERVICES,
               boot_cmd=["make", "start"], workdir=demo)


def _flagd():
    from labs.otel.flagd import FlagdClient

    return FlagdClient(os.environ.get(_FLAGD_URL_ENV, _FLAGD_DEFAULT))


def make_inject(scenario: Scenario) -> Callable[[], None]:
    if scenario.lab == "sock_shop":
        from labs.sockshop.faults import inject_cpu

        return lambda: inject_cpu(scenario.truth_service, hogs=3)

    from labs.otel.live_incident import SPECS

    spec = SPECS[scenario.id.removeprefix("otel-")]
    # note: specs with targeting_true_variant (productCatalogFailure) need the
    # targeting rewrite before they can join the menu; neither menu scenario does
    return lambda: _flagd().set_flag_variant(spec.raw_flag_key, spec.variant)


def make_clear(scenario: Scenario) -> Callable[[], None]:
    if scenario.lab == "sock_shop":
        from labs.sockshop.faults import clear_cpu

        return lambda: clear_cpu(scenario.truth_service)

    return lambda: _flagd().reset_all_flags("off")


def make_health(scenario: Scenario, nrql: Callable[[str], list[dict]]) -> Callable[[], float | None]:
    """Badness for the scenario (cpu percent, error rate, ...): the recovery
    signal read live from New Relic via the scenario's own NRQL."""

    def health() -> float | None:
        rows = nrql(scenario.health_nrql)
        if not rows:
            return None
        value = rows[0].get("p")
        return None if value is None else float(value)

    return health
