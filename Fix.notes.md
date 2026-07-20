# Fix.notes — Phase 0 findings (verify before build)

## 0.1 schemas.py
- `Synthesis` carries a single `root_cause_service` (+ fault_type, justification). Fix 5 (ranked list) is needed.
- `WorkerVerdict` = hypothesis, supported, root_cause_service, fault_type, confidence, evidence. `synthesize` reads the verdict dicts (json-dumped) — root_cause_service / supported / confidence / evidence.

## 0.2 truth side (delay/loss) — RESOLVED, no caller/callee ambiguity
- `RCAEvalTruth.accepted_services = [case.service]`; `root_cause.service = case.service` (the INJECTED service). There is no caller/callee split in truth — it is always the injected service.
- Confirmed the injected service's OWN latency metric moves for a delay (orders p50 359→1526). So candidate = the service showing the signature = injected service = truth. Fix 2's "candidate = signature-showing service" aligns; the old `edge=[upstream,downstream]` wording is moot. Fix 6(b): no side-flip needed.

## 0.3 job label == reconciled service == truth.service — CONFIRMED (de-risks fix 6a)
- For ss_orders_delay, ob_productcatalogservice_delay, ob_currencyservice_loss: `truth.service` IS in the metric service set (== OTLP service.name == Prometheus `job`). `latency_p50_ms` + `latency_p95_ms` present per service.
- So NO reconciliation map is needed. Fix 6(a) reduces to an eval-side sanity check in run.py (loud warn if store.list_services() ∩ truth.accepted_services is empty), kept beside the agent, not in it.

## 0.4 metric-only readers (registry names) — for fixes 2/3
- `metrics_summary_all(onset)` -> EVERY service's error rate + p95 latency + resource metrics at once. This is the ruling-out core / full-vector reader.
- `metrics_detect_shift(service, metric)` -> largest level shift + its time (onset-step for one series).
- `metrics_compare_baseline(service, metric, baseline/compare windows)` -> mean shift.
- `metrics_top_movers(metric, onset_second)` -> rank services by one metric's change.
- `metrics_resource_saturation(onset)` -> resource risers.
- Latency-at-onset reader EXISTS: metrics_detect_shift / metrics_compare_baseline on latency_p95_ms. No new tool needed.

## 0.5 scorer
- `rcaeval_grader.py` = AC@1 location-only, single service. The oss path does NOT use it (grades in run scripts). Fix 5 = add `ranked_services` to `Synthesis`; grade AC@3 in the oss analysis. Do not modify rcaeval_grader (separate fixture path).

## Decisions confirmed
1. Static topology: OB derived by freezing `traces_build_topology` on a trace-rich OB case (ob_recommendationservice_cpu_1, 9 edges). SS has NO traces ever, so SS is HAND-WRITTEN from the published Sock Shop architecture. Train Ticket: skipped (no data downloaded).
2. Causal source: STUB (returns None). All our systems get a static artifact, so causal is unnecessary now (per plan, causal can wait).
3. `fault_class` -> `signature`: rename in one pass. sentinel/oss is self-contained; the fixture path uses a separate RootCauseReport schema, so no alias needed.

## Ruling-out core (chosen)
Every worker subset includes `metrics_summary_all` (full per-service vector) so the worker can always read resource+latency+error at onset regardless of assigned signature. Signature-specific tool added on top.

## Deviation from plan: ranking heuristic (fix 1)
The plan's "boundary = deepest anomalous node" ranking is empirically WRONG on mid-tier delays: for checkout_delay and orders_delay the origin is UPSTREAM of its anomalous downstream (its edges to callees are delayed, so callees' latency metrics also step), so the leaf victims look like boundaries and outrank the origin. Replaced with: rank anomalous services by DEGREE within the anomalous subgraph (origin is most-connected to its victims), with request-entry roots (in-degree 0 = frontend/front-end) pushed down (structural accumulators, never origins). Verified: truth ranks 0 on all 6 tested cases (productcatalog_delay, currency_loss, checkout_delay, orders_delay, user_delay, catalogue_cpu).
