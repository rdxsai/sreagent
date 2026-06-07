# Handoff: Detection Layer Findings and Direction Change

## What this document is

The derived-alert detection layer was built and proven on one scenario (`payment_failure_001`), then a second (`payment_unreachable_001`). While starting the remaining six, a set of probes showed that the rule set as designed only detects the two payment faults. This doc records the original approach, the probe findings that forced a rethink, the leak-safety reasoning behind the new design, and the redesigned approach we are now re-recording against.

Status at time of writing: all detection-layer code and both recorded scenarios are committed and pushed (`origin/main` at `c560d6e`). A comprehensive signal probe is running to finalize the redesigned rules.

## Approach before (what we built and recorded)

The detection layer freezes a `DerivedAlert` set into `public/manifest.json`, derived at build time from live Prometheus over the recorded window, sealed alongside the rest of the public fixture. Leak-safety is enforced by an allow-list (symptom-only alertnames and labels, no service naming, no label interpolation in annotations) plus cross-fixture coverage invariants:

- E1 coverage: every scenario fires at least one alert.
- E2 sharing: every alertname fires for at least two scenarios.
- E3 no-index: every distinct firing set is shared by at least two scenarios (no combination is a unique fingerprint of a fault).

The first rule set (`rules.yaml` v1) had four rules, all on the user-facing edge (frontend/checkout) span metrics, with absolute thresholds:

| rule                  | expr (edge span metrics)     | threshold |
| --------------------- | ---------------------------- | --------- |
| CheckoutFailureRate   | checkout error rate          | > 0.10    |
| FrontendHighErrorRate | frontend error rate          | > 0.05    |
| FrontendHighLatency   | frontend p95                 | > 200ms   |
| CheckoutHighLatency   | checkout p95                 | > 250ms   |

Recorded and committed under v1:

- `payment_failure_001` fired `{CheckoutFailureRate (t=405, 0.104), CheckoutHighLatency (t=585, 560ms)}`.
- `payment_unreachable_001` fired `{CheckoutFailureRate (t=450, 0.214)}`.

The implicit assumption: every fault would surface at the user-facing edge as an error-rate or latency breach.

## What the probes found

Three probe rounds, each a short live injection (reset flags, inject the fault, wait ~120-150s for the rate window to fill, read the rule exprs against live Prometheus, reset).

### Finding 1: product_catalog_failure is edge-quiet

| signal                | value   | threshold |
| --------------------- | ------- | --------- |
| CheckoutFailureRate   | 0.0     | 0.10      |
| FrontendHighErrorRate | 0.0     | 0.05      |
| FrontendHighLatency   | 44.2ms  | 200       |
| CheckoutHighLatency   | 162.5ms | 250       |

No edge errors and only a mild checkout latency bump, under threshold. Under v1 rules this fires nothing, so `derive_alerts` would raise `NoAlertFired` and the recording would fail. The probe saved an 18-minute wasted recording.

### Finding 2: all six unrecorded scenarios are edge-quiet

Baseline (flag off): checkout/frontend errors 0.0, frontend p95 43ms, checkout p95 117ms.

| scenario               | checkout err | frontend err | frontend p95 | checkout p95 | fires under v1? |
| ---------------------- | ------------ | ------------ | ------------ | ------------ | --------------- |
| product_catalog        | 0.0          | 0.0          | 41ms         | 180ms        | no              |
| recommendation_cache   | 0.0          | 0.004        | 47ms         | 176ms        | no              |
| ad_high_cpu            | 0.0          | 0.0          | 44ms         | 175ms        | no              |
| ad_manual_gc           | 0.0          | 0.003        | 43ms         | 140ms        | no              |
| load_flood             | 0.0          | 0.0          | 34ms         | 170ms        | no              |
| kafka                  | NaN          | 0.0          | 40ms         | NaN          | no              |

Headline: only the two payment (error) faults fire. The other six produce essentially zero user-facing errors, flat frontend latency, and a checkout p95 elevated to 140-180ms but all under the 250ms threshold. kafka's checkout metric came back NaN (checkout span volume effectively zero in the window, unmeasurable at the edge).

The checkout p95 bump (117ms baseline to 140-180ms) is suspect: ad, recommendation, and product-catalog are not on the checkout path, so that elevation is likely baseline noise rather than fault-induced. We could not justify simply lowering the threshold without a baseline-variance read. Conclusion: v1 detects 2 of 8, and the user-facing edge layer alone does not surface the other six in this demo.

### Finding 3: the signals do exist (metric catalog)

The demo exports 482 metric names. The USE/saturation/lag families needed to detect the other six are all present and per-service labeled:

| fault                  | signal class    | confirmed metric (per-service labeled)                                  |
| ---------------------- | --------------- | ----------------------------------------------------------------------- |
| product_catalog        | backend errors  | `traces_span_metrics_*` for product-catalog (RED per service)           |
| recommendation_cache   | backend err/lat | `traces_span_metrics_*` for recommendation                              |
| ad_high_cpu            | CPU saturation  | `container_cpu_utilization_ratio{container_name="ad"}` (ad 0.035 base)  |
| ad_manual_gc           | GC / memory     | `jvm_gc_duration_seconds_*` (ad present, by `jvm_gc_action`)            |
| load_flood             | traffic surge   | `rate(traces_span_metrics_calls_total)` on frontend                     |
| kafka                  | async backlog   | `kafka_consumer_records_lag_max`, `kafka_consumer_group_lag_ratio`      |

Caveat: `container_memory_percent_ratio` runs ~90-95% at baseline (containers near limits), so memory% is too noisy to alert on. CPU and GC pause are the clean saturation signals.

So signal availability is not the bottleneck. The bottleneck is that production alerts on RED + USE + lag across every service, while v1 watched only edge RED at two services.

## The real bottleneck: leak-safe sharing, not signals

A per-service or per-resource alert ("ad CPU high", "kafka consumer lag high") is exactly what production uses, but in this benchmark such an alert fires for one scenario only, which makes it a fingerprint of the answer and fails E2/E3 (a one-to-one alert-to-fault mapping leaks the root cause). So any new rule must be an aggregated symptom class: generic labels (the symptom class, not the service), expr aggregated across services (max over services), and shared by at least two scenarios.

Most faults cluster naturally into shared classes. The two that do not are load_flood (traffic surge) and kafka (consumer lag), whose signals are unique to one fault each. We pair them into a single `ThroughputAnomaly` class (too much inbound vs too-slow processing) so they share a class and stay leak-safe.

## Does relaxing leak-safety break the hackathon (the analysis)

The graded answer (`eval_only/truth.json`) has three parts: where (root-cause service), what (failure type), and which change (culprit among decoys). An alert can leak at most the first, and sometimes the second; it never names a change, so change-correlation and decoy-rejection always remain the agent's work.

Leak severity depends on causal shape:

| causal shape                      | examples                                   | does a localized alert leak the cause? |
| --------------------------------- | ------------------------------------------ | -------------------------------------- |
| symptom is downstream of cause    | payment_failure, payment_unreachable, load_flood, kafka | barely; the agent still traces to the cause |
| symptom service is the root cause | ad_high_cpu, ad_manual_gc, product_catalog, recommendation | yes; a per-service alert names root_cause.service |

The hackathon's core requirements (50+ tools, subagent orchestration, production scaffolding, composable typed I/O, a 20+ tool-call long-horizon run) are architectural and independent of incident difficulty; the long-horizon run is carried by the propagation faults regardless. So a leaky alert would not fail the requirements. The real cost of leaking is eval discrimination: the benchmark becomes a weaker test of root-cause reasoning, because the agent is partly told the answer. We keep it leak-safe by choice, because it costs only aggregated PromQL and yields a benchmark that genuinely tests localization.

## Approach now (the redesign)

Leak-safe taxonomy: four aggregated symptom-class rules, each generic-labeled and shared by exactly two scenarios.

| symptom class       | signal (aggregated, generic)                            | covers                               |
| ------------------- | ------------------------------------------------------- | ------------------------------------ |
| UserFacingErrorRate | checkout/frontend error rate (edge, already built)      | payment_failure, payment_unreachable |
| BackendErrorRate    | max over backend services of span error rate           | product_catalog, recommendation      |
| ResourceSaturation  | max over services of CPU utilization or GC pause rate   | ad_high_cpu, ad_manual_gc            |
| ThroughputAnomaly   | frontend request-rate surge OR consumer-group lag high  | load_flood, kafka                    |

Decisions locked with the user:

- Keep the strict, generic line (no service naming in labels or annotations; aggregate across services). No deliberate leaking.
- One primary alert per class for now, so E1/E2/E3 are clean (each class shared by two, each set shared by two). Revisit richer multi-alert sets later if more scenarios are added.

Ordered next steps:

1. Baseline-variance and per-fault confirmation probe (running now): sample the baseline noise floor several times, then inject each fault and confirm it actually trips its class signal at the service level (backend error/latency by service, top container CPU, GC pause rate, frontend request rate, consumer lag).
2. Implement the expanded `rules.yaml` as aggregated/generic symptom classes, update the allow-list. The evaluator, validator gates, and coverage harness already support it.
3. Re-record all eight scenarios under the new rules (the two payment fixtures must be re-recorded too, since their frozen alerts were derived under v1).
4. Run `coverage.py` live to enforce E1/E2/E3 across all eight; iterate rules if a fault misses or a set is not shared.
5. Commit `rules.yaml` + eight public fixtures + `coverage_matrix.json`.

