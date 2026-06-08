# Spec: Derived Alert Feature ("Detection Layer")

**Status:** Ready to build
**Audience:** Claude Code (implementation agent)
**Scope:** Add a deterministic, leak-safe alert-derivation step to the existing fixture pipeline. The step evaluates symptom-level rules against a recorded scenario window (via the existing store APIs), emits a structured `Alert`, and seals it into `public/manifest.json`. This becomes the production-shaped trigger the agent receives.

---

## 1. Purpose & non-goals

### Purpose
Today the scenario trigger is an authored prose `symptom` string in `scenarios.yaml`. This feature replaces/augments it with an `Alert` **derived from the recorded telemetry**, so the trigger is grounded in the data, structured (parseable like a real Alertmanager webhook payload), and demonstrates an actual detection layer rather than an asserted one.

The alert is produced **once at fixture-build time and frozen** into the fixture. Determinism comes from the freeze, not from any live process. The agent reads the frozen alert at analysis time exactly as it reads metrics/traces/logs.

### Non-goals
- **No live agent triggering in the eval path.** The agent never runs during recording. Alert derivation is part of *producing* the fixture, not *running* the agent.
- **No running Alertmanager process required** for the core feature. We evaluate rules offline against the recorded window using the Prometheus query API. (A live Alertmanager + webhook path is documented in Appendix A as an *optional, demo-only* extension that reuses the same `Alert` contract.)
- **No new ground-truth surface.** The alert is a `public/` artifact and must pass every existing seal/redaction gate. It must never encode the root cause.

---

## 2. Where this sits in the pipeline

```
RECORD TIME (once per scenario)                     ANALYZE TIME (repeatable)
  inject fault + load                                 agent reads public/ fixture
  telemetry -> 3 stores (Prom / Jaeger / logs)        agent builds topology from traces
  >> NEW: rule eval over window -> Alert <<           agent -> RootCauseReport
  write_fixture seals public/ + eval_only/            evaluator grades vs eval_only/truth
        |
        +-- the freeze: fixture is immutable ---------+
```

The new step runs **inside the fixture build, before `write_fixture` seals `public/`**, so the derived `Alert` is sealed alongside the manifest and passes through `assert_no_banned_tokens` like every other public artifact.

---

## 3. Module layout (new code)

Create a new package alongside the existing OTel lab code:

```
labs/otel/alerting/
  __init__.py
  rules.yaml          # symptom-level rule definitions (the only place rules live)
  schema.py           # AlertRule, DerivedAlert pydantic models
  evaluator.py        # offline rule evaluation over a recorded window
  allowlist.py        # symptom vocabulary allow-list (alertnames + label keys/values)
  coverage.py         # scenario x rule matrix + invariants check (eval harness side)
```

Touch points in existing code (do not rewrite these files; extend them):
- `sentinel/fixtures/schemas.py` — add `DerivedAlert` and an `alert: DerivedAlert` field to `PublicManifest`.
- `labs/otel/workflow.py` — in `_manifest()`, call the evaluator and attach the alert to the public manifest. Never read `raw_flag_key` here (unchanged invariant).
- `labs/otel/writer.py` — no logic change; the existing `assert_no_banned_tokens(public_dir)` seal gate now also covers the serialized alert because it lives in the public manifest.
- `labs/otel/validator.py` — add the alert-specific seal assertions (Section 7).

---

## 4. Data contract: `DerivedAlert`

Mirror the shape of an Alertmanager webhook alert so the live path (Appendix A) and the offline path are interchangeable. Add to `sentinel/fixtures/schemas.py`:

```python
class DerivedAlert(BaseModel):
    alertname: str                  # from the symptom allow-list, e.g. "FrontendHighErrorRate"
    severity: Literal["warning", "critical"]
    starts_at: datetime             # onset of the symptom (>= window start)
    labels: dict[str, str]          # symptom-level ONLY (Section 6). e.g. {"tier": "edge", "signal": "error_rate"}
    annotations: dict[str, str]     # templated from symptom fields ONLY
    value: float                    # the breaching value of the symptom series
    expr: str                       # the PromQL that fired (symptom series only)
    fingerprint: str                # stable hash of (alertname + sorted labels)

class PublicManifest(BaseModel):
    # ... existing fields ...
    alert: DerivedAlert             # NEW. The frozen trigger.
```

