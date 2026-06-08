# Spec: Sentinel tooling layer (minimal core + eval)

Status: building
Audience: Claude Code
Scope: the first slice of the agent harness from the Sentinel agent design brief. Build the validated 15-tool core registry, a terminal report tool, the fixture-backed executor seam, typed errors, golden unit tests, and the cookbook tool-eval harness. Prove it on `payment_failure_001` (service fault) and `payment_unreachable_001` (edge fault), which both have frozen alerts and ground truth.

This is greenfield: `sentinel/tools/` and `sentinel/registry/` are stubs, and `sentinel_tool_eval/` does not exist. Nothing validated is on disk to wrap.

## Layering

```
Anthropic model --picks tool by description--> ToolRegistry
                                                  | dispatch(name, input, store)
                                                  v
                     tool fn(params: InModel, store) -> OutModel   (workflow-shaped, lean output)
                                                  | reads via
                                                  v
                          TelemetryStore <- FixtureStore(load_public_fixture, public/ only)
                                            (swap point for live Prometheus/Jaeger/Loki)
```

Tools never touch the filesystem or `eval_only/`. They call a `TelemetryStore` bound to one scenario's public fixture. The grader is the only code that reads `eval_only/truth.json`.

## Registry (`sentinel/registry`)

One `ToolRegistry`; `@tool(namespace=...)` reads the function's type hints (`params` annotation = input model, return annotation = output model) as the single source of truth. The registry derives Anthropic tool schemas from `InModel.model_json_schema()`, dispatches by name with input validation, and supports `subset(namespaces=, names=)` for later investigator scoping. Selection is model-driven; no hand-routed dispatch. On a bad input the dispatcher returns a structured `{"error": {code, message, hint, example}}` instead of a traceback.

## Tools (`sentinel/tools`)

Scenario is bound to the store, so it is not a per-call parameter. Returns are lean (no raw span attribute blobs) and token-bounded with truncation notes.

Namespaces and the validated core (15) plus the terminal report tool:

- traces: traces_build_topology, traces_first_error_time, traces_error_origin, traces_find, traces_get_trace
- metrics: metrics_list_series, metrics_series, metrics_compare_baseline, metrics_detect_shift
- logs: logs_search, logs_for_trace
- changes: changes_search, changes_lookback
- correlate: correlate_attribute_fault, correlate_timeline
- report: report_root_cause (terminal; input is a typed RootCauseReport; ends the run)

Grounded rules (confirmed against both fixtures, do not regress):

- Attribution is trace-based on `span.kind`. The error frontier is the deepest erroring span (no erroring descendant). A SERVER frontier span means a service fault at its service. A childless CLIENT frontier span means an edge fault from its service to the callee. Never attribute node vs edge from a per-service error-rate metric.
- The callee of a CLIENT span comes from the child SERVER span's service (via `parent_span_id`) when present, else from the RPC operation name (`oteldemo.PaymentService/Charge` -> `payment`). `server.address` is unreliable (IP or absent).
- Onset is the first error span time (`traces_first_error_time`), not the metric alert time.
- Change correlation looks backward from the trace-based onset, asymmetrically.
- Logs are noisy: reach them through a failing trace with `logs_for_trace`, case-fold severity, truncate hard.

Composable typed I/O chains: traces_first_error_time -> changes_lookback; traces_find(status=ERROR) -> logs_for_trace; traces_error_origin -> correlate_attribute_fault; changes_lookback -> correlate_timeline; the whole investigation -> report_root_cause.

## Typed contracts

Reuse `Topology`, `ChangeEvent`, `RootCause` from `sentinel/fixtures/schemas.py`. New I/O models live in `sentinel/tools/models.py` (SpanSummary, Onset, Origin, Attribution, Series, SeriesPoint, SeriesKey, LogLine, TimelineEntry, Hypothesis, EvidenceReport, RootCauseReport). `RootCauseReport.root_cause` mirrors `PrivateTruth.root_cause` so the grader compares directly.

## Eval harness (`sentinel_tool_eval`)

Cookbook flow (evaluating-agent-tools): one incident task per scenario (symptom + frozen alerts from the manifest, never naming a tool); a manual agentic loop with tools = registry schemas, dispatch over `FixtureStore`, max-iteration and token-budget guards; the model emits a `<feedback>` block on tool ergonomics and calls `report_root_cause` to finish; the grader reads `truth.json` only and scores field by field (root_cause kind + service/caller/callee + type, culprit_change_id, decoys ⊆ ruled_out). Metrics recorded per task: tool-call count, sequence, tokens (input/output/cache), tool errors.

Credit discipline: model `claude-opus-4-6` in normal mode (no adaptive thinking), effort `medium`, `max_tokens` small per turn, prompt caching on the stable tool+system prefix, per-call usage summed and a hard per-task token cap that aborts the loop. The key is read from the environment (`.env` loaded if `ANTHROPIC_API_KEY` is unset); never hardcoded.

Overfitting guard (only two ground-truthed scenarios): tune on the service fault, hold out the edge fault; vary task phrasing; tune for general SRE ergonomics, not these two answers.

## Testing

- Golden unit tests per tool, anchored on measured values: onset ~329; payment SERVER errors 36 (failure) vs 0 (unreachable); checkout->payment CLIENT errors 36/37; culprit chg_0003; topology contains frontend->checkout and checkout->payment.
- Grader unit test on a fabricated report vs `truth.json` (no API).
- Live eval run over both scenarios; report accuracy, tokens, and ergonomics findings.

## Build sequence

1. errors + models + store + registry (test-first).
2. the tools, traces namespace first.
3. eval harness + grader; run on both scenarios.
4. read transcripts, tune descriptions/schemas.
