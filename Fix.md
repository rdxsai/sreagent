# Fix: metrics-first RCA for trace-absent scenarios

Status: ready to build
Audience: the building agent (assume no prior context)
Scope: fix the OSS code-mode agent (`sentinel/oss/*`) so it localizes root cause on scenarios with no usable traces (metric-only faults such as delay and loss). Three coupled root causes plus two eval-correctness gaps, found by reading the running pipeline: `sentinel_rcaeval/re2.py` (ingest), `sentinel/lgtm/store.py` (live stores), `sentinel/oss/run.py` (bring-up), and `sentinel/oss/{rca,manager,worker,catalog}.py`.

Do not touch: the sandbox (`sentinel/sandbox/executor.py`), the ingest span-kind inference (`re2.py:_infer_span_kinds`), the two-tier catalog split, or the trace-tree logging. Those are correct and load-bearing.

## Problem summary

The agent sources topology only from traces, classifies faults by the tool that proves them, and gates the worker on the manager's assigned class. On a trace-rich scenario all three resolve and the design looks complete. On a metric-only fault they fail together: no traces means no graph, the taxonomy has no class for a pure latency-metric signal, and the worker has no slot to report a signal the manager did not name. A worker holding a clear latency step (for example p95 359 to 1526) returns no verdict because no hypothesis will accept it.

Confirmed sites:

| id | root cause                                  | evidence                                                              |
| -- | ------------------------------------------- | -------------------------------------------------------------------- |
| A  | topology built only from traces             | `rca.py:63-70` graph worker over `TOPOLOGY_TOOLS`; `catalog.py:25-33` |
| B  | taxonomy and tool subsets coupled to traces | `manager.py:31-43`: `network_edge` uses trace tools; latency has no home |
| C  | worker gated on the assigned hypothesis     | `worker.py:54-74,122`: no full-vector baseline                        |
| D  | trace enumeration non-deterministic         | `store.py:246-255`: wall-clock end plus `trace_cap` sampling          |
| E  | synthesis returns one service, not ranked   | `manager.py:60-94`: AC@3 and Avg@5 need a ranked list                 |

Fixes A, B, C are one path: a metric-only scenario needs all three before it localizes end to end. Do them together. D, E, F are independent and can land in any order.

## Phase 0: confirm before changing

These are assumptions I could not verify by reading. Each changes a fix below. Record answers in `Fix.notes.md` first, then adjust.

1. `sentinel/oss/schemas.py`: read `Plan`, `Hypothesis`, `WorkerVerdict`, `Synthesis`, `GraphResult`. Confirm `Synthesis` carries a single service (fix E assumes so) and the exact `WorkerVerdict` fields `synthesize` reads.
2. `sentinel_rcaeval/truth.py`: for `delay` and `loss`, confirm whether the labeled root-cause service is the caller or callee side of the affected edge. Fixes B and F depend on the side.
3. The feeder that loads a scenario into the live stores (look under `scripts/rcaeval`, `scripts/live-scenario`, or `labs/lgtm`): confirm the Prometheus `job` label equals the reconciled service name that `re2.py:_svc` produces and that `truth.service` uses. If they differ, `LgtmStore.metric_series` (`store.py:139`) returns empty with no error.
4. `sentinel/tools/metrics.py`: list the exact registry names of metric-only readers for latency series, resource series, and error rate at onset. Fixes B and C need them. The plan assumes `metrics_resource_saturation`, `metrics_compare_baseline`, `metrics_top_movers` exist (they appear in `manager.py`), and that a per-service latency-at-onset reader exists or can be added.
5. The offline scorer (`scripts/eval` or the scorecard): confirm whether it reads a ranked candidate list or one service. Fix E updates it if it reads one.

## Fix 1: topology provider with a non-trace fallback chain

Problem. `rca.py:63-70` builds the graph as a worker over `TOPOLOGY_TOOLS`, which (`catalog.py:25-33`) is entirely trace and topology tools. On a trace-absent or trace-weak scenario that worker returns `{"edges": [], "ranked_services": [], "notes": "graph worker failed"}` (`rca.py:70`), and the manager then plans over an empty graph, which kills its propagation and blast-radius reasoning.

