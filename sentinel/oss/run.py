"""Entry point: run the code-mode RCA agent over one live LGTM scenario.

Prereqs: the scenario's LGTM stack is up (labs/lgtm) and OPENROUTER_API_KEY is
exported. The agent (gpt-oss-120b by default) never sees raw telemetry in the
manager; workers investigate in the hardened docker sandbox.

  python -m sentinel.oss.run --smoke                       # one tool-use round-trip
  python -m sentinel.oss.run rcaeval/library/ob_recommendationservice_cpu_1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from sentinel.lgtm import LgtmStore
from sentinel.oss.rca import run_rca
from sentinel.providers import resolve, smoke_test

_REGISTRY = Path("rcaeval/library/registry.json")
_COMPOSE = "labs/lgtm/docker-compose.yml"


def _bring_up(case_id: str) -> tuple[Path, dict]:
    """Start a registered scenario's stack from its persisted volumes; return its dir + ports."""
    reg = json.loads(_REGISTRY.read_text())
    if case_id not in reg:
        raise SystemExit(f"unknown scenario {case_id!r}; registered: {sorted(reg)}")
    v = reg[case_id]
    p = v["ports"]
    env = {**os.environ, "COMPOSE_PROJECT_NAME": v["project"], "PROM_PORT": str(p["prom"]),
           "LOKI_PORT": str(p["loki"]), "TEMPO_PORT": str(p["tempo"]), "OTLP_PORT": str(p["otlp"])}
    subprocess.run(["docker", "compose", "-f", _COMPOSE, "start"], env=env, check=False,
                   capture_output=True, text=True)
    c = httpx.Client(timeout=5)
    urls = {"prom": f"http://localhost:{p['prom']}/-/ready",
            "tempo": f"http://localhost:{p['tempo']}/ready", "loki": f"http://localhost:{p['loki']}/ready"}
    ok: set = set()
    deadline = time.time() + 120
    while time.time() < deadline and len(ok) < 3:
        for k, u in urls.items():
            if k not in ok:
                try:
                    if c.get(u).status_code == 200:
                        ok.add(k)
                except Exception:
                    pass
        if len(ok) < 3:
            time.sleep(3)
    if len(ok) < 3:
        raise SystemExit(f"scenario {case_id} stores not ready: {ok}")
    # /ready is not enough for Tempo after a restart: block search needs a blocklist-poll
    # cycle. For scenarios that have traces, wait until search actually returns spans, else
    # the store hydrates with 0 traces and the trace tools are blind.
    if v.get("fed", {}).get("traces", 0) > 0:
        wf = json.loads((Path("rcaeval/library") / case_id / "window.json").read_text())
        s0 = wf["window_start_ms"] // 1000 - 600
        deadline = time.time() + 150
        while time.time() < deadline:
            try:
                tr = c.get(f"http://localhost:{p['tempo']}/api/search",
                           params={"q": '{ span.span.kind =~ ".+" }', "start": s0,
                                   "end": int(time.time()), "limit": 5}).json()
                if tr.get("traces"):
                    break
            except Exception:
                pass
            time.sleep(5)
    return Path("rcaeval/library") / case_id, p


def _incident(scenario_dir: Path) -> tuple[str, dict]:
    window = json.loads((scenario_dir / "window.json").read_text())
    manifest = json.loads((scenario_dir / "public" / "manifest.json").read_text())
    symptom = manifest.get("symptom", "An incident is in progress; determine the root cause.")
    span = window["window_span_s"]
    onset = window["onset_second"]
    incident = (
        f"Symptom: {symptom}\n"
        f"Recording window: seconds 0 to {span}; the fault onset is around second {onset}.\n"
        "Investigate the recorded telemetry (metrics, traces, logs) and determine which service "
        "is the root cause and the failure type."
    )
    return incident, window


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Code-mode RCA over one live LGTM scenario.")
    ap.add_argument("scenario_dir", nargs="?", default="rcaeval/library/ob_recommendationservice_cpu_1")
    ap.add_argument("--scenario", default=None, help="registered case_id: brings its stack up and uses its ports")
    ap.add_argument("--model", default=None, help="preset name (default env SENTINEL_MODEL or gpt-oss-120b)")
    ap.add_argument("--backend", default="docker", help="sandbox backend: docker (isolated) or local")
    ap.add_argument("--out", default="runs/oss")
    ap.add_argument("--concurrency", type=int, default=3, help="parallel workers (mind OpenRouter rate limits)")
    ap.add_argument("--prometheus", default="http://localhost:9090")
    ap.add_argument("--loki", default="http://localhost:3100")
    ap.add_argument("--tempo", default="http://localhost:3200")
    ap.add_argument("--smoke", action="store_true", help="just run the provider tool-use smoke test")
    args = ap.parse_args(argv)

    preset = resolve(args.model)
    if args.smoke:
        print(json.dumps(smoke_test(args.model), indent=2))
        return 0

    prom, loki, tempo = args.prometheus, args.loki, args.tempo
    if args.scenario:
        scenario_dir, ports = _bring_up(args.scenario)
        prom = f"http://localhost:{ports['prom']}"
        loki = f"http://localhost:{ports['loki']}"
        tempo = f"http://localhost:{ports['tempo']}"
    else:
        scenario_dir = Path(args.scenario_dir)
    incident, window = _incident(scenario_dir)
    store = LgtmStore(
        prometheus_url=prom, loki_url=loki, tempo_url=tempo,
        window_start_ms=window["window_start_ms"], window_end_ms=window["window_end_ms"],
        onset_second=window["onset_second"], now_s=int(time.time()),   # Fix 4: fixed per run
    )
    # Fix 6: eval-side sanity check (beside the agent, never in its path): the metric job
    # labels must overlap the truth's accepted services, else metric_series is silently empty.
    truth_path = scenario_dir / "eval_only" / "truth.json"
    if truth_path.exists():
        truth = json.loads(truth_path.read_text())
        accepted = set(truth.get("accepted_services") or [truth.get("root_cause", {}).get("service")])
        if not (set(store.list_services()) & accepted):
            print(f"WARNING: no store service matches truth {accepted}; metric_series will be empty",
                  file=sys.stderr)

    case_id = args.scenario or scenario_dir.name
    print(f"model={preset.model}  scenario={scenario_dir.name}  backend={args.backend}", file=sys.stderr)
    result = run_rca(store, incident=incident, out_dir=args.out, model=args.model,
                     onset=window['onset_second'], run_id=case_id, backend=args.backend,
                     worker_concurrency=args.concurrency)

    print(json.dumps({
        "root_cause_service": result.root_cause_service,
        "ranked_services": result.ranked_services,
        "synthesis": result.synthesis,
        "n_verdicts": len(result.verdicts),
        "graph_edges": len(result.graph.get("edges", [])),
        "graph_source": result.graph.get("source"),
        "trace": result.trace_path,
        "usage": result.usage,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
