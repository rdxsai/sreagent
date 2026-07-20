# Action half: propose, approve, execute

Status: ready to build
Audience: the building agent (assume no prior context beyond this file)
Scope: add the gated action half to Sentinel. The investigation half (code-mode RCA over RCAEval scenarios, `sentinel/oss/*`) is complete and scored; this plan adds the second half: after a run produces a report, the system proposes next actions, posts a brief to Slack, and executes a remediation only after explicit human approval. Version 1 (Option A) runs on the existing replay path with communication actions real and remediation as an audited dry-run. Version 2 (Option B) extends the same machinery to a live Sock Shop lab where an approved remediation changes real state and the telemetry recovers.

Shape borrowed from the DevOps-agent capstone guide (aiengineeringfromscratch, capstone 06): read-only by default, destructive tools on a separate surface behind an approval the agent cannot mint, a Slack card with at most one remediation button, and an append-only audit of considered vs executed with a near-miss metric. We take the shape and the safety posture, not the stack (no LangGraph, no Neo4j, no ArgoCD, no K8s requirement).

Do not touch: the sandbox hardening (`sentinel/sandbox/executor.py`), the seal and budget hooks, the scored eval path's determinism, or the two frozen topology artifacts.

## Design seams (read this first)

The codebase already follows a pattern this plan repeats: a production capability is designed as an interface, and the eval runs a faithful stand-in behind it.

| seam                | production backend (designed)         | eval stand-in (built)                  |
| ------------------- | ------------------------------------- | -------------------------------------- |
| topology provider   | maintained graph (Kiali, Datadog, mesh) | frozen `rcaeval/topology/*.json`     |
| remediation executor | LiveExecutor against a live system   | DryRunExecutor on the replay path      |

The frozen graphs are hand-made for two systems (Online Boutique from traces, Sock Shop from published architecture) and are correct for the benchmark; a real deployment would wire a real provider. The action half mirrors this exactly: the gate, the Slack surface, the approval store, and the audit are identical in Option A and Option B; only the executor backend swaps. Build everything against the interface, prove it with the dry-run backend, and Option B becomes a backend swap plus a lab.

## Current state (verified by reading the code)

| piece                          | where                                   | state |
| ------------------------------ | --------------------------------------- | ----- |
| ranked root-cause output       | `oss/schemas.py` `Synthesis.ranked_services` | have |
| worker evidence lines          | `WorkerVerdict.evidence: list[str]`     | have, prose only |
| hooks with allow/deny/modify   | `agent/hooks.py` `HookRunner`           | have  |
| schema-driven registry         | `registry/__init__.py`                  | have, no side-effect flag |
| append-only JSONL journal      | `oss/trace.py` `TraceLogger`            | have, pattern to mirror |
| FastAPI app with a webhook     | `api/app.py` `/alert`                   | have, no signature verification |
| run artifacts                  | `runs/oss/<run_id>.jsonl` plus `RcaResult` | have |
| Slack, approvals, action tools, audit, executor | anywhere                | none  |

## The two-path rule (non-negotiable)

The scored RCAEval replay stays read-only, deterministic, and action-free. The action half is a separate path that consumes a completed run's report and executes after the eval, never inside it. Even on the same scenario, "score the RCA" and "demo the gated action" are two different runs. Enforce this structurally: the `sentinel/oss/` and `sentinel/agent/` eval paths must not import the new `sentinel/actions/` package, and a test asserts it (walk the import graph or grep imports in CI).

## Action taxonomy

Actions split by whether they change the incident system:

| class   | examples                                              | gate                        | runnable on replay |
| ------- | ----------------------------------------------------- | --------------------------- | ------------------ |
| notify  | post Slack brief, open ticket, page oncall, annotate  | none, but always audited    | yes, for real      |
| mutate  | restart service, scale, remove impairment, revert     | explicit approval, single use | dry-run only     |

Posting the brief is how the system asks for approval, so it cannot itself require approval. It is still journaled. Mutate actions are proposed with abstract parameters (action type plus target service); the executor maps them to concrete operations at execute time. The model never sees or emits the concrete command, and the agent process never holds the credential that runs it.

## Phase 0: confirm before building

Record answers in `actionplan.notes.md`, then adjust.

