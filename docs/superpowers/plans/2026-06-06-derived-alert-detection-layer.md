# Derived Alert (Detection Layer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, leak-safe detection layer that evaluates symptom-level alert rules against the recorded window via live Prometheus at build time, freezes the full firing set into `public/manifest.json` as the agent's production-shaped trigger (the agent triages the set), and also wires the optional live Alertmanager → webhook → agent path for the demo, reusing the same `DerivedAlert` contract.

**Architecture:** A new `labs/otel/alerting/` package holds the rule model, an allow-list, a Prometheus-backed evaluator that returns the whole firing set, and a cross-fixture coverage check (coverage + sharing + no-1:1-set invariants). The recorder evaluates real PromQL symptom rules over the recorded window and freezes the deterministic firing set into the manifest; `write_fixture`'s seal gate covers it because it lives in the manifest; truth stays isolated in `eval_only/`. A separate demo-only path (Part B) compiles the same rules into Prometheus alerting rules, runs Alertmanager, and maps its webhook payload back onto `DerivedAlert` via a FastAPI `/alert` endpoint, never used for grading.

**Tech Stack:** Python 3.11+, Pydantic v2, PyYAML, the existing `PrometheusClient` (urllib HTTP), FastAPI (Part B), pytest. Part B infra: Alertmanager container + a docker-compose overlay.

**Execution & scope (Phase 1):** Build the full pipeline (Part A + Part B) end to end for `payment_failure_001` only, and record just that one scenario to confirm it passes. The other 7 scenarios and the live cross-fixture coverage invariants E2/E3 are Phase 2 (Task A10), gated on Phase 1 working. With a single fixture only E1 (the fixture fires at least one alert) is live-checkable; the E1/E2/E3 logic itself is fully unit-tested now (Task A7). The Part B agent-kickoff is a stub (`on_alert` logs the alert set); the real agent lands later, do not block on it. Execute task-by-task via superpowers:subagent-driven-development: a fresh subagent per code task with review between tasks. The long live recording (Task A9) is run by the orchestrator directly, not a subagent. Commit to `main` per the repo's established workflow.

---

## 0. Grounded findings (confirmed against the live stack, do not re-derive)

These were probed against the running demo and the committed `payment_failure_001` fixture. They override the illustrative metric names in the spec.

- No `envoy_*` metrics are scraped. `http_server_request_duration_seconds_*` exists only for `cart`, `jaeger`, `shipping` (not the frontend). The frontend and frontend-proxy do not emit HTTP server duration metrics into Prometheus.
- The only consistent per-service user-facing signal is the trace-derived `traces_span_metrics_*` family (`traces_span_metrics_calls_total`, `traces_span_metrics_duration_milliseconds_bucket`), present for `frontend`, `frontend-proxy`, `frontend-web`, `checkout`, `load-generator`, and every other service.
- The service label is `service_name` (values `frontend`, `checkout`, ...). `job` is `opentelemetry-demo/<svc>`. Error spans carry `status_code="STATUS_CODE_ERROR"`. Duration buckets are in **milliseconds**.
- For `payment_failure_001`, propagation is uneven: `frontend` error rate post-onset avg 0.004 / max 0.014 (flat), `frontend` p95 ~42ms (flat), but `checkout` error rate post-onset avg 0.167 / max 0.250. The load-bearing user-facing symptom is the **checkout transaction error rate**, not the frontend HTTP layer.

Framing note: rules are symptom-level / user-facing, not literally "edge". We pick whatever user-perceived signal a scenario actually moves (checkout, frontend, load-gen-perceived), tuned and widened against the real recordings so every scenario fires. The non-negotiable is that rules stay shared (never one-to-one with a fault); we widen by broadening or sensitizing a symptom rule, never by adding a fault-specific rule.

---

## 1. The whole pipeline (with the new step + the live demo path)

### 1.1 Diagram

