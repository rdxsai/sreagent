"""A tiny hand-built RCAEval case for converter/grader tests.

Absolute inject time is 1000s. Metric sample times span 820..1300 (step 30),
so with the default window (pre=180, post=300) every point falls inside
[820, 1300] and the injected onset re-bases to second 180. cartservice_cpu and
frontend_error_rate breach after onset; frontend_latency rises mildly.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

INJECT_TIME = 1000
TIMES = list(range(820, 1330, 30))  # 820, 850, ..., 1300  (17 points)


def _series(baseline: float, breach: float) -> list[float]:
    return [baseline if t < INJECT_TIME else breach for t in TIMES]


def write_synth_case(raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "inject_time.txt").write_text(f"{INJECT_TIME}\n", encoding="utf-8")

    metrics = {
        "time": TIMES,
        "frontend_error_rate": _series(0.001, 0.20),
        "frontend_latency": _series(90.0, 140.0),
        "cartservice_cpu": _series(0.10, 0.95),
        "cartservice_mem": _series(120.0, 130.0),
    }
    (raw_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")

    with (raw_dir / "logs.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "service", "level", "message", "trace_id"])
        w.writerow([850, "cartservice", "INFO", "ok", "t1"])
        w.writerow([1030, "cartservice", "ERROR", "cpu throttled", "t2"])
        w.writerow([1600, "cartservice", "ERROR", "outside window", "t3"])  # dropped

    with (raw_dir / "traces.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["trace_id", "span_id", "parent_id", "start_time",
                    "service", "operation", "duration_ms", "status"])
        w.writerow(["t1", "s1", "", 850, "frontend", "GET /cart", 20.0, "OK"])
        w.writerow(["t2", "s2", "s1", 1030, "cartservice", "AddItem", 800.0, "ERROR"])