Change. Add a topology provider that returns a graph from the first source that can build one. Topology is a maintained input, not a per-incident trace computation. For the benchmark systems the graph is a known constant; a trace source stays in the chain so trace-rich scenarios still sharpen.

Contract. New module `sentinel/oss/topology.py` (distinct from `sentinel/tools/topology.py`):

```python
class Graph(BaseModel):
    edges: list[tuple[str, str]]     # directed caller -> callee
    ranked_services: list[str]       # blast-radius order, widest first
    source: str                      # "static" | "trace" | "causal"
    notes: str = ""

class TopologySource(Protocol):
    name: str
    def build(self, store, *, onset: int) -> Graph | None   # None: this source could not build one
```

Sources, tried in order, first non-empty wins:

| order | source  | how it builds edges                                                        | availability            |
| ----- | ------- | -------------------------------------------------------------------------- | ----------------------- |
| 1     | static  | load `rcaeval/topology/<system>.json`, keyed by the case_id system prefix  | always, benchmark systems |
| 2     | trace   | the current graph worker over `TOPOLOGY_TOOLS`, returns None if edges empty | only when traces present |
| 3     | causal  | lag-correlation over per-service latency series, direction by which leads   | metrics only, last resort |

The case_id system prefix comes from the `<system>_<service>_<fault>_<instance>` shape in `re2.py:71`. Add `system_of(case_id) -> str`.

Ranking runs after a source returns edges and is shared across sources. It is an anomaly overlay, not part of the source:
- From metrics at `onset`, mark a service anomalous if any of resource, latency, or error stepped at that time. Reuse the same onset-step test the worker uses (fix 3), so the definition is one function.
- Rank anomalous services: a node whose downstream neighbours are not anomalous ranks higher (the boundary is the origin). Break ties by in-degree within the anomalous subgraph.

Static artifacts. `rcaeval/topology/{online_boutique,sock_shop,train_ticket}.json`, each `{"edges": [["frontend","cartservice"], ...]}`. Build once per system by running the trace source on one clean trace-rich case and freezing its edges, or from the system's published architecture. Record which case each was derived from in a top-of-file comment or a sidecar note.

Files. New `sentinel/oss/topology.py`. `rca.py`: replace lines 63-70 with `graph = resolve_topology(store, system=system_of(case_id), onset=onset)`. Keep `GraphResult` as the trace source's `finish()` contract; `Graph` is what the manager plans over.

Verification.
- A metric-only case (no traces fed) yields a non-empty `Graph` with `source == "static"` and a non-empty `ranked_services`.
- A trace-rich case: assert static and trace edges agree on one clean case, so a stale static artifact is caught.
- The manager's `plan` never receives `edges: []`.

## Fix 2: signature taxonomy, metrics-first

Problem. `manager.py:31-43` uses three classes tied to the proving tool: saturation, network_edge, internal (errors and latency). The `tool_subset` guidance points `network_edge` at `traces_edge_latency_origin` and `internal` at error-origin tools. A pure latency-metric signal with no cpu, no errors, and no traces matches none of them. The standalone latency signature the earlier design had was folded into `internal` and then tooled for errors, so it fell into a gap.

Change. Classify by observable signature, not by proving tool. Three per-service symptom signatures, all detectable from metrics: resource, latency, error. `edge` becomes an optional localizer, not a class. Reinstate latency as first-class: own p50 or p95 rose at onset with cpu flat and no errors is origin evidence on its own.

Rewrite `_PLAN_SYSTEM`:
- Classes are the three symptom signatures. `edge` is how you localize latency and error across services, not a fourth symptom.
- Anti-pattern text: every onset signature is first-class origin evidence regardless of whether the request path looks quiet. Trace the anomaly backward across all signature types, not only latency; an upstream resource step outranks a downstream latency symptom.
- `tool_subset` rule: the tools for the assigned signature plus a ruling-out core (one resource reader, one latency reader, one error reader). Metrics-first. Traces are enrichment, added only when `graph.source == "trace"`.
- Replace the worked example so its latency hypothesis uses metric readers plus the ruling-out core, not trace tools.

Contract (`schemas.py`):

```python
class Hypothesis(BaseModel):
    candidate_service: str
    signature: Literal["resource", "latency", "error"]   # was fault_class
    edge: tuple[str, str] | None = None                  # localizer, kept
    tool_subset: list[str]
    investigation_directive: str                         # was rationale; 1-3 sentences of focus
```

