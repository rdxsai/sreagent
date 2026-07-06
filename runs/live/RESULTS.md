# Live scenario results

Agent evaluated against live incidents injected into the OTel Demo streaming
to New Relic. One row per scenario run. Full artifacts (scorecard, agent
trace, NRQL query log, fixture-format window export) in each run directory.

| # | Scenario                            | Cognitive class                  | Result  | Loc | Culprit | Decoys | Cost   | Calls | Errors | Wall    | Run dir                                          |
|---|-------------------------------------|----------------------------------|---------|-----|---------|--------|--------|-------|--------|---------|--------------------------------------------------|
| 1 | kafka_queue_problems_live_001       | async queue lag, no error spans  | CORRECT | yes | yes     | yes    | $0.98  | 67    | 0      | ~13m    | kafka_queue_problems_live_001_1783304357897      |
| 2 | recommendation_cache_failure_live_001| OOM crashloop, victim noise     | WRONG   | no  | no      | no     | $1.16  | 89    | 0      | 16m     | recommendation_cache_failure_live_001_1783306411989 |
| 3 | intl_shipping_slowdown_live_001     | dimensional latency needle       | CORRECT | yes | yes     | yes    | $1.66  | 117   | 0      | 6m      | intl_shipping_slowdown_live_001_1783316825851    |
| 4 | email_memory_leak_live_001          | slow leak, service stays up      | CORRECT | yes | yes     | yes    | $1.46  | 84    | 0      | 4m      | email_memory_leak_live_001_1783320315486         |
| 5 | llm_rate_limit_live_001             | probabilistic 429s, sparse path  | CORRECT | yes | yes     | yes    | $0.72  | 50    | 0      | 3m      | llm_rate_limit_live_001_1783321573234            |
| 6 | load_generator_flood_live_001       | traffic surge, self-inflicted    | WRONG   | no  | yes     | yes    | $0.97  | 83    | 0      | 5m      | load_generator_flood_live_001_1783322901410      |
| 7 | ad_failure_live_001                 | probabilistic error spans        | CORRECT | yes | yes     | yes    | $0.49  | 36    | 0      | 2m      | ad_failure_live_001_1783323939751                |
| 8 | ad_high_cpu_live_001                | cpu saturation, no errors        | CORRECT | yes | yes     | yes    | $1.36  | 83    | 0      | 4m      | ad_high_cpu_live_001_1783324787063               |
| 9 | ad_manual_gc_live_001               | gc pause stalls                  | CORRECT | yes | yes     | yes    | $0.50  | 40    | 0      | 2m      | ad_manual_gc_live_001_1783325696234              |
| 10| payment_failure_live_001            | sharp error onset (control)      | CORRECT | yes | yes     | yes    | $0.53  | 37    | 0      | 2m      | payment_failure_live_001_1783326460065           |
| 11| payment_unreachable_live_001        | edge fault, dead-victim decoy    | CORRECT | yes | yes     | yes    | $0.57  | 45    | 0      | 3m      | payment_unreachable_live_001_1783327307922       |
| 12| cart_failure_live_001               | datastore conn failure           | CORRECT | yes | yes     | yes    | $0.67  | 43    | 0      | 3m      | cart_failure_live_001_1783328126972              |
| 13| product_catalog_failure_live_001    | single-product scoped errors     | CORRECT | yes | yes     | yes    | $0.68  | 48    | 0      | 3m      | product_catalog_failure_live_001_1783329064579   |
| 14| image_slow_load_live_001            | proxy fault injection, browser   | CORRECT | yes | yes     | yes    | $1.25  | 74    | 0      | 4m      | image_slow_load_live_001_1783329873544           |

## Campaign summary (2026-07-06)

- 14 of 16 demo flags run live; 12 of 14 CORRECT (86%). Culprit change
  identified in 13 of 14 (only the recommendation crashloop missed it).
- 896 agent tool calls total, 0 tool errors, 0 backend failures across
  ~2,000 NRQL queries. Total eval cost about $13.
- Failures: recommendation_cache_failure (dead-service blind spot) and
  load_generator_flood (blamed healthy proxy; server-healthy +
  one-caller-slow means the caller). Both fully archived for debugging.
- Recurring agent weakness (from its own run feedback): onset-consensus and
  triage anchor on error signals; latency-only and resource incidents need
  metric-shift anchoring. Subagents repeatedly anchored on noisy onsets and
  were overridden by the manager in 4 runs.

## Skipped flags (with justification)

- failedReadinessProbe: flips cart's gRPC readiness to NOT_SERVING, but the
  compose deployment defines no healthcheck for cart and no dependent uses
  service_healthy, so nothing observes it. Kubernetes-only fault.
- llmInaccurateResponse: swaps response text for one product with no error,
  latency, or resource signature. Telemetry-invisible by design; out of scope
  for a telemetry-driven agent.

## Notes

- Run 1 graded under accepted_services policy (fault code spans checkout,
  fraud-detection, kafka; agent said fraud-detection).
- Run 2 failure mode: dead-service blind spot. Agent blamed the victim
  (frontend, connection errors) and a frontend decoy; its subagent inspected
  recommendation and declared it healthy (0.97 confidence) because a
  crashlooping service looks healthy whenever it is up and emits nothing when
  down. Unused evidence: memory_mb sawtooth, repeated startup logs.
- Run 5 graded under edge-accepted policy: agent reported the
  product-reviews -> llm edge; both endpoints carry the injected fault code.
- Run 6 failure mode: blamed frontend-proxy, but export data shows proxy and
  frontend server spans stayed at ~18ms p95 post-onset; only load-generator's
  own client spans were slow (p95 2.2s). The flooding caller was drowning
  itself; server-healthy + one-caller-slow should have pointed at the caller.
  Culprit change still identified correctly.