Standing consequence: any rule change invalidates already-frozen fixtures, so a tuning decision is made once and all scenarios are re-recorded for consistency.

## Open items and risks

- Per-fault confirmation: the running probe must show each fault trips its intended class signal; if one does not (recommendation_cache and kafka were the original edge-quiet suspects), widen that symptom class, never add a fault-specific rule.
- Thresholds must sit above the measured baseline noise floor, especially for CPU, latency, and lag; relative-to-baseline framing is the fallback if absolute thresholds are too close to noise.
- The two singletons: confirm `ThroughputAnomaly` cleanly covers both load_flood and kafka; if not, fall back to authored-symptom triggering for the holdout.
- The seal and the existing eval truth are unaffected by all of this; alerts remain a public artifact and `eval_only/truth.json` stays the answer key.

## Status snapshot

- Code: detection layer (schema, rules, allow-list, evaluator, validator gates, coverage, Prometheus-rule compiler, webhook mapper, FastAPI `/alert`, deploy overlay) committed and pushed at `c560d6e`.
- Recorded under v1 (to be re-recorded under the new rules): `payment_failure_001`, `payment_unreachable_001`.
- Plan of record: `docs/superpowers/plans/2026-06-06-derived-alert-detection-layer.md`.
- Signal probe complete; the clean four-class taxonomy did not survive contact with the data (below).

## Update: signal probe outcome

The full signal probe (baseline + all eight faults, service-level signals) showed the four-class taxonomy is not supported by the demo's real behaviour:

- Only the two payment faults produce a clean user-facing error signal.
- recommendation_cache and product_catalog show a usable latency signal (recommendation p95 ~356ms; product_catalog checkout p95 ~240ms vs ~130ms baseline).
- Three faults have absent or noisy intended signals: kafka consumer lag stayed 0, ad GC pause rate ~0, ad CPU was masked by frontend's higher absolute CPU; load_flood's request-rate surge was not fault-specific (payment and ad_manual_gc showed similar rates).
- Latency aggregation must exclude async/batch services (accounting, load-generator, fraud-detection consumer), whose p95 pins to the 15000ms histogram ceiling.

## Alternative source evaluated: YouBrokeProd (ruled out)

We researched YouBrokeProd (youbrokeprod.com; the "YourBrokePod"/"yourbrokeprod" referenced) as a source of harder scenarios. Verdict: not usable as a data source.

- Proprietary closed SaaS: no open source, no self-host, no public API, no scenario or telemetry export.
- It is a browser-based human-training game; its telemetry is (high-confidence inference, undocumented) scripted/canned for in-browser reading, not real Prometheus/Jaeger/OpenSearch series, and is the wrong data model for our record/normalize/seal/replay pipeline even if it could be extracted.
- Its Claude/ChatGPT MCP integration is the inverse of our need (it lets an LLM play their live game, not export data).
- Only takeaway: its scenario catalog is good inspiration for which failure modes are worth modeling.
- The real fix for the stragglers is to inject harder, real faults into the same OTel demo with a chaos tool (docker-compose: Pumba/stress-ng/tc; or the demo's Kubernetes deploy plus Chaos Mesh/Krkn), which produces strong recordable telemetry and keeps the whole pipeline. Deferred, not pursued now.

## Decision: locked to four scenarios (2026-06-07)

Active set (detectable, leak-safe): payment_failure_001, payment_unreachable_001, recommendation_cache_failure_001, product_catalog_failure_001.
Deferred (need chaos injection; revisit after the agent works on the four): ad_high_cpu_001, ad_manual_gc_001, load_generator_flood_homepage_001, kafka_queue_problems_001.

Detection rules for the four (leak-safe, generic, shared):

- UserFacingErrorRate (edge checkout/frontend error) for payment_failure and payment_unreachable. Built.
- RequestLatencyHigh (max p95 over synchronous request-path services, excluding async/batch, threshold above the ~150ms baseline noise) for recommendation_cache and product_catalog. To build.

Open nuance to finalize at record time: payment_failure's high checkout latency (~560ms) would also trip the latency rule, making its set {error, latency} differ from payment_unreachable's {error}, which breaks E3 across the pair. Resolution options: collapse to a single UserFacingDegradation class (error OR latency; all four fire it; clean E1/E2/E3; an SLO-style page), or keep two classes and tune so the payment pair matches. Leaning to the single class for clean coverage now, with richer multi-alert revisited when differentiated scenarios are added.

Remaining execution to complete the lock: add the latency (or unified) rule, record recommendation_cache and product_catalog, re-record the two payment fixtures so frozen alerts stay consistent, then run coverage E1/E2/E3 across the four. The two payment fixtures already exist and are usable for starting agent work.
