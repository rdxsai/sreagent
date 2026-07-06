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

## Notes

- Run 1 graded under accepted_services policy (fault code spans checkout,
  fraud-detection, kafka; agent said fraud-detection).
- Run 2 failure mode: dead-service blind spot. Agent blamed the victim
  (frontend, connection errors) and a frontend decoy; its subagent inspected
  recommendation and declared it healthy (0.97 confidence) because a
  crashlooping service looks healthy whenever it is up and emits nothing when
  down. Unused evidence: memory_mb sawtooth, repeated startup logs.
