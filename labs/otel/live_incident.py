"""Inject a live incident into the running OTel Demo and record ground truth.

Flow: post SentinelChange events (culprit + decoys) to New Relic, flip the
culprit feature flag via flagd-ui, write truth.json (eval-only) and
run_meta.json (public context: window epochs, symptom, derived alert), then
soak so bad telemetry accumulates before the agent runs.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from labs.otel.flagd import FlagdClient
from sentinel.newrelic.client import load_env_var, post_custom_events

_DETECTION_DELAY_S = 60  # fallback alert onset: injection + typical detection delay


@dataclass(frozen=True)
class LiveChange:
    id: str
    service: str
    kind: str
    summary: str
    diff_touches: tuple[str, ...]
    offset_s: int


@dataclass(frozen=True)
class LiveScenarioSpec:
    id: str
    raw_flag_key: str
    variant: str
    symptom: str
    alertname: str
    root_cause: dict
    culprit: LiveChange
    decoys: tuple[LiveChange, ...]
    expected_evidence: tuple[str, ...]
    accepted_services: tuple[str, ...] | None = None
    baseline_s: int = 300
    soak_s: int = 300


_PAYMENT_DECOYS = (
    LiveChange("chg_1001", "frontend", "deploy", "frontend copy and layout assets changed", ("copy", "layout_assets"), -90),
    LiveChange("chg_1002", "recommendation", "runtime_config_change", "recommendation scoring parameter changed", ("scoring",), -45),
    LiveChange("chg_1004", "currency", "runtime_config_change", "currency rate cache TTL changed", ("rate_cache",), -120),
)

_RECOMMENDATION_DECOYS = (
    LiveChange("chg_3001", "frontend", "deploy", "frontend homepage layout updated", ("layout",), -110),
    LiveChange("chg_3002", "product-catalog", "runtime_config_change", "product catalog page size adjusted", ("page_size",), -50),
    LiveChange("chg_3004", "ad", "runtime_config_change", "ad rotation interval changed", ("rotation",), 30),
)

_SHIPPING_DECOYS = (
    LiveChange("chg_4001", "frontend", "deploy", "frontend checkout form validation updated", ("checkout_form",), -95),
    LiveChange("chg_4002", "currency", "runtime_config_change", "currency conversion rate source switched", ("rate_source",), -55),
    LiveChange("chg_4004", "payment", "runtime_config_change", "payment capture timeout adjusted", ("capture_timeout",), 45),
)

_KAFKA_DECOYS = (
    LiveChange("chg_2001", "frontend", "deploy", "frontend banner assets updated", ("banner",), -100),
    LiveChange("chg_2002", "shipping", "runtime_config_change", "shipping rate table updated", ("rate_table",), -60),
    LiveChange("chg_2004", "payment", "runtime_config_change", "payment retry budget adjusted", ("retry_budget",), 40),
)

SPECS: dict[str, LiveScenarioSpec] = {
    "payment_failure_live_001": LiveScenarioSpec(
        id="payment_failure_live_001",
        raw_flag_key="paymentFailure",
        variant="100%",
        symptom="Checkout attempts started failing.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "service", "type": "payment_charge_failure", "service": "payment",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_1003", "payment", "runtime_config_change",
                           "payment processing configuration changed",
                           ("charge_handler", "payment_provider"), 0),
        decoys=_PAYMENT_DECOYS,
        expected_evidence=(
            "payment error or latency signals increase after onset",
            "payment logs or traces show post-onset failure evidence",
            "symptoms begin after change chg_1003",
        ),
    ),
    "kafka_queue_problems_live_001": LiveScenarioSpec(
        id="kafka_queue_problems_live_001",
        raw_flag_key="kafkaQueueProblems",
        variant="on",  # verified against pinned demo.flagd.json: variants {on: 100, off: 0}
        symptom="Order processing is delayed; checkout succeeds but downstream order handling lags.",
        alertname="OrderProcessingLag",
        root_cause={"kind": "service", "type": "queue_backpressure", "service": "kafka",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_2003", "kafka", "runtime_config_change",
                           "queue consumer configuration changed",
                           ("consumer_delay", "queue_capacity"), 0),
        decoys=_KAFKA_DECOYS,
        expected_evidence=(
            "kafka consumer lag or queue metrics degrade after onset",
            "checkout continues to succeed while downstream consumers fall behind",
            "symptoms begin after change chg_2003",
        ),
        # The injected code lives in checkout (producer flood) and
        # fraud-detection (consumer sleep); kafka is where the backlog shows.
        accepted_services=("kafka", "fraud-detection", "checkout"),
    ),
    "recommendation_cache_failure_live_001": LiveScenarioSpec(
        id="recommendation_cache_failure_live_001",
        raw_flag_key="recommendationCacheFailure",
        variant="on",  # verified against pinned demo.flagd.json: variants {on, off}
        symptom="Product recommendations are intermittently missing from the storefront.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "service", "type": "cache_leak", "service": "recommendation",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_3003", "recommendation", "runtime_config_change",
                           "recommendation cache configuration changed",
                           ("cache", "eviction_policy"), 0),
        decoys=_RECOMMENDATION_DECOYS,
        expected_evidence=(
            "recommendation memory grows in a sawtooth as the process is OOM-killed and restarts",
            "cache miss logs and repeated service startup logs after onset",
            "callers see intermittent recommendation failures while the process is down",
        ),
        soak_s=600,  # gradual leak: needs time to become visible
    ),
    "intl_shipping_slowdown_live_001": LiveScenarioSpec(
        id="intl_shipping_slowdown_live_001",
        raw_flag_key="intlShippingSlowdown",
        variant="10sec",  # verified against pinned demo.flagd.json: {10sec, 5sec, off}
        symptom="Some checkout confirmations intermittently take far longer than usual.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "service", "type": "latency", "service": "shipping",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_4003", "shipping", "runtime_config_change",
                           "shipping rate table updated for international zones",
                           ("intl_rates", "carrier_selection"), 0),
        decoys=_SHIPPING_DECOYS,
        expected_evidence=(
            "a small fraction of ship-order requests take about 10 seconds after onset",
            "slow requests correlate with non-US shipping addresses",
            "aggregate latency stays near normal because domestic traffic dominates",
        ),
        soak_s=900,  # ~11% of orders are international; sparse outliers need time
    ),
}


def change_events_payload(spec: LiveScenarioSpec, injection_ms: int) -> list[dict]:
    events = []
    for change in (spec.culprit, *spec.decoys):
        events.append(
            {
                "eventType": "SentinelChange",
                "changeId": change.id,
                "service": change.service,
                "kind": change.kind,
                "summary": change.summary,
                "diffTouches": ",".join(change.diff_touches),
                "timestamp": injection_ms + change.offset_s * 1000,
            }
        )
    return events


def truth_doc(spec: LiveScenarioSpec) -> dict:
    doc = {
        "scenario_id": spec.id,
        "injection": {
            "raw_flag_key": spec.raw_flag_key,
            "variant": spec.variant,
            "enabled_at_second": spec.baseline_s,
        },
        "root_cause": spec.root_cause,
        "culprit_change_id": spec.culprit.id,
        "expected_evidence": list(spec.expected_evidence),
        "decoy_change_ids": [d.id for d in spec.decoys],
    }
    if spec.accepted_services:
        doc["accepted_services"] = list(spec.accepted_services)
    return doc


def run_meta_doc(spec: LiveScenarioSpec, injection_ms: int, end_ms: int) -> dict:
    onset_s = spec.baseline_s + _DETECTION_DELAY_S
    return {
        "scenario_id": spec.id,
        "window_start_ms": injection_ms - spec.baseline_s * 1000,
        "window_end_ms": end_ms,
        "symptom": spec.symptom,
        "alerts": [
            {
                "alertname": spec.alertname,
                "severity": "critical",
                "starts_at_second": onset_s,
                "labels": {"signal": "degradation", "tier": "user_facing"},
                "annotations": {"summary": f"Degradation detected since second {onset_s}"},
                "value": 1.0,
                "expr": f"live:{spec.raw_flag_key}",
                "fingerprint": f"live-{spec.id}",
            }
        ],
    }


def run_live_incident(
    spec: LiveScenarioSpec,
    base_dir: Path,
    *,
    flagd: FlagdClient | None = None,
    poster: Callable[[list[dict]], None] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock_ms: Callable[[], int] | None = None,
) -> Path:
    clock = clock_ms or (lambda: int(time.time() * 1000))
    if poster is None:
        license_key = load_env_var("NEW_RELIC_LICENSE_KEY")
        account = load_env_var("NEW_RELIC_ACCOUNT_ID")
        if not license_key or not account:
            raise SystemExit("NEW_RELIC_LICENSE_KEY and NEW_RELIC_ACCOUNT_ID required")
        poster = lambda events: post_custom_events(int(account), license_key, events)  # noqa: E731
    flagd = flagd or FlagdClient()

    injection_ms = clock()
    poster(change_events_payload(spec, injection_ms))
    flagd.set_flag_variant(spec.raw_flag_key, spec.variant)

    run_dir = base_dir / f"{spec.id}_{injection_ms}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "truth.json").write_text(json.dumps(truth_doc(spec), indent=2), encoding="utf-8")

    sleeper(spec.soak_s)
    end_ms = max(clock(), injection_ms + spec.soak_s * 1000)
    meta = run_meta_doc(spec, injection_ms, end_ms)
    (run_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"incident live; run dir: {run_dir}")
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec_id", choices=sorted(SPECS))
    parser.add_argument("--run-dir", type=Path, default=Path("runs") / "live")
    args = parser.parse_args()
    run_live_incident(SPECS[args.spec_id], args.run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
