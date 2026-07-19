"""Feed a converted RCAEval scenario (full-window JSONL) into the live LGTM stack
via OTLP. Rebases relative-second timestamps so the window ends ~2 min in the past
(recent enough for the stores to accept). Writes window.json for the store to query.

Usage: python labs/lgtm/feed.py rcaeval/library/ob_recommendationservice_cpu_1 \
           --collector http://localhost:4318 --onset 720
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

_KIND_INT = {"internal": 1, "server": 2, "client": 3, "producer": 4, "consumer": 5}


def _rows(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _svc_res(service: str) -> dict:
    return {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]}


def _post(client: httpx.Client, url: str, payload: dict) -> None:
    r = client.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=120)
    if r.status_code not in (200, 202, 204):
        raise RuntimeError(f"{url} -> {r.status_code}: {r.text[:300]}")


def feed(scenario_dir: Path, collector: str, onset: int) -> dict:
    public = scenario_dir / "public"
    metrics = _rows(public / "metrics.jsonl")
    logs = _rows(public / "logs.jsonl")
    traces = _rows(public / "traces.jsonl")

    span = max([r["time"] for r in metrics + logs + traces], default=0)
    now_ms = int(time.time() * 1000)
    # Land the window ~20 min in the past so all data is older than Tempo's default
    # 15-min backend-search threshold; then block search covers the whole window
    # with stock config (no version-specific querier tuning). Prometheus/Loki are
    # queried at the window epoch, not "now", so age does not matter for them.
    base_ms = now_ms - (span + 1200) * 1000

    def ns(t: int) -> str:
        return str((base_ms + t * 1000) * 1_000_000)

    client = httpx.Client()

    # METRICS -> Prometheus. Sort by time (in-order per series), group by (svc, metric).
    metrics.sort(key=lambda r: r["time"])
    BATCH = 2000
    m_sent = 0
    for i in range(0, len(metrics), BATCH):
        chunk = metrics[i:i + BATCH]
        by_svc: dict[str, dict[str, list]] = {}
        for r in chunk:
            by_svc.setdefault(r["service"], {}).setdefault(r["metric"], []).append(
                {"asDouble": float(r["value"]), "timeUnixNano": ns(int(r["time"]))}
            )
        rm = [
            {"resource": _svc_res(svc),
             "scopeMetrics": [{"metrics": [
                 {"name": metric, "gauge": {"dataPoints": pts}} for metric, pts in mm.items()]}]}
            for svc, mm in by_svc.items()
        ]
        _post(client, f"{collector}/v1/metrics", {"resourceMetrics": rm})
        m_sent += len(chunk)

    # LOGS -> Loki
    logs.sort(key=lambda r: r["time"])
    l_sent = 0
    for i in range(0, len(logs), BATCH):
        chunk = logs[i:i + BATCH]
        by_svc2: dict[str, list] = {}
        for r in chunk:
            rec = {"timeUnixNano": ns(int(r["time"])),
                   "severityText": r.get("severity", "INFO"),
                   "body": {"stringValue": r.get("message", "")}}
            if r.get("trace_id"):
                rec["attributes"] = [{"key": "trace_id", "value": {"stringValue": r["trace_id"]}}]
            by_svc2.setdefault(r["service"], []).append(rec)
        rl = [{"resource": _svc_res(svc), "scopeLogs": [{"logRecords": recs}]}
              for svc, recs in by_svc2.items()]
        _post(client, f"{collector}/v1/logs", {"resourceLogs": rl})
        l_sent += len(chunk)

    # TRACES -> Tempo
    t_sent = 0
    for i in range(0, len(traces), BATCH):
        chunk = traces[i:i + BATCH]
        by_svc3: dict[str, list] = {}
        for r in chunk:
            start = base_ms + int(r["time"]) * 1000
            start_ns = start * 1_000_000
            end_ns = start_ns + int(float(r["duration_ms"]) * 1_000_000)
            kind = (r.get("attributes") or {}).get("span.kind", "internal")
            sp = {
                "traceId": r["trace_id"], "spanId": r["span_id"],
                "name": r.get("operation", "op"),
                "kind": _KIND_INT.get(kind, 0),
                "startTimeUnixNano": str(start_ns), "endTimeUnixNano": str(end_ns),
                "status": {"code": 2 if r.get("status") == "ERROR" else 0},
                "attributes": [{"key": "span.kind", "value": {"stringValue": kind}}],
            }
            if r.get("parent_span_id"):
                sp["parentSpanId"] = r["parent_span_id"]
            by_svc3.setdefault(r["service"], []).append(sp)
        rs = [{"resource": _svc_res(svc), "scopeSpans": [{"spans": sps}]}
              for svc, sps in by_svc3.items()]
        _post(client, f"{collector}/v1/traces", {"resourceSpans": rs})
        t_sent += len(chunk)

    client.close()
    window = {
        "scenario": scenario_dir.name,
        "window_start_ms": base_ms,
        "window_end_ms": base_ms + span * 1000,
        "onset_second": onset,
        "window_span_s": span,
        "fed": {"metrics": m_sent, "logs": l_sent, "traces": t_sent},
    }
    (scenario_dir / "window.json").write_text(json.dumps(window, indent=2))
    return window


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario_dir")
    ap.add_argument("--collector", default="http://localhost:4318")
    ap.add_argument("--onset", type=int, default=720)
    args = ap.parse_args()
    w = feed(Path(args.scenario_dir), args.collector, args.onset)
    print(json.dumps(w, indent=2))


if __name__ == "__main__":
    main()