Files. `manager.py` (`_PLAN_SYSTEM`, and the repair fallback subset at lines 77-80). `schemas.py` (`Hypothesis`). `rca.py` (lines 79-80 build `hyp_text` from `fault_class`/`edge`; switch to `signature`/`edge`/`investigation_directive`). Decide in Phase 0 whether to keep `fault_class` as a deprecated alias or rename in one pass.

Verification. On a metric-only delay case, `plan` emits at least one `latency` hypothesis whose `tool_subset` holds only registry-valid metric tools, and the repair at `manager.py:78` does not substitute.

## Fix 3: worker reports the full onset signature vector

Problem. `worker.py:54-74,122` frames the run around confirming the one assigned hypothesis. There is no baseline that reads what actually stepped at onset, so a signal the manager did not name has nowhere to land. This is the direct cause of the no-verdict on a clear latency step.

Change. Make the worker's first obligation to establish the onset change-point, read the full signature vector for the candidate service from metrics (resource, latency, error at onset), report it, then judge the hypothesis. The assigned signature is a prior, not a gate. The ruling-out core from fix 2 guarantees the worker can read the whole vector.

Contract (`schemas.py`):

```python
class WorkerVerdict(BaseModel):
    hypothesis: str
    supported: bool
    root_cause_service: str | None
    signature: Literal["resource", "latency", "error"] | None   # the signature actually observed
    observed_signatures: dict[str, bool]                        # resource / latency / error (/ edge)
    confidence: float
    evidence: list[str]
```

`_SYSTEM` gains the vector step and the rule: a signal that stepped at onset is origin evidence even if it is not the signature you were asked about; put it in `observed_signatures` and let `supported` reflect the whole vector plus graph position. The example turn should read the vector, then `finish()`.

Files. `worker.py` (`_SYSTEM` and the example). `schemas.py` (`WorkerVerdict`). `manager.py` (`_SYNTH_SYSTEM` should read `observed_signatures`).

Verification (the regression test for the crack). Feed the worker a metric-only latency case with a deliberately wrong assigned signature (resource). The verdict still reports `observed_signatures["latency"] == True` and does not `harness_fail`.

## Fix 4: deterministic trace enumeration

Problem. `store.py:246-255` searches Tempo with `end = now_s or time.time()` and caps at `trace_cap` (1000). The span set the agent sees depends on wall-clock and on Tempo's return order, so two runs of the same frozen scenario can hydrate different spans and localize differently. Metrics via `query_range` are deterministic; traces are not.

Change.
- `run.py` passes an explicit `now_s` to `LgtmStore`, derived from the window (for example `window_end_s` plus a fixed margin), so the search end is stable. `LgtmStore` already takes `now_s` (`store.py:83`); `run.py:122-126` does not set it.
- In `_enum_trace_ids`, sort the returned trace ids deterministically before applying `trace_cap`, so the cap keeps a stable subset.
- Document the contract in the module docstring: same scenario plus same `now_s` yields the same span set. Model sampling still varies; the telemetry the agent sees does not.

Files. `run.py` (pass `now_s`). `store.py` (`_enum_trace_ids` sort; docstring).

Verification. Hydrate the same trace-rich scenario twice with the same `now_s`; assert identical `span_id` sets and identical `stats`.

## Fix 5: ranked candidate list from synthesis

Problem. `manager.py:60-94` picks one `root_cause_service`. RCAEval scores AC@1, AC@3, Avg@5 over ranked candidates, so AC@3 and Avg@5 degenerate to AC@1 with a single answer.

Change. Synthesis returns a ranked list, most likely first. Rank by worker `supported` and `confidence`, then graph position (most-upstream origin first). Keep `root_cause_service` as `ranked_services[0]`.

Contract (`schemas.py`):

```python
class Synthesis(BaseModel):
    ranked_services: list[str]      # up to 5, most likely first
    root_cause_service: str         # == ranked_services[0]
    fault_type: str
    justification: str
```

Files. `schemas.py` (`Synthesis`). `manager.py` (`_SYNTH_SYSTEM` asks for the ranked list; `synthesize`). `rca.py` (final_answer log). The scorer under `scripts/eval` reads `ranked_services`, with the single-service path as fallback.

