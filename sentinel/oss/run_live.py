"""Whole-lab live investigation: inject ONE fault into the running OTel Demo (Online
Boutique), then run the gpt-oss code-mode agent against the LIVE New Relic telemetry over
the whole system. The agent is given only a generic symptom, it localizes the root cause
among all ~18 services itself (topology from the live spans, not a scoped scenario). Persists
runs/oss_live/<id>.result.json for the action half to remediate.

  python -m sentinel.oss.run_live --spec ad_high_cpu_live_001 --baseline 180 --soak 240

Uses NewRelicStore (where the live demo's telemetry flows). The injected flag is recorded in
the result so the LiveExecutor can clear it on approval; truth is never shown to the agent.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from sentinel.fixtures.schemas import DerivedAlert
from sentinel.newrelic.client import NerdGraphClient
from sentinel.newrelic.store import NewRelicStore
from sentinel.oss.rca import run_rca
from sentinel.oss.topology import resolve_topology

_FLAGD = "http://localhost:8081/feature"


_SIG_FAULT = {"resource": "resource saturation", "latency": "latency/slowdown", "error": "errors"}


def _fast_localize(store, onset: int) -> dict:
    """Deterministic whole-system localization: the maintained (live-trace) topology + the
    robust-z anomaly overlay rank the origin first; the top-ranked service is the root cause.
    Proven 14/14 on the frozen benchmark; the original binary-flag overlay failed live
    (ranked the frontend-proxy hub on contention noise), which is what the effect-size
    ranking in resolve_topology fixes. No LLM worker phase."""
    from sentinel.oss.topology import Z_MIN
    g = resolve_topology(store, system="online_boutique", onset=onset, prefer_trace=True)
    root = g.ranked_services[0] if g.ranked_services else None
    eff = g.effects.get(root, {}) if root else {}
    signature = max(eff, key=eff.get) if eff and max(eff.values()) > 0 else None
    sig = {fam: z >= Z_MIN for fam, z in eff.items()}
    # evidence: the origin's stepped metric (with effect size) + the victims that inherited it
    evidence = []
    if root:
        z = eff.get(signature, 0.0) if signature else 0.0
        evidence.append(f"{root} shows a {signature or 'anomalous'} step at onset (z={z:.0f}); "
                        f"ranked #1 of {len(g.ranked_services)} live services by effect size")
        victims = [s for s in g.anomalous if s != root][:3]
        if victims:
            vz = {s: max(g.effects.get(s, {}).values() or [0.0]) for s in victims}
            evidence.append("downstream victims inheriting the symptom: "
                            + ", ".join(f"{s} (z={vz[s]:.0f})" for s in victims))
    return {
        "root_cause_service": root,
        "ranked_services": g.ranked_services[:5],
        "synthesis": {"root_cause_service": root, "ranked_services": g.ranked_services[:5],
                      "fault_type": _SIG_FAULT.get(signature, signature), "justification": "; ".join(evidence)},
        "verdicts": [{"candidate_service": root, "supported": True, "root_cause_service": root,
                      "signature": signature, "observed_signatures": sig, "evidence": evidence}],
        "graph_source": g.source, "graph_edges": len(g.edges),
    }


def _inject(spec_id: str) -> tuple[str, str, str]:
    """Flip the fault flag; return (flag, variant, generic symptom)."""
    from labs.otel.flagd import FlagdClient
    from labs.otel.live_incident import SPECS
    spec = SPECS[spec_id]
    FlagdClient(_FLAGD).set_flag_variant(spec.raw_flag_key, spec.variant)
    return spec.raw_flag_key, spec.variant, spec.symptom


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Whole-lab live gpt-oss investigation over New Relic.")
    ap.add_argument("--spec", required=True, help="live_incident SPEC id (which fault to inject)")
    ap.add_argument("--baseline", type=int, default=180, help="seconds of healthy baseline before onset")
    ap.add_argument("--soak", type=int, default=240, help="seconds to let the fault accumulate in NR")
    ap.add_argument("--lag", type=int, default=60, help="ingest-lag margin; window ends now-lag")
    ap.add_argument("--out", default="runs/oss_live")
    ap.add_argument("--model", default=None)
    ap.add_argument("--backend", default="local", help="sandbox backend (local is lighter for the live demo)")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--no-inject", action="store_true", help="a fault is already active; skip injection")
    ap.add_argument("--fast", action="store_true",
                    help="deterministic localization only (topology+ranking); reliable, no LLM worker phase")
    args = ap.parse_args(argv)

    flag = variant = None
    if args.no_inject:
        from labs.otel.live_incident import SPECS
        symptom = SPECS[args.spec].symptom
        # a fault is already active; look at the recent window and put onset at baseline in.
        window_end_ms = int(time.time() * 1000) - args.lag * 1000
        window_start_ms = window_end_ms - (args.baseline + 180) * 1000
        onset = args.baseline
    else:
        flag, variant, symptom = _inject(args.spec)
        inject_ms = int(time.time() * 1000)
        print(f"injected {flag}={variant}; soaking {args.soak}s for live telemetry ...", flush=True)
        time.sleep(args.soak)
        window_start_ms = inject_ms - args.baseline * 1000
        window_end_ms = int(time.time() * 1000) - args.lag * 1000
        onset = args.baseline
    window_s = (window_end_ms - window_start_ms) // 1000

    alert = DerivedAlert(alertname="ServiceDegraded", severity="warning", starts_at_second=onset,
                         labels={"tier": "user_facing"}, annotations={"summary": symptom},
                         value=1.0, expr="live", fingerprint=f"live-{args.spec}")
    store = NewRelicStore(NerdGraphClient.from_env(), window_start_ms=window_start_ms,
                          window_end_ms=window_end_ms, alerts=[alert])

    incident = (
        f"Symptom: {symptom}\n"
        f"Recording window: seconds 0 to {window_s}; the incident onset is around second {onset}.\n"
        "This is a LIVE microservices system (many services). Investigate the telemetry and "
        "determine which single service is the root cause and the failure type."
    )
    run_id = f"live-{args.spec}-{int(time.time())}"
    print(f"investigating the live system over New Relic (window {window_s}s, onset {onset}s) ...", flush=True)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    if args.fast:
        core = _fast_localize(store, onset)
        usage = {}
    else:
        result = run_rca(store, incident=incident, out_dir=args.out, model=args.model, run_id=run_id,
                         system="online_boutique", onset=onset, backend=args.backend,
                         worker_concurrency=args.concurrency, prefer_trace=True)
        core = {"root_cause_service": result.root_cause_service, "ranked_services": result.ranked_services,
                "synthesis": result.synthesis, "verdicts": result.verdicts,
                "graph_source": result.graph.get("source"), "graph_edges": len(result.graph.get("edges", []))}
        usage = result.usage

    handoff = {"run_id": run_id, "symptom": symptom, "live": True, "injected_flag": flag,
               "usage": usage, **core}
    out_path = Path(args.out) / f"{run_id}.result.json"
    out_path.write_text(json.dumps(handoff, indent=2))
    print(json.dumps({k: handoff[k] for k in ("root_cause_service", "ranked_services",
                      "graph_source", "graph_edges")} | {"result_json": str(out_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
