"""The dashboard's scenario catalog: one Scenario per runnable incident, across
both labs. A scenario carries everything the state machine needs that differs by
lab or fault: what to inject, the generic symptom the agent sees, the sealed truth
for the honesty badge, which chart is the hero, and how recovery is confirmed.

Sock Shop scenarios are the four vetted CPU-hog targets. OTel Demo scenarios are
drawn verbatim from the proven live specs (labs/otel/live_incident.SPECS), menu
restricted to the ones whose remediation (flag reset) and recovery signal are
clean and campaign-proven; more graduate in once their health metric is verified
against live data.
"""
from __future__ import annotations

from dataclasses import dataclass

from labs.otel.live_incident import SPECS
from labs.sockshop.faults import VETTED_TARGETS

SOCKSHOP_SYMPTOM = ("Customers report the Sock Shop storefront is sluggish: browsing the "
                    "product catalogue and loading pages feels slow. Overall the system is degraded.")


@dataclass(frozen=True)
class Scenario:
    id: str
    lab: str                       # sock_shop | otel_demo
    label: str
    fault_kind: str                # "cpu_hog" or the flagd flag key
    fault_desc: str
    truth_service: str
    accepted: tuple[str, ...]
    symptom: str
    system: str                    # topology system passed to run_rca
    prefer_trace: bool
    hero_metric: str               # cpu | error  (chart emphasis + health family)
    health_nrql: str               # badness, single row aliased AS p
    recovered_below: float | None  # absolute threshold; None -> relative (after < before/3)
    remediation_key: str | None    # catalog fault key override; None -> agent's fault type


def _cpu_health(container: str) -> str:
    return ("SELECT average(container.cpu.utilization) AS p FROM Metric "
            f"WHERE container.name = '{container}' SINCE 90 seconds ago")


def _error_rate_health(service: str) -> str:
    return ("SELECT filter(count(*), WHERE otel.status_code = 'ERROR') / count(*) AS p "
            f"FROM Span WHERE service.name = '{service}' SINCE 120 seconds ago")


def _sockshop(target: str) -> Scenario:
    return Scenario(
        id=f"sockshop-cpu-{target}",
        lab="sock_shop",
        label=f"CPU saturation in {target}",
        fault_kind="cpu_hog",
        fault_desc=f"3 CPU busy-loops injected into the {target} container",
        truth_service=target,
        accepted=(target,),
        symptom=SOCKSHOP_SYMPTOM,
        system="sock_shop",
        prefer_trace=False,
        hero_metric="cpu",
        health_nrql=_cpu_health(target),
        recovered_below=50.0,
        remediation_key=None,      # catalog maps the agent's cpu/resource type to restart
    )


# demo service -> container name (only where they differ), mirrors the LiveExecutor map
_OTEL_CONTAINER = {"adservice": "ad", "ad": "ad", "productcatalog": "product-catalog"}

# (spec id, hero metric, health nrql, recovered_below)
_OTEL_MENU: tuple[tuple[str, str, str, float | None], ...] = (
    # cpu scale of the demo's collector export is not pinned, so recovery is relative
    ("ad_high_cpu_live_001", "cpu", _cpu_health("ad"), None),
    ("payment_failure_live_001", "error", _error_rate_health("payment"), 0.05),
)


def _otel(spec_id: str, hero: str, health: str, recovered_below: float | None) -> Scenario:
    spec = SPECS[spec_id]
    service = spec.root_cause["service"]
    accepted = tuple(spec.accepted_services or (service,))
    return Scenario(
        id=f"otel-{spec_id}",
        lab="otel_demo",
        label=f"{spec.raw_flag_key} ({service})",
        fault_kind=spec.raw_flag_key,
        fault_desc=f"feature flag {spec.raw_flag_key}={spec.variant} via flagd",
        truth_service=service,
        accepted=accepted if service in accepted else accepted + (service,),
        symptom=spec.symptom,
        system="online_boutique",
        prefer_trace=True,
        hero_metric=hero,
        health_nrql=health,
        recovered_below=recovered_below,
        remediation_key="flag_injected",   # the right fix is clearing the flag, whatever the symptom type
    )


SCENARIOS: tuple[Scenario, ...] = tuple(
    [_sockshop(t) for t in VETTED_TARGETS]
    + [_otel(*row) for row in _OTEL_MENU]
)


def scenario_by_id(scenario_id: str) -> Scenario | None:
    return next((s for s in SCENARIOS if s.id == scenario_id), None)
