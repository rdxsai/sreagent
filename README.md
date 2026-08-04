# Sentinel

An autonomous SRE incident-response agent. Given a firing alert, it investigates recorded microservice telemetry, traces the symptom to its root cause, and emits a structured incident report. It resolves the six recorded incidents and ships with 181 unit and integration tests.

Sentinel runs against a sealed, replayable telemetry environment built from the OpenTelemetry Demo: a known fault is injected into a service, the real traces, metrics, and logs it produces are captured, and the result is written as a fixture the agent investigates. The agent reads only the public telemetry; the ground truth is held separately and used only to grade.

## What it does

- A manager agent triages an incident (it builds the dependency graph, finds onset, and localizes the fault), then delegates to isolated investigator subagents that each deep-dive one candidate service and return a typed finding, which the manager reconciles into a root-cause report.
- 50 tools across ten namespaces (traces, metrics, logs, changes, correlate, topology, hypothesis, investigate, report, runbook), exposed through a decorator and schema registry with model-driven selection and typed, composable Pydantic inputs and outputs.
- Production scaffolding: retries with backoff, rate limiting, typed errors, structured logging, and a deterministic hooks layer (a leak-safety seal, a tool-call budget, a report gate, finding validation).
- An evaluation harness that grades each run against sealed ground truth and reports pass@k across repeats.
- A live web demo that streams the agent's reasoning, tool calls, and nested subagent investigations token by token.

## Quickstart

Prerequisites: Python 3.11+, Node 18+ (for the demo frontend), and an Anthropic API key.

Install:

```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
echo "ANTHROPIC_API_KEY=sk-..." > .env
```

Run the tests:

```
scripts/test                      # or: python -m pytest tests/
```

Run the evaluation (the agent investigates the recorded incidents and is graded against ground truth):

```
scripts/eval                      # all six incidents
scripts/eval payment_failure_001  # one incident
SENTINEL_EVAL_REPEATS=3 scripts/eval   # reliability: pass@k over repeats
```

Run the live demo:

```
cd frontend && npm install && npm run build && cd ..
scripts/server                    # serves the app + API at http://localhost:8000
```

Open http://localhost:8000, pick an incident, and watch the agent investigate live. For frontend development with hot reload, run `scripts/server` and, in a second terminal, `npm run dev` inside `frontend/` (Vite proxies the API).

## Live lab demo

The Live lab tab runs a whole incident on a real system, end to end: boot the
dockerized Sock Shop (13 services, telemetry to New Relic), inject a CPU fault
into a chosen service, watch the charts degrade, watch the gpt-oss code-mode
agent localize the root cause live (plan, parallel workers, the Python they
write, their verdicts), then approve a gated `docker restart` from the page and
watch recovery confirmed from live telemetry. Every run is journaled under
`runs/dashboard/` and can be replayed through the identical UI (demo insurance
for flaky networks).

Prerequisites: Docker running, and `.env` with `NEW_RELIC_LICENSE_KEY`,
`NEW_RELIC_USER_KEY`, `NEW_RELIC_ACCOUNT_ID`, `OPEN_ROUTER_API_KEY`.

```
docker compose -f labs/sockshop/docker-compose.yml up -d   # or press Boot lab in the UI
set -a; source .env; set +a
scripts/server
```

A real run takes 12 to 18 minutes with the proven protocol (3m clean baseline,
4m soak under fault, then the investigation) and costs a few cents. To rehearse
the UI without docker, New Relic, or an LLM:

```
SENTINEL_LIVELAB_FAKE=1 scripts/server    # scripted run at 20x speed
```

## How it works

The recording pipeline (`labs/otel`) runs the OpenTelemetry Demo at a pinned commit under self-driven load, injects one feature flag at a known onset, captures telemetry from Prometheus (metrics), Jaeger (traces), and OpenSearch (logs), runs an alerting layer that fires a single UserFacingDegradation alert, and writes a sealed fixture: `public/` for the agent, `eval_only/` for the grader. The six recorded incidents live in `fixtures/`.

The agent runtime (`sentinel/agent`) is plain async Python: a shared hooked loop drives both the manager and the investigator subagents. Running the agent and the demo needs only the committed public fixtures; the grader additionally reads the eval-only ground truth, which is kept out of version control by the seal; only re-recording new fixtures needs the live demo.

## Project layout

```
sentinel/agent/      manager + investigator loop, hooks, events, runner
sentinel/tools/      the 50 tools, the fixture store, typed I/O models
sentinel/registry/   the decorator and schema tool registry
sentinel/api/        FastAPI app: the /demo SSE stream and the alert webhook
sentinel/fixtures/   fixture schemas and replay
sentinel_tool_eval/  the evaluation harness and grader
labs/otel/           the OpenTelemetry recording pipeline
fixtures/            the six sealed incident fixtures (public telemetry)
frontend/            the React and Vite demo UI
tests/               unit and integration tests
```

## Tool modes

Sentinel exposes its SRE tools two ways, switchable with `SENTINEL_TOOL_MODE`:

- `native` (default): a two-tier manager/investigator agent picks among the typed
  tools with function calling.
- `code`: a single agent writes Python against a generated client, run in an
  isolated Docker sandbox (`--network none`); proxy calls cross back to the host,
  which runs the hooks and the real tools. Set `SENTINEL_CODE_BACKEND=local` to run
  the sandbox as a subprocess instead of Docker (tests and Docker-less dev).

The eval compares the two arms: `SENTINEL_EVAL_TOOL_MODE=native|code`.

## Design

See [MEMO.md](MEMO.md) for what was built, what was cut, what more time would address, and the design decisions and the alternatives they were chosen over.