Hard rule enforced by construction: `DerivedAlert` has **no field** that can hold a flag key, culprit service, or root-cause classification. If a value would name the culprit, the rule that produced it is wrong (Section 6), not the schema.

---

## 5. Rule model & `rules.yaml`

Rules are **symptom-level and shared across scenarios** — never 1:1 with a fault. They reference only user-facing / edge series. Define them in `labs/otel/alerting/rules.yaml`; this YAML is the single source of truth for rules.

```python
class AlertRule(BaseModel):
    alertname: str                  # MUST be in the allow-list
    expr: str                       # PromQL on symptom series only
    threshold: float
    comparison: Literal[">", ">=", "<", "<="]
    for_seconds: int                # series must breach continuously for this long before firing
    severity: Literal["warning", "critical"]
    labels: dict[str, str] = {}     # static symptom labels merged into the firing
    annotation_templates: dict[str, str] = {}  # {{value}}, {{starts_at}} only — never {{labels.service}}
```

```yaml
# rules.yaml  (starter set for the OTel demo astronomy shop)
rules:
  - alertname: FrontendHighErrorRate
    expr: 'sum(rate(http_server_request_duration_count{job="frontend",http_status_code=~"5.."}[2m])) / sum(rate(http_server_request_duration_count{job="frontend"}[2m]))'
    threshold: 0.05
    comparison: ">"
    for_seconds: 60
    severity: critical
    labels: {tier: edge, signal: error_rate}
    annotation_templates:
      summary: "Edge error rate elevated to {{value}} since {{starts_at}}"

  - alertname: FrontendHighLatency
    expr: 'histogram_quantile(0.95, sum(rate(http_server_request_duration_bucket{job="frontend"}[2m])) by (le))'
    threshold: 1.5            # seconds; tune per baseline
    comparison: ">"
    for_seconds: 60
    severity: warning
    labels: {tier: edge, signal: latency_p95}
    annotation_templates:
      summary: "Edge p95 latency {{value}}s since {{starts_at}}"

  - alertname: CheckoutFailureRate
    expr: 'sum(rate(app_checkout_attempts_total{outcome="error"}[2m])) / sum(rate(app_checkout_attempts_total[2m]))'
    threshold: 0.10
    comparison: ">"
    for_seconds: 60
    severity: critical
    labels: {tier: edge, signal: checkout_error_rate}
    annotation_templates:
      summary: "Checkout failure rate {{value}} since {{starts_at}}"
```

> **Metric names above are illustrative.** First task for the implementer: introspect the recorded fixtures / live Prometheus to confirm the exact metric and label names the demo emits (they vary by collector config and demo version), then fix the `expr` strings. Keep every `expr` pointed at edge / user-facing series (frontend, checkout, load-gen-perceived) — never at a downstream service like payment or cart.

---

## 6. Leak-safety design (the core constraint)

The alert is a `public/` artifact, so it must obey the same discipline as everything else, plus two alert-specific rules. There are four leak vectors; the design closes all four.

1. **Label leak.** A rule on `{service="payment"}` produces an alert labeled `service=payment` — the answer, and `assert_no_banned_tokens` won't catch it (it's a legit service name). **Mitigation:** rules query only edge/symptom series; firing labels are static symptom descriptors (`tier`, `signal`), set in the rule, not copied from the breaching series' service label.
2. **Alertname leak.** `PaymentChargeFailure` leaks the classification. **Mitigation:** `alertname` must be drawn from `allowlist.py` (symptom vocabulary: `FrontendHighErrorRate`, `FrontendHighLatency`, `CheckoutFailureRate`, ...). Validation rejects any other name.
3. **Structural ("which rule fired") leak — the topology lesson.** If one bespoke rule maps to each fault and exactly one fires per scenario, the fired alert is a perfect index into the root cause. No token filter catches this. **Mitigation:** rules are symptom-level and shared, so multiple distinct faults trip the same alert. Enforced by the coverage invariant in Section 7 ("every rule fires for >= 2 scenarios").
4. **Annotation / value leak.** Templated summaries often interpolate `{{ $labels.service }}`. **Mitigation:** annotation templates may reference only `{{value}}` and `{{starts_at}}`. Validation rejects templates containing label interpolations.

