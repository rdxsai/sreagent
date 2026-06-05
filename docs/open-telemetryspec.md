# Sentinel v1 OpenTelemetry Incident Lab Spec

Sentinel v1 uses OpenTelemetry Demo as a realistic telemetry generator. The goal
of this layer is to produce sealed, replayable incident fixtures that the agent
can investigate through tools.

This document covers only the telemetry lab: scenario control, recording,
normalization, fixture storage, ground truth, redaction, validation, and eval
safety. It does not define the agent runtime.

## Design Rationale

OpenTelemetry Demo is a running microservice-based demo application designed to
represent a near-real-world distributed system. It emits metrics, logs, and
traces across many services and includes built-in feature-flag failure scenarios.

We use it because it gives richer telemetry than a small custom simulator would
within the project window.

However, telemetry alone does not provide ground truth. Ground truth comes from
the scenario controller: the component that deliberately enables a known failure
at a known time.

The v1 design is therefore:

```text
OpenTelemetry Demo
  + controlled feature-flag scenario
  + steady workload
  -> recorded telemetry
  -> normalized public fixtures
  -> private eval truth
```

The agent sees only public fixtures. The eval harness sees private truth.

## Scope

v1 supports recorded, replayable scenarios from OpenTelemetry Demo feature flags.

v1 does not support custom network fault injection, custom service patches, or
large generated scenario sweeps. Those are v2.

Expected v1 target:

```text
6-10 curated recorded scenarios
2-3 candidate confusable pairs
1 validated long-horizon demo fixture
```

This is a curated benchmark, not a synthetic generator.

## Source System

The source system is a pinned checkout of OpenTelemetry Demo.

Record the following for every fixture:

```yaml
otel_demo:
  repository: https://github.com/open-telemetry/opentelemetry-demo
  git_sha: "<pinned sha>"
  deployment_mode: "docker-compose" # or kubernetes
  compose_files:
    - compose.yaml
    - compose.observability.yaml
  recorded_at: "<iso timestamp>"
```

Do not record against a floating `main` without storing the exact SHA.

## Built-In Scenario Catalog

OpenTelemetry Demo exposes feature flags through `flagd`.

Important: docs-facing names and raw flag keys may differ. The recorder should
use raw flag keys, but public fixtures must not expose them.

Example mapping:

```yaml
display_name: paymentServiceFailure
raw_flag_key: paymentFailure
public_change_kind: payment_processing_config_change
root_cause_type: payment_charge_failure
```

Initial v1 scenario candidates:

```yaml
- raw_flag_key: paymentFailure
  display_name: paymentServiceFailure
  expected_root_cause: payment service charge failure

- raw_flag_key: paymentUnreachable
  display_name: paymentServiceUnreachable
  expected_root_cause: checkout cannot reach payment service

- raw_flag_key: recommendationCacheFailure
  display_name: recommendationServiceCacheFailure
  expected_root_cause: recommendation service cache/memory issue

- raw_flag_key: productCatalogFailure
  display_name: productCatalogFailure
  expected_root_cause: product catalog failure for a specific product

- raw_flag_key: adHighCpu
  display_name: adServiceHighCpu
  expected_root_cause: ad service CPU saturation

- raw_flag_key: adManualGc
  display_name: adServiceManualGc
  expected_root_cause: ad service GC pressure

- raw_flag_key: loadGeneratorFloodHomepage
  display_name: loadgeneratorFloodHomepage
  expected_root_cause: frontend overload from traffic flood

- raw_flag_key: kafkaQueueProblems
  display_name: kafkaQueueProblems
  expected_root_cause: Kafka lag / queue backlog
```

Potential confusable pairs:

```yaml
- paymentFailure vs paymentUnreachable
- adHighCpu vs adManualGc
- loadGeneratorFloodHomepage vs adHighCpu
```

These must be validated empirically after recording. Do not assume they are hard
or confusable until fixture fingerprints confirm it.

## Three-Layer Seal

The credibility of the benchmark depends on this separation.

```text
Layer 1: Scenario Controller
  Starts workload, changes flags, records private injection metadata.

Layer 2: Public Fixture Store
  Contains only observable metrics/logs/traces/topology/sanitized changes.

Layer 3: Eval Truth
  Contains injected flag, root cause, onset, expected evidence, and grading keys.
```

The agent and tools are constructed only with Layer 2.

The eval harness is the only component that reads Layer 3.

Hard rules:

```text
tools/ must not read eval_only/
tools/ must not receive raw flag names
tools/ must not expose feature_flag.* attributes
tools/ must not expose truth.json
tools/ must not expose injection_log.json
```

## Fixture Layout

Each recorded scenario lives under one directory.

```text
fixtures/
  payment_unreachable_001/
    public/
      manifest.json
      topology.json
      metrics.jsonl
      logs.jsonl
      traces.jsonl
      changes.jsonl
      runbooks.jsonl

    eval_only/
      truth.json
      injection_log.json
      raw_flag_snapshot.before.json
      raw_flag_snapshot.after.json

    raw/
      prometheus/
      jaeger/
      logs/
      collector/
```