Verification. Synthesis on a multi-verdict case returns 1 to 5 ranked services with top-1 equal to `root_cause_service`; the scorer's AC@3 differs from AC@1 when truth sits at rank 2 or 3.

## Fix 6: naming and attribution alignment

Problem. Two silent-wrong paths. (a) `store.py:139` queries metrics by the `job` label; if the feeder `job` value differs from the reconciled service name, `metric_series` returns empty. (b) `manager.py:35` attributes `network_edge` to the upstream service, which may not match the side RCAEval labels for delay and loss.

Change.
- (a) After Phase 0.3, if the feeder `job` differs from the reconciled service name, add a reconciliation map in `LgtmStore` that mirrors `re2.py:_svc`. Add an eval-side sanity check in `run.py`: `set(store.list_services()) & set(truth.accepted_services)` is non-empty, else log a loud warning. Keep truth out of the agent path; this check runs beside it, not inside it.
- (b) After Phase 0.2, set the localizer's candidate side to whichever side truth labels. With fix 2, `edge` is a localizer and `candidate_service` is the service showing the signature, so this may already align; confirm on one delay case end to end.

Files. `store.py` (optional reconciliation). `run.py` (eval-side check). `manager.py` (edge wording only if the side must change).

Verification. On one delay case, `store.metric_series(truth.service, "latency_p95_ms")` returns rows, and the agent's top-1 equals `truth.service`.

## Cross-cutting invariants

- The manager never reads raw telemetry or truth. Catalog-only.
- Prompt prefixes stay byte-stable for provider caching: keep the frozen tool order in `catalog.py`; keep static example text out of per-scenario fields.
- No fix may make a metric-only scenario depend on a trace tool. Traces are enrichment, gated on `graph.source == "trace"`.
- Keep `harness_fail` separate from wrong. A missing or mismatched signature is a wrong answer; an uncaught traceback or no verdict is a crash.
- Do not weaken the sandbox or the seal and budget hooks.

## Testing strategy

Unit:
- topology resolver: static returns edges per system; trace returns None on empty spans; causal builds a graph from a synthetic two-series lead-lag fixture.
- onset-step test: flags a stepped series, ignores a flat one at the same onset.
- worker verdict schema: `observed_signatures` required; a verdict missing it fails validation.
- synthesis: `ranked_services` bounds; `root_cause_service == ranked_services[0]`.

Integration (one metric-only delay case, traces absent):
- graph non-empty, `source == "static"`.
- plan emits a latency hypothesis with a metric-only subset.
- worker returns `observed_signatures["latency"] == True` under a wrong assigned signature.
- top-1 equals `truth.service`.

Regression:
- the latency-crack case (or a synthetic p95 359 to 1526): no `harness_fail`, latency reported.

Determinism:
- same scenario plus same `now_s` hydrates identical spans twice.

## Definition of done

- [ ] Phase 0 findings in `Fix.notes.md`; fixes adjusted to them.
- [ ] topology provider with static, trace, and causal sources; `rca.py` uses it; metric-only scenarios get a non-empty graph.
- [ ] static topology artifacts for the three systems, each traceable to the case it came from.
- [ ] signature taxonomy in the manager prompt and `Hypothesis` schema; latency first-class; subsets metrics-first with a ruling-out core.
- [ ] worker reports `observed_signatures`; assigned signature is a hint; regression test green.
- [ ] deterministic trace enumeration; determinism test green.
- [ ] `ranked_services` from synthesis; scorer reads it; AC@3 distinct from AC@1 where truth is not rank 1.
- [ ] naming and attribution checks pass on one delay case end to end.
- [ ] a full metric-only case localizes correctly end to end on gpt-oss-120b.
- [ ] existing trace-rich cases do not regress.

## Decisions to confirm before building

1. Static topology source: derive from a clean trace-rich case per system, then freeze, and record the source case. Confirm this over hand-writing from published architecture.
2. Causal source: build now, or leave a stub returning None until a system without a static artifact appears. If all three systems get static artifacts, causal can wait.
3. `fault_class` to `signature`: keep a deprecated alias, or rename in one pass. Depends on the scorer and trace consumers found in Phase 0.