1. Slack workspace: is a workspace with app-creation rights available (bot token with `chat:write`, signing secret, interactivity URL)? Slack interactivity needs a public HTTPS callback; for local dev that means a tunnel (cloudflared or ngrok) or skipping Slack buttons in favor of the web-approve fallback (section 7). If no workspace, build the web fallback first; the gate is identical and Slack becomes a later surface.
2. Handoff artifact: confirm the exact fields available from a completed run to build the brief from (`RcaResult`: `root_cause_service`, `synthesis` with `ranked_services`, `fault_type`, `justification`, `verdicts` with `observed_signatures` and `evidence`, `graph`, `trace_path`, `usage`). Define `runs/oss/<run_id>.result.json` as the persisted handoff if it is not already written to disk; the action CLI takes this file, not live objects.
3. Env and secrets: `.env` currently carries provider keys. Action half adds `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_CHANNEL_ID`, `ACTION_APPROVAL_TTL_S`. All read from env, never hardcoded (repo rule). The Slack inbound endpoint must fail closed: if `SLACK_SIGNING_SECRET` is unset, the route refuses requests rather than skipping verification.
4. Dependencies: prefer plain `httpx` for Slack calls (already a dependency); do not add `slack_sdk` unless Block Kit assembly becomes painful.
5. Sandbox exclusion point: confirm where `sentinel/sandbox/client_gen.py` selects tools for the worker SDK (`code_tool_specs()` in `catalog.py` filters terminals today). Mutate tools must be excluded there.

## Component 1: side-effect classification in the registry

Problem. `ToolSpec` cannot express that a tool writes. Every gate downstream needs this bit.

Change. Add `effect: Literal["read", "notify", "mutate"] = "read"` to `ToolSpec` and a matching decorator parameter: `@tool(namespace="action", effect="mutate")`. Rules enforced in code, not convention:

- `code_tool_specs()` (worker sandbox SDK) excludes any spec with `effect != "read"`. The sandbox stays network-none and read-only; action tools never enter it.
- The manager catalog (`oss/catalog.py`) is built from the same read-only specs, so the investigation half is untouched.
- Existing 52 tools default to `read`; no call-site changes.

Files. `sentinel/registry/__init__.py`, `sentinel/sandbox/client_gen.py` (or the filter in `oss/catalog.py`, wherever the spec selection lives), one test.

Verification. A registered `effect="mutate"` tool does not appear in `sdk_for` output or the manager catalog; a `read` tool still does.

## Component 2: action journal and approval store (one artifact)

Problem. Approvals need persistence across the two-phase flow, and the guide's audit requirement is considered vs executed. These are the same data.

Change. One append-only JSONL journal per action run, event-sourced; current state is a fold over events. This mirrors `TraceLogger` deliberately. No separate database.

Event kinds, in lifecycle order:

| event            | carries                                                        |
| ---------------- | -------------------------------------------------------------- |
| proposed         | full `SuggestedAction`, including `params_hash`                |
| posted           | surface (slack or web), channel, message ts or url             |
| approved         | approver identity, surface, at                                  |
| denied           | approver identity, reason                                       |
| expired          | at (TTL sweep)                                                  |
| execute_started  | executor backend (dry_run or live), the concrete op preview     |
| execute_result   | ok or error, before/after observation, duration                 |
| near_miss        | what was attempted, by whom (model or code), why it was blocked |

Schemas (new `sentinel/actions/models.py`):

```python
class SuggestedAction(BaseModel):
    id: str                                   # uuid
    kind: Literal["restart", "scale", "remove_impairment", "revert_change",
                  "open_ticket", "page_oncall"]
    effect: Literal["notify", "mutate"]
    target_service: str
    params: dict[str, str | int]              # abstract only, e.g. {"replicas": 2}
    description: str                          # human sentence for the card
    risk: Literal["low", "medium", "high"]
    reversible: bool
    preview: str                              # the exact concrete op the executor would run
    citations: list[str]                      # evidence lines from the verdicts backing this
    params_hash: str                          # sha256 over (kind, target_service, sorted params)

class ApprovalState(BaseModel):
    action: SuggestedAction
    status: Literal["proposed", "posted", "approved", "denied", "expired",
                    "executing", "done", "failed"]
    approver: str | None = None
    expires_at: float | None = None
```

`params_hash` is the binding: an approval is valid only for the exact action content it was granted for. Verify the hash again at execute time; a mismatch is a `near_miss`, not an error to retry.

Files. New `sentinel/actions/models.py`, `sentinel/actions/journal.py` (append plus fold), tests for the fold (every event sequence reaches exactly one terminal state; out-of-order approve-after-expire is rejected).

## Component 3: remediation catalog

Problem. The mapping from a diagnosed fault to candidate actions must be deterministic and reviewable, not model-improvised.

Change. A static table keyed by the synthesis `fault_type` (and signature), producing ranked `SuggestedAction` drafts for the top-ranked service. Reversible-first ordering. The model does not choose the action set; it produced the diagnosis, the catalog maps it. Starter table:

| fault_type / signature | ranked candidates                                        |
| ---------------------- | -------------------------------------------------------- |
| cpu, mem (resource)    | restart(service), scale(service, +1)                     |
| delay, loss (latency plus edge) | remove_impairment(service), restart(service)    |
| disk (resource)        | restart(service), page_oncall                            |
| socket (error/latency) | restart(service), remove_impairment(service)             |
| error                  | revert_change(service), restart(service)                 |
| unknown / low confidence | open_ticket, page_oncall (notify only, no mutate)      |

Rules: exactly one mutate action is elected primary per brief (the guide caps the card at one remediation button); the rest appear as text alternatives. If synthesis confidence is low or verdicts conflict, propose notify-class only and say so in the brief. `preview` is filled by asking the executor to render the concrete op without running it, so the human approves exactly what will run.

Files. New `sentinel/actions/catalog.py`, tests (each fault_type yields a non-empty ranked list; unknown yields notify-only).

## Component 4: brief builder

Problem. The Slack card needs a compact, explainable brief with citations, built from the handoff artifact.

Change. `build_brief(result: RcaResult-shape) -> Brief` producing: symptom line, top-3 `ranked_services` each with its strongest verdict evidence lines (these are the telemetry citations; workers already record query-and-value prose in `evidence`), the observed signature vector, graph source note (static or trace), the elected primary action with risk, reversibility, and `preview`, and the alternatives as text. Deterministic, no LLM call; the investigation already produced the content. Optional later: a `citations` structured field on `WorkerVerdict` (source, query, observation); do not block on it, the prose lines suffice for v1.

Files. New `sentinel/actions/brief.py`, a render test against a saved `result.json` fixture.

## Component 5: the gate (ApprovalGuard) and executor isolation

Problem. Enforcement must be code, not prompt. Two layers, per the guide's separate-server idea:

Primary layer: the two-phase flow itself. The proposing run terminates after posting; nothing in the model's process can execute. Execution happens in the server process, triggered only by an approval event, via the executor. The model is not in the loop at execute time, and the executor (not the agent) holds any credential (docker socket access, later a token). This is the structural gate.

Defense in depth: `ApprovalGuard(Hook)` in `agent/hooks.py` style: `pre_tool_use` denies any `effect="mutate"` tool call unless the run context carries an approval token whose `params_hash` matches the call. Registered in any loop that could ever see a mutate tool. Today no loop has them (component 1 excludes them from the sandbox and catalog), so the guard's deny branch plus its `near_miss` journal entry is the tripwire proving the property holds. An attempted mutate call without approval is journaled as `near_miss`, never silently dropped.

Executor interface (new `sentinel/actions/executor.py`):

```python
class Executor(Protocol):
    def render(self, action: SuggestedAction) -> str      # the concrete op, for preview
    def execute(self, action: SuggestedAction) -> Outcome # runs it; journals started/result

class DryRunExecutor:   # Option A: logs the op it would run; simulated ok outcome
class LiveExecutor:     # Option B: docker compose ops against the live lab
```

Execute is idempotent per approval: an approval is consumed on first execute (journal `execute_started` marks it), duplicate triggers (Slack retries, double clicks) find it consumed and no-op with a journaled duplicate note.

Verification (the core security tests, write these before any Slack code):
- mutate without approval: denied, `near_miss` journaled.
- approve action A, attempt execute with altered params: hash mismatch, denied, `near_miss`.
- approve, execute, re-trigger: second execute no-ops.
- expiry: approve after TTL: rejected.

## Component 6: Slack surface

Outbound (`sentinel/actions/slack.py`). `chat.postMessage` via httpx with Block Kit: brief sections plus one Approve and one Deny button carrying the action id, and the alternatives as plain text. Bot token from env. Posting appends `posted` to the journal. On execute completion, post the outcome to the same thread.

Inbound. New route in `api/app.py`: `POST /slack/interact`. Requirements, in order:
1. Verify `X-Slack-Signature`: HMAC-SHA256 of `v0:{timestamp}:{raw_body}` with the signing secret, constant-time compare, reject if the timestamp is older than 5 minutes (replay guard). Use the raw request body bytes, not re-serialized JSON. This is the trust boundary; the existing `/alert` route has no verification because it is a local Alertmanager, the Slack route faces the internet through a tunnel.
2. Ack within 3 seconds (Slack retries otherwise): append the approved or denied event, return 200 immediately, run the executor in the background (the journal's consumed-approval idempotency makes Slack's retries safe).
3. Record approver identity from the payload into the `approved` event.