`allowlist.py` exposes:
```python
ALLOWED_ALERTNAMES: set[str]          # symptom vocabulary
ALLOWED_LABEL_KEYS: set[str]          # {"tier", "signal", "severity"}
ALLOWED_LABEL_VALUES: dict[str, set]  # tier in {edge}, signal in {error_rate, latency_p95, checkout_error_rate, ...}
```

**Onset timing is allowed.** `starts_at` may equal the true injection onset — a real symptom genuinely starts then, and onset != cause. Truth (`enabled_at_second`) may be used to *validate* that a rule fired at the right time (eval side), but the onset value never enters a rule `expr`.

---

## 7. Validation gates (extend `validator.py`)

Add to `validate_fixture` (these run after the existing seal/signal checks):

- **A. Allow-list check.** `manifest.alert.alertname in ALLOWED_ALERTNAMES`; every label key in `ALLOWED_LABEL_KEYS` and value in `ALLOWED_LABEL_VALUES`. Fail with `RedactionError` otherwise.
- **B. Template safety.** No annotation template string contains `labels.` / `$labels` / a service-name token. Reject service-naming interpolation.
- **C. Seal coverage.** Confirm the serialized alert was included in the `assert_no_banned_tokens(public_dir)` scan (it is, since it lives in `manifest.json`). Add an explicit assertion that the manifest JSON containing the alert was one of the scanned files.
- **D. Consistency.** `manifest.alert.starts_at >= window.start` and `<= window.end`.

Add a **harness-level** check in `coverage.py` (runs across the whole fixture set, not per-fixture — this is the structural anti-leak guard):

- **E. Coverage matrix.** Build a `scenario x alertname` matrix of firings across all recorded fixtures. Assert:
  - every scenario row has >= 1 firing (coverage), and
  - every alertname column fires for >= 2 distinct scenarios (sharing / no fault index).
  Emit the matrix as an artifact (`coverage_matrix.json`) for inspection and for the eval report.

---

## 8. Evaluation engine (`evaluator.py`)

Pure, deterministic, no live process. Uses the existing Prometheus store API client.

**Input:** `window: (start, end)`, the store API handle, the loaded `rules.yaml`.
**Output:** a single `DerivedAlert` (the highest-severity, earliest firing — see tie-break below), or raise `NoAlertFired` (a coverage hole to fix per Section 5/7-E).

Algorithm per rule:
1. Issue a **range query** for `expr` over `[start, end]` at a fixed step (e.g. 15s) via the store API.
2. Walk the resulting series; find the first timestamp `t0` where `comparison(value, threshold)` holds **continuously for `for_seconds`**.
3. If found, record a firing: `(alertname, severity, starts_at=t0, value=value_at_t0, labels, annotations rendered from templates, expr, fingerprint)`.

Across firings, select the alert deterministically:
- Prefer `critical` over `warning`.
- Within a severity, prefer the **earliest** `starts_at`.
- Break remaining ties by `alertname` lexicographic order (so the result is stable and reproducible).

Determinism requirements:
- Fixed query step, fixed range derived only from `window`.
- No wall-clock reads, no randomness, no dependence on evaluation order beyond the explicit tie-break.
- Same recorded data in -> identical `DerivedAlert` out. (This is what makes "run once and freeze" safe.)

> Implementation note: if querying the *live* Prometheus is inconvenient at build time, you may instead evaluate against the **recorded** series. Two acceptable sources: (a) a throwaway Prometheus pointed at the recording's TSDB snapshot, queried via its HTTP API; or (b) the already-normalized `metrics.jsonl` if it retains the raw symptom series at adequate resolution. Prefer (a) for fidelity; (b) avoids standing up Prometheus. Pick one and document it; do not mix.

---

## 9. Integration sequence (`workflow.py`)

In `_manifest()` (the function that builds the public manifest and must never read `raw_flag_key`):

```python
def _manifest(scenario, window, store_api) -> PublicManifest:
    # ... existing public fields (scenario_id, source, window, symptom, available_signals) ...
    alert = derive_alert(window=window, store_api=store_api, rules=load_rules())  # NEW
    return PublicManifest(..., alert=alert)
```