Only `public/` is passed to tools.

`raw/` may contain unsanitized captures for debugging, but it must never be used
by the agent or committed if it contains leaked flag names or excessive noise.

Prefer committing only sanitized public fixtures plus private eval truth.

## Scenario Lifecycle

Every scenario is recorded through a controller script.

```text
1. Start OpenTelemetry Demo at pinned SHA.
2. Reset all feature flags to off/default.
3. Start steady workload.
4. Warm up baseline period.
5. Start recorder.
6. Emit sanitized public decoy change events.
7. Enable exactly one target failure flag.
8. Continue workload for post-onset period.
9. Stop recorder.
10. Normalize telemetry.
11. Redact leaks.
12. Validate fixture.
13. Write public fixture and private truth.
```

Default timing:

```yaml
warmup_seconds: 180
recording_seconds: 900
injection_at_seconds: 300
post_injection_seconds: 600
```

The agent should see enough pre-onset and post-onset data.

## Public Manifest

`public/manifest.json` describes the scenario without revealing the answer.

```json
{
  "scenario_id": "payment_unreachable_001",
  "source": "opentelemetry-demo",
  "time_unit": "second",
  "window": {
    "start": 0,
    "end": 900
  },
  "symptom": "Checkout failures increased during the recording window.",
  "available_signals": ["metrics", "logs", "traces", "topology", "changes"],
  "notes": [
    "Feature flag names and private injection metadata are intentionally redacted."
  ]
}
```

Do not put the raw flag key or root-cause label here.

## Private Truth

`eval_only/truth.json` is the authoritative answer.

```json
{
  "scenario_id": "payment_unreachable_001",
  "injection": {
    "raw_flag_key": "paymentUnreachable",
    "variant": "on",
    "enabled_at_second": 300
  },
  "root_cause": {
    "kind": "edge",
    "caller": "checkout",
    "callee": "payment",
    "type": "dependency_unreachable"
  },
  "culprit_change_id": "chg_0003",
  "expected_evidence": [
    "checkout error rate rises after onset",
    "payment dependency spans fail or disappear from successful checkout traces",
    "logs show checkout/payment connectivity errors",
    "symptoms begin after sanitized change chg_0003"
  ],
  "decoy_change_ids": ["chg_0001", "chg_0002"]
}
```

## Public Change Events

The agent needs operational context, but not raw flag truth. Public changes must
be sanitized.

Bad public event:

```json
{
  "flag": "paymentUnreachable",
  "variant": "on"
}
```

Good public event:

```json
{
  "id": "chg_0003",
  "time": 300,
  "service": "checkout",
  "kind": "runtime_config_change",
  "summary": "payment dependency routing configuration changed",
  "diff_touches": ["payment_client", "service_discovery"]
}
```

Every scenario should include at least one decoy change near the incident window.

## Redaction Rules

This is the most important safety section.

OpenTelemetry feature-flag instrumentation may emit attributes such as:

```text
feature_flag.key
feature_flag.result.variant
feature_flag.result.value
feature_flag.provider.name
feature_flag.result.reason
feature_flag.version
```

These can leak the answer.

The normalizer must remove or sanitize:

```text
raw flag keys
raw flag variants
feature_flag.* attributes
flagd config contents
flag descriptions
flagd UI logs
scenario filenames inside public metadata
obvious strings like "paymentUnreachable"
obvious strings like "recommendationCacheFailure"
```

Allowed public replacement:

```json
{
  "config_event_id": "chg_0003",
  "change_category": "runtime_config_change"
}
```

The redaction test should scan all public fixture files for banned tokens.

Example banned token list:

```text
paymentFailure
paymentUnreachable
recommendationCacheFailure
productCatalogFailure
adHighCpu
adManualGc
cartFailure
kafkaQueueProblems
loadGeneratorFloodHomepage
imageSlowLoad
emailMemoryLeak
feature_flag.key
feature_flag.result.variant
feature_flag.result.value
```

If any public fixture contains those tokens, the fixture fails validation.

## Normalized Telemetry Schemas

Metrics:

```json
{
  "time": 312,
  "service": "checkout",
  "metric": "request_error_rate",
  "value": 0.22,
  "unit": "ratio",
  "attributes": {
    "route": "/api/checkout"
  }
}
```

Logs:

```json
{
  "time": 315,
  "service": "checkout",
  "severity": "ERROR",
  "message": "dependency call failed",
  "attributes": {
    "dependency": "payment",
    "operation": "Charge"
  },
  "trace_id": "abc123"
}
```

Traces:

```json
{
  "trace_id": "abc123",
  "span_id": "def456",
  "parent_span_id": "root123",
  "time": 315,
  "service": "checkout",
  "operation": "PlaceOrder",
  "duration_ms": 842,
  "status": "ERROR",
  "attributes": {
    "rpc.system": "grpc"
  }
}
```

