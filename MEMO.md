# Sentinel: an autonomous SRE incident-response agent

## What I built

Sentinel investigates a production-shaped incident the way an on-call engineer would. Given a firing alert it reasons across the service dependency chain, inspects recorded telemetry through a registry of 50 tools with typed Pydantic inputs and outputs that compose (one tool's structured output feeds the next), traces the symptom to its root cause, and emits a structured incident report. It is a manager agent that triages and delegates to isolated investigator subagents, backed by a sealed, replayable telemetry environment, an evaluation harness that grades it against real ground truth and reports pass@k across repeats, and a live web demo that streams its reasoning token by token. It resolves all six sealed incidents correctly, with 181 unit and integration tests green.

## The environment

I had no live production system to query, so I built a stand-in from the OpenTelemetry Demo, a fully instrumented reference microservices shop. OpenTelemetry is the standard for emitting telemetry (traces, metrics, logs) and shipping it through a Collector to backends (Jaeger, Prometheus, OpenSearch); the Demo ships feature flags that make a chosen service misbehave, and that fault then surfaces in the real telemetry. For each incident I inject one flag at a known onset under self-driven load, capture the telemetry from the three backends, run an alerting layer (PromQL thresholds on errors, latency, and CPU that fire a single UserFacingDegradation alert), normalize it, and write a sealed fixture: public/ for the agent, eval_only/ for the grader. The tools read only public/, so the ground truth never enters the agent's context, which is what lets me score it honestly. Three coverage invariants (E1/E2/E3) keep the alert from fingerprinting the answer, so the difficulty comes from the data: one real culprit change hidden among five plausible decoys.

The six recorded incidents, each driven by one demo feature flag:

| Feature flag           | Microservice    | Fault it injects                                                  |
| ---------------------- | --------------- | ---------------------------------------------------------------- |
| paymentFailure         | payment         | Charge attempts error: a service fault on the payment path       |
| paymentUnreachable     | checkout        | Checkout's calls to payment fail to connect: an edge fault       |
| productCatalogFailure  | product-catalog | Product lookups return errors                                    |
| cartFailure            | cart            | Cart operations fail as the cart loses its cache connection      |
| adManualGc             | ad              | Heavy garbage-collection pauses: high latency with no errors     |
| adHighCpu              | ad              | CPU saturation: cores pegged while latency and errors stay normal |

## The agent

An engineer orients before digging in, so the manager triages first: it builds the dependency graph, finds onset, and localizes where the failure originates, then picks a few candidate services (not every node). For each it spawns a subagent in an isolated context with a scoped, recursion-free tool set; the subagent goes deep and returns one structured finding. The isolation is the point: deep investigation is many tool calls, and if the manager did it all its context would degrade, so a subagent absorbs that detail and hands back only the result. A single investigation runs 35 to 70 tool calls across the manager and its subagents without losing the plan. The loop is plain async Python with production scaffolding: retries with backoff, rate limiting, typed errors, structured tracing, and a deterministic hooks layer at PreToolUse, PostToolUse, and SubagentStop (modeled on the Claude and OpenAI hook semantics) enforcing a leak-safety seal, a tool-call budget, a report gate, and finding validation.

## Two decisions I would defend

Manager-and-subagent over a single agent. A reasonable engineer might run one agent over all 50 tools, and for six scenarios that works. I chose the split because it scales: context isolation keeps the manager coherent as services, telemetry, and parallel candidates grow, which is exactly where a single context degrades. I also narrow to a few candidates rather than spawning one subagent per service, avoiding the opposite failure of over-orchestration.

Evaluating tools with Anthropic's cookbook over writing them by intuition. The easy path is to write tool descriptions, polish them, and ship. Instead I handed the agent only its tools and realistic tasks, watched what it selected, scored against sealed truth, and refined from the transcripts, holding two scenarios out so the gains generalized. You cannot predict tool ergonomics by inspection; the evaluation is what turned 50 tools into a set the agent actually navigates, and it surfaced both a real gap (a missing latency localizer) and redundancies to cut.

## What I cut

A broader, fault-heavy benchmark. I tried to record heavier, multi-fault incidents for a more comprehensive evaluation dataset, but hit real limits in the demo: its built-in faults are narrow (one product, one operation) and they interact (a full payment failure aborts checkout before the cart fault can surface), the checkout path starves under heavy browse load, and the telemetry pipeline destabilizes under sustained heavy load on a single host. So I kept that recorder exploratory and shipped six clean, reliable incidents instead. I also cut negative controls (a healthy "no incident" case), a record-and-replay mode for the demo (it runs live), and kept the runbook knowledge base deliberately thin.

## What more time would address

Two things. First, a broader and harder benchmark: more fault-heavy and compound, multi-cause incidents plus negative controls, which needs a chaos-injection layer to produce strong, controllable faults rather than leaning on the demo's built-in flags. Second, a macro-evaluation layer. The current evaluation is micro: per-run outcome against ground truth, and per-tool selection. The OpenAI macro-evals approach adds the layer above it, normalizing many runs into trace documents and mining recurring failure patterns across them (clustering, impact scoring by prevalence and severity) to surface systemic weaknesses rather than single-run correctness. Once there are many runs, that is how you learn what repeatedly goes wrong, and I would build it on top of the existing harness.

---

## Appendix: the 50 tools, across ten namespaces

| Tool                         | Purpose                                                          |
| ---------------------------- | --------------------------------------------------------------- |
| traces_find                  | Search spans by service, kind, status, callee, operation, time  |
| traces_get_trace             | Fetch all spans of one trace, ordered                           |
| traces_build_topology        | Build the service dependency graph from spans                   |
| traces_first_error_time      | Find onset: the time of the first error span                    |
| traces_error_origin          | Locate the failure origin; classify service vs edge fault       |
| traces_latency_origin        | Locate the service whose own work is slowest                    |
| traces_slowest_operations    | Rank operations by p95 to find the latency hotspot              |
| traces_error_summary         | Per-service error-span counts as fault observations             |
| traces_span_tree             | Render one trace as a call tree                                  |
| traces_latency_breakdown     | Rank a trace's spans by duration                                |
| traces_compare_pre_post      | Pair a healthy pre-onset trace with a failing post-onset one    |
| traces_service_summary       | One service's count, errors, error rate, p95 from spans         |
| metrics_series               | Fetch one metric series for a service                           |
| metrics_list_series          | List available (service, metric, unit) series                   |
| metrics_compare_baseline     | Compare a metric's mean across two windows                      |
| metrics_detect_shift         | Time of the largest level shift in a series                     |
| metrics_top_movers           | Rank services by a metric's change across onset                 |
| metrics_resource_saturation  | Services with CPU cores above a threshold                       |
| metrics_error_budget         | Error rate vs SLO; report budget burn                           |
| metrics_summary_all          | Every service's error rate, p95, and CPU at once                |
| logs_search                  | Search logs by service, severity, substring, or time           |
| logs_for_trace               | The log lines attached to one trace                             |
| logs_error_clusters          | Cluster error logs by message template                          |
| logs_level_histogram         | Logs per severity in time buckets (when errors surged)          |
| logs_first_error             | Each service's earliest error-level log                         |
| changes_search               | List recent changes, filterable by service or time             |
| changes_lookback             | Changes strictly before onset, nearest first                    |
| changes_rank_culprit         | Rank pre-onset changes by culprit likelihood                    |
| correlate_signals            | Align a service's error/latency/CPU before vs after onset       |
| correlate_attribute_fault    | Decide node vs edge from fault observations (span-kind rule)    |
| correlate_metric_to_traces   | Drill from a metric anomaly down to exemplar traces             |
| correlate_onset_consensus    | Corroborate the onset across traces and logs                    |
| correlate_timeline           | Order changes against onset; flag which precede it              |
| topology_dependencies        | A service's direct callers and callees                          |
| topology_blast_radius        | A service's transitive blast radius                             |
| topology_critical_path       | Path from the user entry point to a target service              |
| topology_locate_origin       | Follow error edges to where propagation terminates              |
| topology_compare             | Edges that began erroring only after onset                      |
| hypothesis_gather_evidence   | Evidence for and against one root-cause hypothesis              |
| hypothesis_rule_out          | Eliminate an uninvolved service or a post-onset change          |
| investigate_service          | Spawn an isolated subagent to deep-dive one service             |
| investigate_parallel         | Fan out subagents over several candidate services in parallel   |
| investigate_change           | Spawn a subagent to assess whether one change is the culprit    |
| report_build_evidence        | Compile the evidence package for the report                     |
| report_self_check            | Validate a draft report before submitting                       |
| report_root_cause            | Submit the final incident report (terminal)                     |
| report_finding               | Submit an investigator's ServiceFinding (terminal)              |
| report_change_verdict        | Submit a change-investigator's culprit verdict (terminal)       |
| runbook_search               | Search the runbook knowledge base by symptom                    |
| runbook_get                  | Fetch a runbook's diagnostic steps by id                        |