`_truth()` is unchanged. The alert flows only into the public manifest; truth still converges only in `PrivateTruth`. `write_fixture` then seals public first and runs `assert_no_banned_tokens(public_dir)` — which now also covers the alert — before writing `eval_only/`.

---

## 10. Best practices (encode these in code & comments)

- **Author rules top-down, validate against recordings.** Write symptom rules from "what an SRE alerts on," then run them over all recordings as a coverage *test*. Close gaps by widening a symptom rule, never by adding a fault-specific rule.
- **Symptom, not fault.** Every `expr` references edge/user-facing series. If you ever need a downstream service label to make a rule fire, stop — that is a structural leak.
- **Shared, not 1:1.** The coverage matrix must show every alert firing for >= 2 scenarios. Identical alert fingerprints across scenarios is *good* — multiple causes present identically at the symptom layer, and the agent disambiguates via investigation, not via the alert.
- **The freeze does the work.** Derive once at build time; never re-derive at analyze time. The agent reads the frozen alert.
- **Truth informs checks, never inputs.** Use onset only to validate firing timing on the eval side.
- **One contract, two producers.** Keep `DerivedAlert` shaped like an Alertmanager webhook so the optional live path (Appendix A) is a drop-in producer.

---

## 11. Testing strategy

**Unit (`evaluator.py`, `allowlist.py`):**
- `for_seconds` logic: a series that breaches for less than `for_seconds` does NOT fire; exactly at the boundary does.
- comparison operators, threshold edges.
- tie-break determinism: same inputs -> identical alert across repeated runs.
- allow-list rejection: a non-allow-listed alertname / label value raises.
- template safety: a template containing `{{labels.service}}` raises.

**Integration (over a recorded fixture, e.g. `payment_failure_001`):**
- run the full build; assert `public/manifest.json` contains a valid `alert`, sealed and passing `assert_no_banned_tokens`.
- assert the alert is symptom-level (alertname in allow-list, no culprit service in labels/annotations).
- assert `starts_at` within window.
- re-run; assert byte-identical alert (determinism / freeze-safety).

**Harness (`coverage.py`, across all fixtures):**
- build the matrix; assert coverage (every scenario fires) and sharing (every alert fires for >= 2 scenarios).
- snapshot `coverage_matrix.json`.

---

## 12. Definition of done

- [ ] `DerivedAlert` added to `schemas.py`; `PublicManifest.alert` populated.
- [ ] `rules.yaml` with a confirmed-against-real-metrics symptom rule set.
- [ ] `evaluator.py` deterministic; unit tests green.
- [ ] `allowlist.py` + validator gates A–D green per fixture.
- [ ] `coverage.py` invariant E green across all fixtures; `coverage_matrix.json` emitted.
- [ ] Alert sealed into `public/` and caught by `assert_no_banned_tokens`; truth still isolated.
- [ ] Re-running a build yields a byte-identical alert (freeze-safe).
- [ ] No `expr` references a non-edge series; no annotation interpolates a label.

---

## 13. Decisions to confirm before building

1. **Metric/label names:** confirm the demo's actual frontend/checkout series names and fix `rules.yaml` `expr` strings.
2. **Query source:** live Prometheus vs TSDB snapshot vs normalized `metrics.jsonl` (Section 8 note). Pick one.
3. **Keep authored `symptom`?** Recommended: keep it as a human-readable narrative field *alongside* the derived `alert`. Confirm.
4. **Severity thresholds:** baseline-tune `FrontendHighLatency` threshold against a no-fault window so it doesn't fire on normal noise.

---

## Appendix A — Optional live Alertmanager path (demo only, not eval)

Reuses the `DerivedAlert` contract. Build only if you want the live "alert fired -> agent kicked off" demo narrative.

- Add an **Alertmanager** container; load the *same* symptom rules into Prometheus as alerting rules.
- Configure an Alertmanager **route + webhook receiver** -> `POST /alert` on a small FastAPI endpoint in the agent service.
- The webhook handler maps the Alertmanager payload onto `DerivedAlert` and starts the manager.
- **Never used in the eval harness** — live Alertmanager is non-deterministic (grouping, `for:` timing). Eval always uses the frozen alert from the fixture.

The leak rules in Section 6 apply identically here: the Prometheus alerting rules must be the symptom-level, shared rule set, and the webhook payload must pass the same allow-list/seal checks before it becomes a manifest.