```
RECORD / BUILD TIME  (once per scenario, controller record)
────────────────────────────────────────────────────────────────────────
 scenarios.yaml ─► flag reset ─► warmup ─► inject fault ─► observe
        └─► collect_raw_capture ─► Prometheus query_range (incident metrics)
                                ├─► Jaeger traces
                                └─► OpenSearch logs (bucketed)
        ─► RawTelemetryCapture(prometheus_matrices, traces, logs, window)
        ─► assemble_recorded_fixture(scenario, capture, prometheus_client)
              normalize metrics/traces/logs (flag identity stripped)
              _public_changes (sanitized culprit + decoys)
              _manifest(scenario, capture, prometheus_client)
                   └─► NEW derive_alerts(window, prometheus, load_rules())
                          range-query each rule expr @15s over [start,end]
                          first continuous breach >= for_seconds ─► firing
                          collect ALL firings, sort deterministically
                   └─► list[DerivedAlert]  ─► PublicManifest.alerts   (the SET)
        ─► write_fixture
              write public/ (manifest+alerts, metrics, logs, traces, changes)
              assert_no_banned_tokens(public/)  ◄─ now also scans every alert
              write eval_only/ (truth, injection_log, flag snapshots)
        ─► validate_fixture: existing gates + NEW A–D applied to each alert
        ─► sealed fixture ──── the freeze: immutable ────┐
                                                         │
HARNESS TIME (eval side)                                 │
 coverage.py ─► scenario×alertname matrix + set map over all fixtures
        E1 coverage : every scenario fires >=1
        E2 sharing  : every alertname fires for >=2 scenarios
        E3 no-index : every distinct firing SET is shared by >=2 scenarios
        ─► coverage_matrix.json                          │
                                                         ▼
ANALYZE TIME (agent, later doc)
 agent reads manifest.alerts (frozen SET, triages) + metrics/traces/logs
        ─► queries the recording via store-shaped tools
        ─► builds topology from traces ─► RootCauseReport
        ─► graded vs eval_only/truth.json (agent never reads it)

LIVE DEMO PATH  (Part B, demo-only, NEVER eval)
────────────────────────────────────────────────────────────────────────
 rules.yaml ─► compile_prometheus_rules ─► prometheus_rules.yml
        Prometheus (continuous eval) ─► Alertmanager (group/route)
        ─► webhook POST /alert (FastAPI, sentinel/api)
        ─► alertmanager_payload_to_alerts ─► list[DerivedAlert]
        ─► allow-list/seal checks ─► on_alert(alerts)  ─► [agent kickoff seam]
```

### 1.2 What each part does, and how

- `scenarios.yaml` + `load_scenario`: the only place the fault is named. The `public:` block feeds public artifacts; `raw_flag_key` + `truth:` feed `PrivateTruth` only. Alerts never read `raw_flag_key`.
- Flag control + `collect_raw_capture`: inject the fault and pull the three signals over the window (unchanged from today).
- `derive_alerts` (NEW): the detection layer. Issues a `query_range` per rule expr over the window at a fixed 15s step, finds each rule's first breach lasting `for_seconds`, and returns the full set of firings sorted deterministically. The agent receives the set and triages.
- `_manifest` (extended): builds existing public fields, calls `derive_alerts`, attaches `PublicManifest.alerts`. Still never reads `raw_flag_key`. `assemble_recorded_fixture` gains a `prometheus` param threaded from `telemetry_clients.prometheus`.
- `write_fixture`: unchanged logic; the seal gate now covers every serialized alert (they live in the manifest).
- `validate_fixture` (extended): per-alert allow-list, template safety, seal-coverage, and `starts_at` within window; plus a non-empty `alerts` assertion.
- `coverage.py` (NEW, harness): builds the scenario×alertname matrix and the per-scenario firing-set map, enforcing E1 (coverage), E2 (sharing), E3 (no firing set is a 1:1 fault index). This is the structural anti-leak guard, extended to sets.
- Live demo path (Part B): the same `rules.yaml` compiled to Prometheus alerting rules, evaluated continuously by Prometheus, routed by Alertmanager to a FastAPI `/alert` webhook that maps the payload back onto `list[DerivedAlert]`, runs the same allow-list checks, and hands them to an agent-kickoff seam. Demo-only because Alertmanager `for:`/grouping/scrape timing is non-deterministic; eval always uses the frozen set.

---

## 2. File structure

New package:

```
labs/otel/alerting/
  __init__.py        # exports rule + alert API, allow-list, derive_alerts, coverage, rule compilation
  rules.yaml         # symptom-level rule definitions (single source of truth for rules)
  schema.py          # AlertRule model + rules.yaml loader
  allowlist.py       # symptom vocabulary allow-list + per-alert and per-rule checks
  evaluator.py       # derive_alerts (returns the firing SET) + NoAlertFired
  coverage.py        # build_coverage_matrix + assert_coverage_invariants (E1/E2/E3)
  prometheus_rules.py# compile_prometheus_rules: rules.yaml -> Prometheus alerting rules (Part B)
  webhook.py         # alertmanager_payload_to_alerts: Alertmanager webhook -> list[DerivedAlert] (Part B)
```

Touched (extend, do not rewrite):

