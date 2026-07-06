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
    # Some flags (productCatalogFailure at the pinned SHA) carry a targeting
    # rule that returns "off" on both branches, so flipping defaultVariant is
    # a no-op. When set, the injector rewrites the targeting rule's
    # true-branch to this variant (and reset must restore it to "off").
    targeting_true_variant: str | None = None


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
    "email_memory_leak_live_001": LiveScenarioSpec(
        id="email_memory_leak_live_001",
        raw_flag_key="emailMemoryLeak",
        variant="1000x",  # email has a 100M compose memory limit; 1000x pads and retains each confirmation
        symptom="Order confirmation emails are intermittently delayed or missing.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "service", "type": "memory_leak", "service": "email",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_5003", "email", "runtime_config_change",
                           "email template rendering configuration changed",
                           ("template", "delivery_buffer"), 0),
        decoys=(
            LiveChange("chg_5001", "frontend", "deploy", "frontend order status page updated", ("order_status",), -100),
            LiveChange("chg_5002", "checkout", "runtime_config_change", "checkout confirmation retry policy adjusted", ("retry_policy",), -50),
            LiveChange("chg_5004", "currency", "runtime_config_change", "currency formatting locale updated", ("locale",), 40),
        ),
        expected_evidence=(
            "email service memory grows steadily after onset",
            "email confirmations degrade while checkout otherwise succeeds",
            "symptoms begin after change chg_5003",
        ),
        soak_s=600,
    ),
    "llm_rate_limit_live_001": LiveScenarioSpec(
        id="llm_rate_limit_live_001",
        raw_flag_key="llmRateLimitError",
        variant="on",
        symptom="The product AI assistant intermittently fails to answer questions.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "service", "type": "rate_limit_errors", "service": "llm",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_6003", "llm", "runtime_config_change",
                           "llm provider rate limit tier changed",
                           ("rate_limit", "provider_tier"), 0),
        decoys=(
            LiveChange("chg_6001", "frontend", "deploy", "frontend AI assistant widget restyled", ("assistant_widget",), -90),
            LiveChange("chg_6002", "product-reviews", "runtime_config_change", "review summary prompt template updated", ("prompt_template",), -45),
            LiveChange("chg_6004", "recommendation", "runtime_config_change", "recommendation scoring parameter changed", ("scoring",), 35),
        ),
        expected_evidence=(
            "about half of AI assistant requests fail with rate limit errors after onset",
            "product-reviews error spans reference HTTP 429 from the llm service",
            "symptoms begin after change chg_6003",
        ),
        # The injected 429 originates in llm; product-reviews carries the error spans.
        accepted_services=("llm", "product-reviews"),
        soak_s=600,  # AI-assistant traffic is low-rate
    ),
    "load_generator_flood_live_001": LiveScenarioSpec(
        id="load_generator_flood_live_001",
        raw_flag_key="loadGeneratorFloodHomepage",
        variant="on",
        symptom="Storefront pages slowed down across the board under heavy traffic.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "service", "type": "traffic_surge", "service": "load-generator",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_7003", "load-generator", "runtime_config_change",
                           "load test configuration changed",
                           ("flood", "user_count"), 0),
        decoys=(
            LiveChange("chg_7001", "frontend", "deploy", "frontend homepage hero banner deployed", ("homepage",), -80),
            LiveChange("chg_7002", "ad", "runtime_config_change", "ad rotation interval changed", ("rotation",), -40),
            LiveChange("chg_7004", "cart", "runtime_config_change", "cart session TTL adjusted", ("session_ttl",), 50),
        ),
        expected_evidence=(
            "homepage request rate spikes sharply after onset",
            "frontend saturates while backend error rates stay near zero",
            "symptoms begin after change chg_7003",
        ),
        # The surge originates in load-generator; frontend is where it lands.
        accepted_services=("load-generator", "frontend"),
        soak_s=300,
    ),
    "ad_failure_live_001": LiveScenarioSpec(
        id="ad_failure_live_001",
        raw_flag_key="adFailure",
        variant="on",
        symptom="Adverts are intermittently missing from product pages.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "service", "type": "request_failure", "service": "ad",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_8003", "ad", "deploy",
                           "ad targeting model rollout",
                           ("targeting_model", "serving_path"), 0),
        decoys=(
            LiveChange("chg_8001", "frontend", "deploy", "frontend ad slot layout changed", ("ad_slot",), -85),
            LiveChange("chg_8002", "recommendation", "runtime_config_change", "recommendation fallback list updated", ("fallback",), -40),
            LiveChange("chg_8004", "quote", "runtime_config_change", "quote cache TTL changed", ("cache_ttl",), 30),
        ),
        expected_evidence=(
            "a tenth of ad requests fail with UNAVAILABLE after onset",
            "ad error spans and warning logs appear after onset",
            "symptoms begin after change chg_8003",
        ),
        soak_s=300,
    ),
    "ad_high_cpu_live_001": LiveScenarioSpec(
        id="ad_high_cpu_live_001",
        raw_flag_key="adHighCpu",
        variant="on",
        symptom="Ad delivery latency degraded across product pages.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "service", "type": "cpu_saturation", "service": "ad",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_9003", "ad", "runtime_config_change",
                           "ad scoring worker pool resized",
                           ("worker_pool", "scoring_threads"), 0),
        decoys=(
            LiveChange("chg_9001", "frontend", "deploy", "frontend product page assets updated", ("assets",), -95),
            LiveChange("chg_9002", "product-catalog", "runtime_config_change", "product catalog page size adjusted", ("page_size",), -50),
            LiveChange("chg_9004", "recommendation", "runtime_config_change", "recommendation batch size changed", ("batch_size",), 45),
        ),
        expected_evidence=(
            "ad container cpu utilization saturates after onset",
            "ad latency rises while error rate stays near zero",
            "symptoms begin after change chg_9003",
        ),
        soak_s=300,
    ),
    "ad_manual_gc_live_001": LiveScenarioSpec(
        id="ad_manual_gc_live_001",
        raw_flag_key="adManualGc",
        variant="on",
        symptom="Ad responses intermittently stall for noticeable pauses.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "service", "type": "gc_pause", "service": "ad",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_10003", "ad", "runtime_config_change",
                           "ad JVM garbage collection settings changed",
                           ("jvm_gc", "heap_settings"), 0),
        decoys=(
            LiveChange("chg_10001", "frontend", "deploy", "frontend ad refresh interval changed", ("ad_refresh",), -75),
            LiveChange("chg_10002", "quote", "runtime_config_change", "quote service worker count adjusted", ("workers",), -35),
            LiveChange("chg_10004", "cart", "runtime_config_change", "cart cache eviction tuned", ("eviction",), 55),
        ),
        expected_evidence=(
            "ad latency shows periodic pause spikes after onset",
            "ad logs mention manual garbage collection after onset",
            "symptoms begin after change chg_10003",
        ),
        soak_s=300,
    ),
    "payment_unreachable_live_001": LiveScenarioSpec(
        id="payment_unreachable_live_001",
        raw_flag_key="paymentUnreachable",
        variant="on",
        symptom="Every checkout attempt is failing at payment.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "edge", "type": "connection_failure", "service": None,
                    "caller": "checkout", "callee": "payment"},
        culprit=LiveChange("chg_11003", "checkout", "runtime_config_change",
                           "payment client endpoint configuration changed",
                           ("payment_endpoint", "grpc_target"), 0),
        decoys=(
            LiveChange("chg_11001", "frontend", "deploy", "frontend checkout button flow updated", ("checkout_flow",), -90),
            LiveChange("chg_11002", "payment", "runtime_config_change", "payment provider credentials rotated", ("credentials",), -45),
            LiveChange("chg_11004", "currency", "runtime_config_change", "currency rate cache TTL changed", ("rate_cache",), 40),
        ),
        expected_evidence=(
            "checkout charge calls fail with connection errors after onset",
            "no payment service spans appear on failing traces",
            "symptoms begin after change chg_11003",
        ),
        soak_s=300,
    ),
    "cart_failure_live_001": LiveScenarioSpec(
        id="cart_failure_live_001",
        raw_flag_key="cartFailure",
        variant="on",
        symptom="Customers cannot complete checkout; cart operations fail at the end of the flow.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "service", "type": "datastore_connection_failure", "service": "cart",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_12003", "cart", "runtime_config_change",
                           "cart store connection string updated",
                           ("connection_string", "store_host"), 0),
        decoys=(
            LiveChange("chg_12001", "frontend", "deploy", "frontend cart page updated", ("cart_page",), -85),
            LiveChange("chg_12002", "checkout", "runtime_config_change", "checkout order pipeline reordered", ("pipeline",), -40),
            LiveChange("chg_12004", "payment", "runtime_config_change", "payment retry budget adjusted", ("retry_budget",), 35),
        ),
        expected_evidence=(
            "cart EmptyCart operations fail with connection errors after onset",
            "checkout fails after payment succeeds on affected orders",
            "symptoms begin after change chg_12003",
        ),
        soak_s=300,
    ),
    "image_slow_load_live_001": LiveScenarioSpec(
        id="image_slow_load_live_001",
        raw_flag_key="imageSlowLoad",
        variant="10sec",
        symptom="Product images are loading very slowly for shoppers.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "service", "type": "latency", "service": "frontend-proxy",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_13003", "frontend-proxy", "runtime_config_change",
                           "image route proxy fault filter configuration changed",
                           ("fault_filter", "image_route"), 0),
        decoys=(
            LiveChange("chg_13001", "image-provider", "deploy", "image provider asset pack refreshed", ("assets",), -80),
            LiveChange("chg_13002", "frontend", "deploy", "frontend product card layout updated", ("product_card",), -40),
            LiveChange("chg_13004", "product-catalog", "runtime_config_change", "product image URL scheme changed", ("image_urls",), 45),
        ),
        expected_evidence=(
            "image requests through the proxy take about 10 seconds after onset",
            "backend services stay healthy while image delivery lags",
            "symptoms begin after change chg_13003",
        ),
        # Delay is injected by the envoy fault filter in frontend-proxy from a
        # header emitted by frontend code; image-provider itself is untouched.
        accepted_services=("frontend-proxy", "frontend"),
        soak_s=600,  # browser-driven traffic only; needs time to accumulate
    ),
    "product_catalog_failure_live_001": LiveScenarioSpec(
        id="product_catalog_failure_live_001",
        raw_flag_key="productCatalogFailure",
        variant="on",
        symptom="One product page fails to load while the rest of the catalog works.",
        alertname="UserFacingDegradation",
        root_cause={"kind": "service", "type": "product_lookup_failure", "service": "product-catalog",
                    "caller": None, "callee": None},
        culprit=LiveChange("chg_14003", "product-catalog", "runtime_config_change",
                           "product catalog data source mapping changed",
                           ("data_source", "product_mapping"), 0),
        decoys=(
            LiveChange("chg_14001", "frontend", "deploy", "frontend product page template updated", ("product_template",), -85),
            LiveChange("chg_14002", "recommendation", "runtime_config_change", "recommendation catalog sync interval changed", ("catalog_sync",), -45),
            LiveChange("chg_14004", "image-provider", "deploy", "product image pack refreshed", ("images",), 40),
        ),
        expected_evidence=(
            "GetProduct errors for a single product id after onset",
            "callers touching that product fail while other products stay healthy",
            "symptoms begin after change chg_14003",
        ),
        # Product-scoped fault: the flag's targeting rule gates by product_id.
        targeting_true_variant="on",
        soak_s=300,
    ),
}


def set_targeting_true_variant(flagd: FlagdClient, raw_flag_key: str, variant: str) -> None:
    """Rewrite a flag's targeting rule true-branch (demo.flagd.json shape:
    targeting.if == [condition, true_variant, false_variant])."""
    document = flagd.read()
    flag = document.get("flags", {}).get(raw_flag_key)
    if not flag or "targeting" not in flag or "if" not in flag["targeting"]:
        raise RuntimeError(f"flag {raw_flag_key} has no targeting rule to patch")
    flag["targeting"]["if"][1] = variant
    flagd.write(document)


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
    if spec.targeting_true_variant is not None:
        set_targeting_true_variant(flagd, spec.raw_flag_key, spec.targeting_true_variant)

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
