# Sentinel evaluation results

Consolidated results across the frozen RCAEval benchmark and the two live labs, plus the
gpt-oss vs Anthropic cost/context comparison. Numbers are pulled from the run artifacts under
`runs/` (RCAEval scorecards, `runs/oss_live/*.result.json`), not estimated, except the gpt-oss
dollar cost which is an estimate at OpenRouter list rates.

## RCAEval localization (AC@1 = top-1 root-cause service correct)

| Agent               | Harness                        | Cases | Hits | AC@1  |
| ------------------- | ------------------------------ | ----- | ---- | ----- |
| Anthropic (Claude)  | RCAEval sweep (`runs/rcaeval`) | 13    | 9    | 69.2% |
| gpt-oss-120b        | frozen fixtures (`runs/oss`)   | 13    | 12   | 92.3% |

Notes:
- The Anthropic sweep skipped 2 further cases mid-run on "credit balance too low" (total spend
  $12.82). Targeted re-runs after a network-localization fix: `networkfix` 4/4, `generalization` 2/2.
- Anthropic by category: resource 5/6, network 4/7. By fault: cpu 2/3, delay 2/4, loss 2/3, mem 3/3.
- gpt-oss frozen set = 7 Online-Boutique + 6 Sock-Shop cases; one miss was
  `productcatalogservice_delay` (a network/delay fault) localized to recommendationservice.

## Two live labs (dockerized, telemetry to New Relic, gpt-oss-120b)

| Lab                          | Services | Result                         | Notes |
| ---------------------------- | -------- | ------------------------------ | ----- |
| OTel Demo / Online Boutique  | ~16      | 3/4 AC@1 (5/6 with adHighCpu)  | payment, ad, email HIT; intl-shipping MISS (dimensional latency) |
| Sock Shop                    | 13       | 4/4 AC@1 after fixes           | catalogue, payment, orders, shipping HIT, all typed resource saturation |

Sock Shop runs (`runs/oss_live/live-sockshop-*.result.json`):

| target    | got       | fault_type          | verdict |
| --------- | --------- | ------------------- | ------- |
| catalogue | carts-db  | (null)              | MISS (pre-fix, span-gated detector) |
| catalogue | catalogue | (null)              | HIT (after metric-first fix, ranking-carried) |
| payment   | payment   | resource saturation | HIT |
| orders    | orders    | resource saturation | HIT |
| shipping  | shipping  | resource saturation | HIT (worker-confirmed, supported verdict) |

## Cost and context per investigation (gpt-oss live vs Anthropic RCAEval)

| Metric                          | Anthropic (Claude) | gpt-oss-120b        | gpt-oss fraction |
| ------------------------------- | ------------------ | ------------------- | ---------------- |
| Billed tokens (input + output)  | 183,532            | 46,028              | 0.25             |
| Full context incl. prompt cache | 587,363            | 46,028              | 0.078            |
| Dollar cost                     | $0.99 (real)       | ~$0.003 to 0.009 (est.) | < 0.05        |

gpt-oss used 25% of the billed tokens, ~8% of the full context, and well under 5% of the dollar
cost (roughly 20x to 100x cheaper depending on the OpenRouter provider rate). The Anthropic run
exhausted its API credits at $12.82.

## Fixes that produced these results (this session)

1. Metric-first service discovery: `NewRelicStore.list_metric_keys` unions span `service.name`
   with metric `container.name`, so span-poor systems (Sock Shop) are not starved. Without it the
   detector saw zero services and localization degenerated to topology order.
2. Fault-typing guardrails in `sentinel/oss/rca.py`: signature correction, anomalous-origin
   injection, verdict reconciliation, and deterministic fault typing from the overlay. Covered by
   `tests/unit/test_oss_rca_faulttype.py`; localization-neutral by construction.

## Reproduce the Sock Shop lab

Lab definition kept under `labs/sockshop/` (compose + collector config + the live driver). Bring up
with `docker compose up -d`, point the collector at a New Relic ingest key, then run the driver.
