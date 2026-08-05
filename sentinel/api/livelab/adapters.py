"""Scenario-bound side effects, per lab: how a fault is injected, cleared, and how
its recovery health (badness) is read. The state machine only ever sees closures,
so both labs (and the test fakes) plug in identically.

  sock_shop: docker-exec CPU hogs, cleared with pkill sweeps
  otel_demo: flagd feature flags, cleared by resetting every flag to off
"""
from __future__ import annotations

import os
from typing import Callable

from sentinel.api.livelab.scenarios import Scenario

_FLAGD_URL_ENV = "SENTINEL_OTEL_FLAGD_UI_BASE_URL"
_FLAGD_DEFAULT = "http://localhost:8081/feature"


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