Topology:

```json
{
  "services": ["frontend", "checkout", "payment", "cart"],
  "edges": [
    {"caller": "frontend", "callee": "checkout"},
    {"caller": "checkout", "callee": "payment"}
  ]
}
```

## Fixture Validation

A fixture is accepted only if it passes validation.

Required checks:

```text
public files contain no banned flag/truth tokens
private truth exists and matches scenario controller output
target signal appears after injection
baseline window is mostly healthy
post-injection window shows measurable impact
at least one relevant metric/log/trace evidence exists
at least one decoy change exists
decoys are not identical to the culprit change
tool replay over public fixtures is deterministic
```

Suggested quantitative gates:

```yaml
error_rate_delta_min: 0.05
latency_p95_delta_min_ratio: 1.5
target_service_signal_after_onset: true
pre_onset_error_rate_max: 0.05
minimum_trace_count: 50
minimum_log_count: 20
```

Confusability validation:

```text
For candidate pairs, compute fixture fingerprints:
- impacted services
- earliest anomalous service
- top error services
- top latency services
- dominant log clusters
- slowest critical-path spans
- resource anomalies
- change-event timing

If fingerprints are completely distinct, the pair is not a hard eval pair.
```

## Recorder Components

Recommended layout:

```text
labs/otel/
  controller.py
  recorder.py
  normalizer.py
  redactor.py
  validator.py
  fingerprint.py
  scenarios.yaml
```

Responsibilities:

```text
controller.py
  starts/stops scenario, toggles flags, writes private injection log

recorder.py
  collects raw metrics/logs/traces during the scenario window

normalizer.py
  converts backend-specific telemetry to Sentinel JSONL schemas

redactor.py
  removes answer-leaking fields and strings

validator.py
  enforces quality gates and seal checks

fingerprint.py
  computes scenario summaries for confusability analysis
```

## V1 Scenario YAML

```yaml
id: payment_unreachable_001
display_name: "Checkout payment dependency unreachable"
raw_flag_key: paymentUnreachable
variant: "on"

timing:
  warmup_seconds: 180
  injection_at_seconds: 300
  recording_seconds: 900

workload:
  profile: checkout_steady
  users: 20
  spawn_rate: 2

public:
  symptom: "Checkout failures increased during the recording window."
  sanitized_change:
    id: chg_0003
    service: checkout
    kind: runtime_config_change
    summary: "payment dependency routing configuration changed"
    diff_touches:
      - payment_client
      - service_discovery
  decoy_changes:
    - id: chg_0001
      service: frontend
      kind: deploy
      summary: "frontend copy and layout assets changed"
    - id: chg_0002
      service: recommendation
      kind: runtime_config_change
      summary: "recommendation scoring parameter changed"

truth:
  root_cause:
    kind: edge
    caller: checkout
    callee: payment
    type: dependency_unreachable
```

## What Not To Expose To The Agent

Never expose:

```text
flagd UI
flagd raw JSON
raw flag key
raw feature flag evaluation attributes
private injection log
truth.json
scenario builder config
validation report with expected root cause
source code comments that name the injected fault
```

The agent may see sanitized change events, topology, telemetry, and runbooks.

## Tests Owed

```text
test_public_fixtures_have_no_banned_tokens
test_tools_cannot_read_eval_only
test_truth_exists_for_every_public_fixture
test_every_fixture_has_decoy_change
test_replay_is_deterministic
test_recorded_fixture_passes_quality_gates
test_confusable_pair_fingerprints_are_not_trivially_distinct
test_public_manifest_does_not_include_raw_flag_key
```

## V1 Limitations

v1 is intentionally constrained by OpenTelemetry Demo's built-in flags.

This means:

```text
variant count is curated, not seed-generated
fault mechanisms are limited to available flags
some scenarios may be educationally obvious
confusable pairs must be validated empirically
```

v2 should add custom boundary-level injections, such as service-edge latency,
connection resets, and packet loss, to create stronger look-alike scenarios.

## Design Claim

Sentinel v1 uses OpenTelemetry Demo for telemetry realism and scenario-controller
truth for objective eval. The tradeoff is fewer scenarios than a simulator would
generate, but each accepted fixture contains real distributed-system telemetry.
The three-layer seal prevents the agent from reading the injected answer while
still allowing the eval harness to grade root-cause accuracy.

## Sources

- [OpenTelemetry Demo repo](https://github.com/open-telemetry/opentelemetry-demo)
- [OpenTelemetry Demo feature flags](https://opentelemetry.io/docs/demo/feature-flags/)
- [OpenTelemetry feature flag semantic conventions](https://opentelemetry.io/docs/specs/semconv/feature-flags/feature-flags-events/)
- [AIOpsLab OpenTelemetry Demo localization problems](https://deepwiki.com/microsoft/AIOpsLab/10.2-localization-and-analysis-problems)