Web fallback (build it regardless; it is the dev surface). `GET /actions/pending` lists briefs awaiting approval; `POST /actions/{id}/approve` and `/deny` append the same events. Same journal, same binding, same executor path; Slack is just a nicer front end on the identical gate. Protect the fallback with a simple shared-secret header from env in dev.

## Component 7: the two-phase flow, end to end

```
phase 1 (CLI, after a scored run completes)
  runs/oss/<id>.result.json -> build_brief -> catalog -> elect primary action
    -> journal: proposed -> post to Slack (and/or web pending) -> journal: posted -> exit

phase 2 (server, on human decision)
  /slack/interact or /actions/{id}/approve
    -> verify signature / secret -> journal: approved (who)
    -> executor.render == approved preview? (hash check)
    -> DryRunExecutor.execute -> journal: execute_started, execute_result
    -> post outcome to the Slack thread
  TTL sweep marks stale proposals expired
```

New CLI: `python -m sentinel.actions.run --result runs/oss/<id>.result.json [--surface slack|web|both]`. The server is the existing FastAPI app with the new routes.

## Option A: definition of done

- [ ] Phase 0 answers recorded; `.env.example` updated with the new vars.
- [ ] registry `effect` flag; mutate tools excluded from sandbox SDK and manager catalog; test green.
- [ ] journal plus fold with all eight event kinds; state-machine tests green.
- [ ] the four core security tests (component 5) green before Slack work starts.
- [ ] catalog and brief builder render a correct brief from a real saved `result.json`.
- [ ] Slack outbound posts the brief with buttons; inbound verifies signature, rejects forged and stale payloads (tests with synthetic signatures).
- [ ] web fallback approves end to end without Slack.
- [ ] full Option A demo: one completed RCAEval run -> brief posted -> human approves -> DryRunExecutor journals the exact op it would have run -> outcome posted back.
- [ ] near-miss and considered-vs-executed report generated from the journal (`python -m sentinel.actions.report`).
- [ ] eval isolation test: scored paths do not import `sentinel.actions`; one scored scenario re-run is byte-identical on the RCA side.

## Eval criteria (grade the work against this)

| weight | criterion            | measured how                                                                  |
| :-: | ----------------------- | ----------------------------------------------------------------------------- |
| 25  | safety invariant        | journal proof across all runs and tests: zero mutate executions without a matching prior approval, including an adversarial test where the model is prompted to call a mutate tool directly |
| 15  | approval binding        | params-hash mismatch and single-use tests pass; approval for X can never execute X' |
| 10  | inbound authentication  | forged signature, tampered body, and stale-timestamp payloads all rejected     |
| 15  | audit completeness      | every proposed action reaches exactly one terminal state; considered-vs-executed and near-miss counts reported per run |
| 10  | explainability          | every posted brief carries citations for the top hypotheses and the exact `preview` of the primary action |
| 10  | eval isolation          | import-separation test green; scored RCA output unchanged by the action half   |
| 10  | end-to-end flow         | the Option A demo above works on the first scenario tried, both surfaces       |
| 5   | latency                 | report-to-brief-posted p50 under 2 minutes (RCA time is tracked separately and does not count against this) |

Total 100. The safety rows are pass/fail in spirit: a single unapproved mutate execution anywhere is a failed deliverable regardless of the rest.

## Option B: live Sock Shop lab (build only after Option A is done)

Goal. The same flow, but an approved remediation changes real state and the symptom visibly recovers. Only the executor backend and the environment change; if anything else needs modification, that is a design smell to fix, not accommodate.

