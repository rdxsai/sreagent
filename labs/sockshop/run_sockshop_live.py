"""Whole-lab LIVE investigation on the dockerized Sock Shop over New Relic.

Sock Shop is not OTel-native and has no flagd, so we inject the fault directly:
peg CPU on one service with `yes` loops. Then run the same gpt-oss code-mode agent
(run_rca) that the OTel-Demo runner uses, but with system="sock_shop" so the ranking
falls back to the maintained static topology (spans are sparse here). The agent is
given only a generic symptom and must localize the root cause among all 13 services.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.chdir("/Users/rudradesai/notes/SREagent-hackathon")
sys.path.insert(0, ".")
for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
os.environ["SENTINEL_LLM_TIMEOUT_S"] = "90"

TARGET = "shipping"           # leaf on the checkout path (orders -> shipping -> queue-master);
                              # never faulted, so its docker_stats accounting is clean
BASELINE, SOAK, LAG = 180, 240, 60
HOGS = 3                       # busy-loops (~3 host cores)
OUT = Path("runs/oss_live")
OUT.mkdir(parents=True, exist_ok=True)


def cpu_pct(store_svc: str) -> float:
    from sentinel.newrelic.client import NerdGraphClient
    c = NerdGraphClient.from_env()
    q = (f"SELECT average(container.cpu.utilization)*100 AS p FROM Metric "
         f"WHERE container.name='{store_svc}' SINCE 90 seconds ago")
    r = c.nrql(q)
    return float((r[0].get("p") or 0.0)) if r else 0.0


def inject() -> None:
    from labs.sockshop.faults import inject_cpu
    inject_cpu(TARGET, hogs=HOGS)


def cleanup() -> None:
    from labs.sockshop.faults import clear_cpu
    clear_cpu(TARGET)


def main() -> int:
    from sentinel.fixtures.schemas import DerivedAlert
    from sentinel.newrelic.client import NerdGraphClient
    from sentinel.newrelic.store import NewRelicStore
    from sentinel.oss.rca import run_rca

    symptom = ("Customers report the Sock Shop storefront is sluggish: browsing the "
               "product catalogue and loading pages feels slow. Overall the system is degraded.")

    # Forward baseline: sweep any stray hogs, then record a clean healthy period BEFORE
    # injecting, so the pre-onset window is guaranteed uncontaminated (reachback risked
    # picking up a prior run's residual CPU).
    cleanup()
    t0_ms = int(time.time() * 1000)
    print(f"baseline cpu[{TARGET}] = {cpu_pct(TARGET):.1f}% of host; "
          f"establishing {BASELINE}s clean baseline ...", flush=True)
    time.sleep(BASELINE)

    print(f"injecting {HOGS} CPU hogs into {TARGET} at {time.strftime('%H:%M:%S')}", flush=True)
    inject()

    time.sleep(90)
    print(f"  t+90s cpu[{TARGET}] = {cpu_pct(TARGET):.1f}% of host (should be climbing)", flush=True)
    time.sleep(SOAK - 90)

    window_start_ms = t0_ms
    window_end_ms = int(time.time() * 1000) - LAG * 1000
    onset = BASELINE
    window_s = (window_end_ms - window_start_ms) // 1000
    print(f"post-soak cpu[{TARGET}] = {cpu_pct(TARGET):.1f}% of host", flush=True)

    alert = DerivedAlert(alertname="ServiceDegraded", severity="warning", starts_at_second=onset,
                         labels={"tier": "user_facing"}, annotations={"summary": symptom},
                         value=1.0, expr="live", fingerprint="live-sockshop-catalogue-cpu")
    store = NewRelicStore(NerdGraphClient.from_env(), window_start_ms=window_start_ms,
                          window_end_ms=window_end_ms, alerts=[alert])

    incident = (
        f"Symptom: {symptom}\n"
        f"Recording window: seconds 0 to {window_s}; the incident onset is around second {onset}.\n"
        "This is a LIVE microservices system (many services). Investigate the telemetry and "
        "determine which single service is the root cause and the failure type."
    )
    run_id = f"live-sockshop-{TARGET}-cpu-{int(time.time())}"
    print(f"investigating live Sock Shop over New Relic (window {window_s}s, onset {onset}s) ...", flush=True)
    try:
        # prefer_trace=False: Sock Shop's static names match the live container/service
        # names, and spans are sparse, so use the full 13-node maintained topology as the
        # structure (trace is still probed so traces_present stays true for the workers).
        result = run_rca(store, incident=incident, out_dir=str(OUT), model=None, run_id=run_id,
                         system="sock_shop", onset=onset, backend="local",
                         worker_concurrency=2, prefer_trace=False)
    finally:
        cleanup()
        print("cleaned up CPU hogs", flush=True)

    core = {"root_cause_service": result.root_cause_service, "ranked_services": result.ranked_services,
            "synthesis": result.synthesis, "graph_source": result.graph.get("source"),
            "graph_edges": len(result.graph.get("edges", [])), "usage": result.usage}
    handoff = {"run_id": run_id, "symptom": symptom, "live": True, "system": "sock_shop",
               "target_truth": TARGET, "fault_truth": "resource", **core}
    out_path = OUT / f"{run_id}.result.json"
    out_path.write_text(json.dumps(handoff, indent=2))

    got = result.root_cause_service
    ft = (result.synthesis or {}).get("fault_type")
    verdict = "HIT" if got == TARGET else "MISS"
    print("\n==== GRADE ====", flush=True)
    print(f"  want={TARGET} (resource)   got={got} ({ft})   -> {verdict}", flush=True)
    print(f"  ranked: {result.ranked_services[:5]}", flush=True)
    print(f"  graph: source={core['graph_source']} edges={core['graph_edges']}", flush=True)
    print(f"  tokens in/out: {result.usage.get('input',0)}/{result.usage.get('output',0)}", flush=True)
    print(f"  result: {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
