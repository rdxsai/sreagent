"""Real RE2 schema handling: CSV metrics with seconds->ms latency scaling, service-name
reconciliation to the trace vocabulary, trace microsecond->ms duration, and span.kind.
Uses a tiny synthetic case in the *real* RE2 shape so it needs no downloaded data."""

import csv
from pathlib import Path

from sentinel.tools.store import FixtureStore
from sentinel_rcaeval.re2 import convert_re2_case
from sentinel_rcaeval.truth import RCAEvalTruth


def _write_real_case(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / "inject_time.txt").write_text("1000", encoding="utf-8")
    times = list(range(820, 1330, 30))  # window [820,1300] around inject=1000
    with (d / "simple_metrics.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        # metric column 'frontend_*' must reconcile to trace serviceName 'frontendservice'
        w.writerow(["time", "frontend_cpu", "frontend_latency-90",
                    "recommendationservice_cpu", "recommendationservice_latency-90"])
        for t in times:
            lat = 0.40 if t >= 1000 else 0.01  # seconds
            w.writerow([t, 0.1, lat, 0.1, lat])
    with (d / "traces.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "traceID", "spanID", "serviceName", "methodName", "operationName",
                    "startTimeMillis", "startTime", "duration", "statusCode", "parentSpanID"])
        # startTimeMillis in ms; duration in microseconds; statusCode grpc (0.0 = OK)
        w.writerow(["16:30", "t1", "s1", "frontendservice", "", "GET /", "1030000", "", "200000", "0.0", ""])
        w.writerow(["16:30", "t1", "s2", "recommendationservice", "", "ListRecs", "1030000", "", "190000", "0.0", "s1"])


def test_re2_converts_real_shape(tmp_path: Path):
    raw = tmp_path / "recommendationservice_delay" / "1"
    _write_real_case(raw)
    out = convert_re2_case(
        "ob_recommendationservice_delay_1", "recommendationservice", "delay", raw, tmp_path / "converted"
    )
    store = FixtureStore(out / "public")

    # metric service 'frontend' reconciled to the trace serviceName 'frontendservice'
    services = store.list_services()
    assert "frontendservice" in services and "recommendationservice" in services

    # latency-90 is seconds; must be scaled to ms (0.40s -> 400ms)
    lat = store.metric_series("frontendservice", "latency_p95_ms")
    post = [r.value for r in lat if r.time >= 180]
    assert post and max(post) == 400.0

    # trace duration microseconds -> ms, and span.kind set so topology works
    rec_span = [s for s in store.all_spans() if s.service == "recommendationservice"][0]
    assert rec_span.duration_ms == 190.0
    assert rec_span.attributes.get("span.kind") == "server"

    truth = RCAEvalTruth.model_validate_json((out / "eval_only" / "truth.json").read_text())
    assert truth.root_cause.service == "recommendationservice"
    assert "delay" in truth.root_cause.type