1. Lab compose. One isolated compose project (own `COMPOSE_PROJECT_NAME`, network, volumes, brought up on demand like the scenario stores): Sock Shop official docker-compose (about 14 services, roughly 4 GB), its load generator, an OTel collector scraping it, and Prometheus, Loki, Tempo. Reuse the `labs/lgtm` store setup; the difference is the collector scrapes a live app instead of receiving replayed OTLP. Sock Shop is the right system: compose-native, fits 18 GB with the model off-box, one of the three scored systems, and the trace-poor one, so its frozen graph (`rcaeval/topology/sock_shop.json`) doubles as the live lab's topology unchanged.
2. Fault injection. Pumba for network faults (`pumba netem delay|loss` on a target container) and stress (`pumba stress` or `stress-ng` in-container) for cpu and mem. Injection is a small script (`labs/sockshop/inject.py`) that records what it injected and when into a lab-side file the agent never reads (the lab's ground truth for the demo).
3. Live window. `LgtmStore` assumes a fixed recorded window (`window_start_ms`, `window_end_ms`, rebased timestamps). Live mode needs a rolling now-relative window (last 15 minutes) and an onset from the derived alert's `starts_at` rather than a `window.json`. Add a construction path for this (an alternate constructor or a small `LiveWindow` helper), do not fork the store. This is the one real code change Option B needs outside the executor.
4. Trigger. Reuse the existing `/alert` route: load the existing symptom-level rules into the lab's Prometheus with Alertmanager (the `deploy/alertmanager` compose exists already) so the flow starts production-shaped: fault injected, alert fires, webhook starts the investigation, brief lands in Slack.
5. LiveExecutor. Maps abstract actions to concrete ops: `restart -> docker compose restart <svc>`, `scale -> docker compose up -d --scale <svc>=N`, `remove_impairment -> kill the pumba/stress process for <svc>`. Runs host-side in the server process with docker access; the agent process and sandbox never have it. `render` returns the exact command for the preview.
6. Recovery confirmation. After execute, re-query the symptom series (the alert's own expr) at plus 2 to 5 minutes; post before/after values and resolved-or-not to the Slack thread; journal it in `execute_result`.
7. The demo loop. Inject a Pumba delay on a mid-tier service, alert fires, agent investigates live, brief proposes remove_impairment or restart, human approves, LiveExecutor clears it, latency recovers on the dashboard, confirmation posts. That closed loop is the deliverable.

Option B additions to done:
- [ ] lab up and down cleanly as one compose project; does not run concurrently with the replay eval (RAM).
- [ ] live-window store path with a test against the lab.
- [ ] the closed-loop demo recorded once end to end (inject to resolved) with the journal showing proposed, posted, approved, executed, recovered.
- [ ] all Option A security tests re-run green with LiveExecutor substituted.
- [ ] a kill switch: an env flag that forces DryRunExecutor regardless of config, for safe rehearsal.

Option B eval additions: closed loop works (weight it as the new end-to-end row), recovery confirmation posted with before/after, and zero regression on the safety rows with the live backend.

## Notes for the building agent

- Build order matters: components 1, 2, 5 first (the gate, no Slack), then 3, 4, then 6, 7. The security tests exist before the first Slack call is written. If you build the Slack surface first you will be tempted to test the gate through it; the gate must be provable without any network surface.
- Slack local dev needs a public HTTPS URL for interactivity. Use cloudflared or ngrok, or do all development against the web fallback and wire Slack last. Do not let the tunnel requirement stall the gate work; they are independent.
- Slack posts from this system are outward-facing side effects. During development, point at a private test channel; never post to a shared channel from a test run. Keep a `SLACK_DRY_RUN=1` env that logs the payload instead of posting.
- The replay path has sealed fixtures and banned tokens (`hooks.py` `BANNED_TOKENS`). The action path consumes the public report, which is already leak-safe, and briefs naming services are fine (service names are public). Never route anything from `eval_only/` into a brief. In Option B there is no truth to protect; the injection record is demo bookkeeping, keep it out of the agent's context anyway so the live demo stays honest.
- Do not add any mutate tool to the worker sandbox SDK, ever, even for convenience. The sandbox is network-none and read-only; that property is load-bearing for both halves.
- Keep `sentinel/actions/` free of imports from `sentinel/oss/` internals; it consumes the serialized `result.json`, not live objects. This keeps the two-path rule testable and lets the action half work with any future investigation backend (the anthropic path produces `RootCauseReport` too; a small adapter can map it to the same brief input later).
- The existing `/alert` route lacks signature verification by design (local Alertmanager). Do not copy that pattern for `/slack/interact`; the Slack route is internet-facing and must verify. Conversely do not retrofit Slack-style verification onto `/alert` in this work; out of scope.
- Semantic commits, one component per slice, tests green before each commit (repo rules). Suggested slices: `feat(registry): effect classification`, `feat(actions): journal and approval fold`, `feat(actions): gate and executors`, `feat(actions): catalog and brief`, `feat(api): slack interactivity with signature verification`, `feat(actions): two-phase runner`, then Option B slices under `feat(lab): ...`.
- Time-to-hypothesis note: the guide's 5-minute alert-to-brief target is dominated by RCA time here (4 to 11.5 minutes on the integration runs). The action half's own budget is the 2-minute report-to-brief row in the rubric; do not spend effort speeding up RCA inside this work, but do not add avoidable latency either (the brief builder is deterministic, no LLM call).
- If a decision is ambiguous, prefer the choice that keeps the journal the single source of truth. Any state you are tempted to keep elsewhere (a dict of pending approvals, a status column) should be a fold over journal events instead; that is what makes the audit trustworthy and the safety criterion provable.