- `sentinel/fixtures/schemas.py` — add `DerivedAlert`; add `alerts: list[DerivedAlert]` to `PublicManifest`.
- `labs/otel/workflow.py` — thread the Prometheus client into `_manifest`; call `derive_alerts`.
- `labs/otel/validator.py` — add alert gates A–D (per alert + non-empty).
- `sentinel/api/__init__.py` (or a new `sentinel/api/app.py`) — FastAPI app with `POST /alert` (Part B).

Test + fixture migration:

- `tests/unit/test_otel_alerting.py` — NEW (schema, allow-list, evaluator set semantics, rule compilation, webhook mapping).
- `tests/unit/test_otel_coverage.py` — NEW (E1/E2/E3).
- `tests/unit/test_api_alert_webhook.py` — NEW (Part B endpoint).
- `tests/unit/test_otel_workflow.py` — extend assemble test: fake Prometheus range client; assert `manifest.alerts`.
- `tests/unit/test_otel_writer.py` — add `alerts=[...]` to the `_manifest()` helper.
- `tests/fixtures/otel/payment_failure_001/public/manifest.json`, `payment_unreachable_001/public/manifest.json` — add hand-authored allow-listed `alerts` lists.
- `fixtures/payment_failure_001/` — re-record so it gains a derived alert set.
- `deploy/alertmanager/` (NEW, Part B) — `alertmanager.yml`, generated `prometheus_rules.yml`, `compose.alertmanager.yml` overlay, and a short runbook.

---

## 3. Data contract

Add to `sentinel/fixtures/schemas.py` (uses the existing `StrictModel`, extra fields rejected):

```python
from typing import Literal


class DerivedAlert(StrictModel):
    alertname: str = Field(min_length=1)        # from the symptom allow-list
    severity: Literal["warning", "critical"]
    starts_at_second: int = Field(ge=0)         # window-relative onset of the symptom
    labels: dict[str, str] = Field(default_factory=dict)        # symptom-level only
    annotations: dict[str, str] = Field(default_factory=dict)   # templated from value/starts_at only
    value: float                                # breaching value of the symptom series
    expr: str = Field(min_length=1)             # the PromQL that fired (symptom series only)
    fingerprint: str = Field(min_length=1)      # stable hash of alertname + sorted labels
```

Window-relative `starts_at_second` (deviation from the spec's `datetime`) keeps the public surface in one time base, matching `MetricRow.time` and `manifest.window`, and removes any epoch from the public side. The live path (Part B) maps Alertmanager `startsAt` onto this relative to the alert group's earliest start.

Extend `PublicManifest`:

```python
class PublicManifest(StrictModel):
    # ... existing fields ...
    alerts: list[DerivedAlert] = Field(min_length=1)   # NEW. The frozen firing SET (>=1). The agent triages.
```

Hard invariant by construction: `DerivedAlert` has no field that can hold a flag key, culprit service, or root-cause classification.

---

## 4. `rules.yaml` (grounded, symptom-level, shared)

`AlertRule` model in `labs/otel/alerting/schema.py`:

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class AlertRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alertname: str = Field(min_length=1)        # MUST be in the allow-list
    expr: str = Field(min_length=1)             # PromQL on user-facing symptom series only
    threshold: float
    comparison: Literal[">", ">=", "<", "<="]
    for_seconds: int = Field(ge=0)
    severity: Literal["warning", "critical"]
    labels: dict[str, str] = Field(default_factory=dict)
    annotation_templates: dict[str, str] = Field(default_factory=dict)
```

`labs/otel/alerting/rules.yaml` (starter set; exprs confirmed against this stack; thresholds tuned in Task A9):

```yaml
rules:
  - alertname: CheckoutFailureRate
    expr: >-
      sum(rate(traces_span_metrics_calls_total{service_name="checkout",status_code="STATUS_CODE_ERROR"}[3m]))
      / sum(rate(traces_span_metrics_calls_total{service_name="checkout"}[3m]))
    threshold: 0.10
    comparison: ">"
    for_seconds: 60
    severity: critical
    labels: {tier: user_facing, signal: checkout_error_rate}
    annotation_templates:
      summary: "Checkout failure rate {{value}} since second {{starts_at}}"

  - alertname: FrontendHighErrorRate
    expr: >-
      sum(rate(traces_span_metrics_calls_total{service_name="frontend",status_code="STATUS_CODE_ERROR"}[3m]))
      / sum(rate(traces_span_metrics_calls_total{service_name="frontend"}[3m]))
    threshold: 0.05
    comparison: ">"
    for_seconds: 60
    severity: critical
    labels: {tier: user_facing, signal: error_rate}
    annotation_templates:
      summary: "User-facing error rate {{value}} since second {{starts_at}}"

  - alertname: FrontendHighLatency
    expr: >-
      histogram_quantile(0.95, sum(rate(traces_span_metrics_duration_milliseconds_bucket{service_name="frontend"}[3m])) by (le))
    threshold: 200          # milliseconds; tuned in Task A9
    comparison: ">"
    for_seconds: 60
    severity: warning
    labels: {tier: user_facing, signal: latency_p95}
    annotation_templates:
      summary: "User-facing p95 latency {{value}}ms since second {{starts_at}}"

  - alertname: CheckoutHighLatency
    expr: >-
      histogram_quantile(0.95, sum(rate(traces_span_metrics_duration_milliseconds_bucket{service_name="checkout"}[3m])) by (le))
    threshold: 250          # milliseconds; tuned in Task A9
    comparison: ">"
    for_seconds: 60
    severity: warning
    labels: {tier: user_facing, signal: checkout_latency_p95}
    annotation_templates:
      summary: "Checkout p95 latency {{value}}ms since second {{starts_at}}"
```

Every `expr` is scoped to a user-facing `service_name` (`frontend` or `checkout`), none reference a downstream service. `[3m]` matches the recorder's rate window. Latency thresholds are milliseconds. The set is intentionally small so multiple faults collapse onto the same firing set (satisfying E3). Widen by sensitivity/breadth if a scenario does not fire, never by adding a fault-specific rule.

---

## 5. Allow-list (`allowlist.py`)

```python
ALLOWED_ALERTNAMES: set[str] = {
    "CheckoutFailureRate", "FrontendHighErrorRate", "FrontendHighLatency", "CheckoutHighLatency",
}
ALLOWED_LABEL_KEYS: set[str] = {"tier", "signal", "severity"}
ALLOWED_LABEL_VALUES: dict[str, set[str]] = {
    "tier": {"user_facing"},
    "signal": {"error_rate", "checkout_error_rate", "latency_p95", "checkout_latency_p95"},
}
FORBIDDEN_ANNOTATION_SUBSTRINGS: tuple[str, ...] = ("labels.", "$labels", "{{labels", "service")


class AllowlistError(ValueError): ...

def assert_alert_is_symptom_level(alert) -> None: ...      # alertname/labels/severity allow-listed
def assert_rule_templates_safe(rule) -> None: ...          # no forbidden substrings in templates
```

`assert_rule_templates_safe` runs inside `load_rules()` so bad rules fail at load, not at validate.

---

## 6. Evaluator (`evaluator.py`) — returns the firing SET

Pure, deterministic. No wall-clock, no randomness.

```python
from hashlib import sha256
from typing import Any, Protocol


class NoAlertFired(ValueError):
    """No rule fired over the window (a coverage hole to fix per Section 5 / coverage E1)."""


class PrometheusRangeClient(Protocol):
    def query_range(self, query: str, *, start: float, end: float, step: str) -> dict[str, Any]: ...


_STEP_SECONDS = 15
_STEP = f"{_STEP_SECONDS}s"


def derive_alerts(
    *,
    window_start_epoch_seconds: float,
    window_end_epoch_seconds: float,
    prometheus: PrometheusRangeClient,
    rules: list[AlertRule],
) -> list[DerivedAlert]:
    firings: list[DerivedAlert] = []
    for rule in rules:
        result = prometheus.query_range(
            rule.expr, start=window_start_epoch_seconds, end=window_end_epoch_seconds, step=_STEP
        )
        firing = _first_continuous_breach(
            result, rule=rule, window_start_epoch_seconds=window_start_epoch_seconds
        )
        if firing is not None:
            firings.append(firing)
    if not firings:
        raise NoAlertFired("no symptom rule fired over the recorded window")
    # Deterministic order: critical first, then earliest onset, then alertname.
    severity_rank = {"critical": 0, "warning": 1}
    return sorted(firings, key=lambda a: (severity_rank[a.severity], a.starts_at_second, a.alertname))


def _first_continuous_breach(result, *, rule, window_start_epoch_seconds) -> DerivedAlert | None:
    # Flatten the single series; sort points by timestamp.
    # Walk; track start index of the current breach run. A point breaches when
    # _compare(value, rule.comparison, rule.threshold). NaN/inf -> non-breach (reset).
    # When (t_point - t_run_start) >= rule.for_seconds: fire with
    #   starts_at_second = round(t_run_start - window_start_epoch_seconds)
    #   value            = value at t_run_start
    # render annotations from {{value}} / {{starts_at}} only; fingerprint = hash(alertname + sorted labels)
    ...  # full body in Task A4
```

Returning the whole set (not one) is the only change from a single-alert evaluator; determinism is unchanged (fixed step, window-derived range, deterministic walk + sort, content-hash fingerprint). "Byte-identical rebuild" means calling `derive_alerts` twice over the same window yields the identical list.

---

## 7. Integration into `workflow.py`

`assemble_recorded_fixture` gains a `prometheus: PrometheusRangeClient` param threaded into `_manifest`:

```python
def _manifest(scenario, capture, prometheus) -> PublicManifest:
    alerts = derive_alerts(
        window_start_epoch_seconds=capture.window_start_epoch_seconds,
        window_end_epoch_seconds=capture.window_end_epoch_seconds,
        prometheus=prometheus,
        rules=load_rules(),
    )
    return PublicManifest(
        scenario_id=scenario.id, source="opentelemetry-demo", time_unit="second",
        window=TimeWindow(start=0, end=scenario.timing.recording_seconds),
        symptom=scenario.public.symptom,                 # authored narrative kept alongside the set
        available_signals=["metrics", "logs", "traces", "changes"],
        notes=[...], alerts=alerts,
    )
```

`record_scenario_definition` passes `telemetry_clients.prometheus`. `_truth()` is unchanged. The assemble unit test injects a fake `PrometheusRangeClient` returning canned breaching series.

---

## 8. Validator gates (extend `validator.py`)

After existing seal/signal checks:

- Non-empty: `manifest.alerts` has ≥1 alert.
- A. Allow-list (each alert): `alertname in ALLOWED_ALERTNAMES`; label keys in `ALLOWED_LABEL_KEYS`; values in `ALLOWED_LABEL_VALUES`. Fail with `RedactionError`.
- B. Template safety (each alert): no annotation value contains `FORBIDDEN_ANNOTATION_SUBSTRINGS`. Fail with `RedactionError`.
- C. Seal coverage: assert `public/manifest.json` exists, contains `alerts`, and was in the `assert_no_banned_tokens` scan.
- D. Consistency (each alert): `0 <= starts_at_second <= window.end`.

---

## 9. Coverage harness (`coverage.py`) + invariants

```python
def build_coverage_matrix(fixture_dirs: list[Path]) -> dict:
    # {scenario_id: sorted([alertname, ...])} from each public/manifest.json.alerts
    ...

def assert_coverage_invariants(matrix: dict) -> None:
    # E1 coverage : every scenario_id has >= 1 firing.
    # E2 sharing  : every alertname fires for >= 2 distinct scenarios.
    # E3 no-index : every distinct firing SET (frozenset of alertnames) is shared by >= 2 scenarios,
    #               so no set is a unique fingerprint of one fault.
    # raise CoverageError listing offenders; caller writes coverage_matrix.json.
```

E3 is the set-level extension of the topology lesson: a fault must not be identifiable by which combination of alerts fired. If a set is unique, widen a symptom rule until faults cluster onto shared sets (error-type faults onto one set, latency-type onto another), never add a fault-specific rule. A shared set still gives the agent triage signal (it narrows the blast radius) without naming the answer.

---

## 10. Schema migration

`PublicManifest.alerts` is required (≥1), which breaks the two golden fixtures and the committed `payment_failure_001` (no `alerts`).

- Golden fixtures: hand-author an allow-listed `alerts` list in each `manifest.json` (e.g. `[CheckoutFailureRate(critical), FrontendHighErrorRate(critical)]`), `starts_at_second` within window, computed `fingerprint`s.
- Committed real fixture: re-record with the new pipeline so it gains a derived set.
- Test helpers: `_manifest()` in `test_otel_writer.py` gets `alerts=[DerivedAlert(...)]`; the workflow assemble test passes a fake `PrometheusRangeClient`.

---

## 11. Tasks — Part A (offline freeze, eval-critical)

### Task A1: `DerivedAlert` + `PublicManifest.alerts`
**Files:** Modify `sentinel/fixtures/schemas.py`; Test `tests/unit/test_otel_alerting.py`.
- [ ] Step 1: failing test — `DerivedAlert` rejects an unknown field; round-trips `starts_at_second`. A `PublicManifest` with empty `alerts` raises (min_length=1).
- [ ] Step 2: run → FAIL (no `DerivedAlert`).
- [ ] Step 3: add `DerivedAlert` (Section 3) and `alerts: list[DerivedAlert] = Field(min_length=1)` to `PublicManifest`.
- [ ] Step 4: run new tests → PASS; then `pytest -q` → existing writer/workflow/fixture tests now FAIL (manifests lack `alerts`); fixed in A5/A8/A10. Note and proceed.
- [ ] Step 5: commit — `feat(otel-alerting): add DerivedAlert contract and manifest alerts list`.

### Task A2: `AlertRule` + `rules.yaml` loader
**Files:** Create `labs/otel/alerting/{__init__.py,schema.py,rules.yaml}`; Test `tests/unit/test_otel_alerting.py`.
- [ ] Step 1: failing test — `load_rules()` returns ≥3 rules; each alertname allow-listed; each expr contains `service_name="frontend"` or `="checkout"` and none of `payment|cart|ad|recommendation|product-catalog|kafka|shipping`.
- [ ] Step 2: run → FAIL.
- [ ] Step 3: implement `AlertRule` (Section 4) + `load_rules()` (package-relative `rules.yaml`); write `rules.yaml` (Section 4).
- [ ] Step 4: run → PASS.
- [ ] Step 5: commit — `feat(otel-alerting): add symptom rules and loader`.

### Task A3: allow-list + checks
**Files:** Create `labs/otel/alerting/allowlist.py`; Test `tests/unit/test_otel_alerting.py`.
- [ ] Step 1: failing tests — valid alert passes; bad alertname/label-value/annotation-with-`service` each raise `AllowlistError`.
- [ ] Step 2: run → FAIL.
- [ ] Step 3: implement `ALLOWED_*`, `AllowlistError`, `assert_alert_is_symptom_level`, `assert_rule_templates_safe`; call the latter in `load_rules()`.
- [ ] Step 4: run → PASS.
- [ ] Step 5: commit — `feat(otel-alerting): add symptom allow-list and template safety`.

### Task A4: evaluator (set semantics + determinism)
**Files:** Create `labs/otel/alerting/evaluator.py`; Test `tests/unit/test_otel_alerting.py`.
- [ ] Step 1: failing tests with a `FakeRange` client — 45s breach + for_seconds=60 → that rule does not fire; exactly-60s breach fires with correct `starts_at_second`/`value`; multiple breaching rules → full set returned, sorted (critical, earliest, name); no firing → `NoAlertFired`; two calls → identical `model_dump`.
- [ ] Step 2: run → FAIL.
- [ ] Step 3: implement `derive_alerts`, `_first_continuous_breach`, `_compare`, `_fingerprint`, `NoAlertFired` (Section 6). Skip NaN/inf.
- [ ] Step 4: run → PASS.
- [ ] Step 5: commit — `feat(otel-alerting): add deterministic evaluator returning the firing set`.

### Task A5: wire `derive_alerts` into `_manifest`
**Files:** Modify `labs/otel/workflow.py`, `tests/unit/test_otel_workflow.py`.
- [ ] Step 1: update assemble test — `FakePrometheusRange` returns a breaching checkout error series; pass `prometheus=`; assert `manifest.alerts[0].alertname == "CheckoutFailureRate"` and labels `{tier: user_facing, signal: checkout_error_rate}`.
- [ ] Step 2: run → FAIL.
- [ ] Step 3: add `prometheus` param to `assemble_recorded_fixture`; extend `_manifest`; pass `telemetry_clients.prometheus` in `record_scenario_definition`.
- [ ] Step 4: run → PASS.
- [ ] Step 5: commit — `feat(otel-recorder): freeze the derived alert set into the manifest`.

### Task A6: validator gates A–D
**Files:** Modify `labs/otel/validator.py`; Test `tests/unit/test_otel_alerting.py`.
- [ ] Step 1: failing tests — non-allow-listed alertname fails; annotation naming a service fails; `starts_at_second > window.end` fails; empty `alerts` fails; clean set passes.
- [ ] Step 2: run → FAIL.
- [ ] Step 3: implement non-empty + A–D in `validate_fixture` (after existing checks), reusing `RedactionError` for A/B.
- [ ] Step 4: run → PASS.
- [ ] Step 5: commit — `feat(otel-validation): gate the derived alert set for leak safety`.

### Task A7: coverage harness + E1/E2/E3
**Files:** Create `labs/otel/alerting/coverage.py`; Test `tests/unit/test_otel_coverage.py`.
- [ ] Step 1: failing tests — matrix built correctly; E1 fails on a scenario with no firing; E2 fails on an alert firing for one scenario; E3 fails on a firing set unique to one scenario; a healthy matrix passes.
- [ ] Step 2: run → FAIL.
- [ ] Step 3: implement `build_coverage_matrix`, `assert_coverage_invariants` (E1/E2/E3), `CoverageError`, `write_coverage_matrix`.
- [ ] Step 4: run → PASS.
- [ ] Step 5: commit — `feat(otel-alerting): add coverage and no-index invariants`.

### Task A8: migrate golden fixtures + writer test
**Files:** Modify the two `tests/fixtures/otel/*/public/manifest.json`, `tests/unit/test_otel_writer.py`.
- [ ] Step 1: add allow-listed `alerts` lists to both golden manifests; add `alerts=[DerivedAlert(...)]` to `_manifest()` helper.
- [ ] Step 2: run `pytest -q` → all unit tests PASS (closes A1 Step 4 failures).
- [ ] Step 3: commit — `test(otel-fixtures): add derived alert sets to golden fixtures`.

### Task A9: tune thresholds + re-record payment_failure_001 (Phase 1, orchestrator-run)
**Files:** Modify `labs/otel/alerting/rules.yaml`; produce a single-row `coverage_matrix.json`.
- [ ] Step 1: measure baseline user-facing p95 (frontend ~42ms, checkout ~104ms) from the pre-onset slice of the existing recording; set latency thresholds with headroom.
- [ ] Step 2: re-record `payment_failure_001` with the new pipeline (unattended, ~18 min); confirm `manifest.alerts` is non-empty and the set is sealed and passes validation A–D.
- [ ] Step 3: run `coverage.py` over the single fixture; assert E1 (it fires ≥1). E2/E3 need ≥2 scenarios and are deferred to Task A10; the logic is unit-tested in Task A7.
- [ ] Step 4: commit tuned `rules.yaml` + recorded fixture (public only) + single-row `coverage_matrix.json`.

### Task A10 (Phase 2, deferred): record the other 7 scenarios; assert E1/E2/E3 across all 8
Gated on Phase 1 working. Record the remaining scenarios, build the full matrix, and widen symptom rules until E1/E2/E3 hold (watch points: `recommendation_cache_failure`, `kafka_queue_problems`). Widen symptom rules only, never add a fault-specific rule. Not part of this execution.

## 11b. Tasks — Part B (live Alertmanager demo path, demo-only, never eval)

### Task B1: compile `rules.yaml` → Prometheus alerting rules
**Files:** Create `labs/otel/alerting/prometheus_rules.py`; Test `tests/unit/test_otel_alerting.py`.
- [ ] Step 1: failing test — `compile_prometheus_rules(load_rules())` returns a dict with `groups[0].rules`, each `{alert, expr: "<expr> <comparison> <threshold>", for: "<for_seconds>s", labels: {**labels, severity}, annotations}`; assert the boolean expr and `for` string are formed correctly.
- [ ] Step 2: run → FAIL.
- [ ] Step 3: implement `compile_prometheus_rules(rules) -> dict` and a `dump_prometheus_rules(path)` writing YAML.
- [ ] Step 4: run → PASS.
- [ ] Step 5: commit — `feat(otel-alerting): compile symptom rules to Prometheus alerting rules`.

### Task B2: Alertmanager-webhook → `list[DerivedAlert]` mapping
**Files:** Create `labs/otel/alerting/webhook.py`; Test `tests/unit/test_otel_alerting.py`.
- [ ] Step 1: failing test — feed a sample Alertmanager webhook JSON (status=firing, two alerts with `labels.alertname/severity/tier/signal`, `startsAt` RFC3339, `annotations.summary`, `value` via an annotation or label); `alertmanager_payload_to_alerts(payload)` returns `list[DerivedAlert]` with `starts_at_second` relative to the group's earliest `startsAt`, each passing `assert_alert_is_symptom_level`; a payload with a non-allow-listed alertname raises `AllowlistError`.
- [ ] Step 2: run → FAIL.
- [ ] Step 3: implement the mapping: parse `startsAt`, compute the relative `starts_at_second`, carry allow-listed labels, render annotations as-is (already templated by Prometheus), recompute `fingerprint`, run allow-list checks. `value` comes from a numeric annotation Prometheus is configured to emit (`annotations.value`), else `0.0`.
- [ ] Step 4: run → PASS.
- [ ] Step 5: commit — `feat(otel-alerting): map Alertmanager webhook payload to DerivedAlert`.

### Task B3: FastAPI `/alert` endpoint
**Files:** Create `sentinel/api/app.py` (or extend `sentinel/api/__init__.py`); Test `tests/unit/test_api_alert_webhook.py`.
- [ ] Step 1: failing test — using FastAPI `TestClient`, `POST /alert` with a sample firing payload returns 200 and the mapped alert count; a captured `on_alert` callback receives `list[DerivedAlert]`; a non-allow-listed payload returns 422/400 (rejected by the mapping). Use a dependency-injected `on_alert` handler so the agent-kickoff seam is testable without an agent.
- [ ] Step 2: run → FAIL.
- [ ] Step 3: implement the FastAPI app, the `POST /alert` route calling `alertmanager_payload_to_alerts` then a pluggable `on_alert(alerts)` handler (default: structured log + return summary). Document the seam where the real agent will be started once it exists.
- [ ] Step 4: run → PASS.
- [ ] Step 5: commit — `feat(api): add /alert webhook receiver mapping to DerivedAlert`.

### Task B4: deployment overlay + runbook
**Files:** Create `deploy/alertmanager/{alertmanager.yml, compose.alertmanager.yml, README.md}`; generate `deploy/alertmanager/prometheus_rules.yml` via B1.
- [ ] Step 1: write `alertmanager.yml` (a single route → `webhook_configs` pointing at `http://<agent-host>:<port>/alert`, low `group_wait`/`group_interval` for demo snappiness).
- [ ] Step 2: write `compose.alertmanager.yml` adding an Alertmanager container; document mounting `prometheus_rules.yml` into the demo Prometheus `rule_files` and pointing Prometheus `alerting.alertmanagers` at Alertmanager. Because the pinned demo Prometheus config is external, the README gives the exact mount/flag edits rather than mutating the demo in-repo.
- [ ] Step 3: write `README.md` runbook: generate rules (`python -m labs.otel.alerting.prometheus_rules > prometheus_rules.yml`), apply the overlay, run the FastAPI app, inject a fault, observe the webhook fire. State plainly: this path is non-deterministic and never used by the eval harness.
- [ ] Step 4: commit — `feat(deploy): live Alertmanager demo overlay and runbook`.
- [ ] Step 5 (manual, optional): stand it up once, confirm a fault produces a `/alert` POST whose mapped alerts pass the allow-list, screenshot for the demo.

---

## 12. Open decisions / risks (resolve during build)

- Coverage + E3: `recommendation_cache_failure` and `kafka_queue_problems` may be quiet at the user-facing layer; and distinct firing sets may be unique per fault. Resolution order: widen/sensitize a symptom rule so faults cluster onto shared sets; if a fault genuinely has no user-facing symptom, document it and decide whether to relax E1 for that scenario. Never add a fault-specific rule. This needs the real recordings to settle (Task A9).
- Determinism vs retention: re-deriving needs the window still in Prometheus; the frozen set in the manifest is the permanent artifact.
- `frontend`/`checkout` in `expr`: these are symptom locations, never the culprit classification, and the rules are shared, so naming them is not a structural leak.
- Part B agent seam: the `/alert` handler maps + validates + logs the alert set today; the real agent kickoff is wired when the agent lands (the next doc). The seam is dependency-injected so it is testable now.
- Part B Prometheus config: applying our alerting rules requires editing the pinned demo Prometheus config (mount + flags). The runbook documents the exact edits rather than mutating the pinned checkout in-repo.

---

## 13. Self-review (against the spec + the agreed changes)

- §3 modules: covered (Section 2), plus `prometheus_rules.py` and `webhook.py` for Part B.
- §4 `DerivedAlert`: covered; `starts_at_second` deviation documented; single→`alerts` list per the agreed triage decision.
- §5 rules + rules.yaml: covered, exprs grounded; framing generalized from "edge" to "user-facing/symptom-level".
- §6 four leak vectors + the new set-level vector: label (static symptom labels), alertname (allow-list), structural single (E2) and set (E3), annotation (template safety) — covered (Sections 5, 6, 8, 9).
- §7 gates A–E: A–D per alert (Task A6), E1/E2/E3 in coverage (Task A7).
- §8 evaluator determinism: covered (Section 6, Task A4), now returning the set.
- §9 integration: covered (Section 7, Task A5).
- §11 testing: unit (A2–A4, A6, A7, B1, B2), integration (A5), endpoint (B3), harness (A7).
- §12 DoD: mapped to Tasks A1–A9; "single alert" replaced by "frozen firing set" per the agreed decision.
- Appendix A (live path): now in scope as Part B (Tasks B1–B4), demo-only, same `DerivedAlert` contract, leak checks reused.
- §13 decisions: metric names confirmed (Section 0), query source = A (live Prometheus at build), keep authored `symptom` (Section 7), latency thresholds tuned (Task A9), trigger = firing set, Alertmanager path in scope.
