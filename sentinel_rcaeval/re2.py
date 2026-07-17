"""Reader for real RCAEval RE2 case directories into Sentinel public fixtures.

The synthetic-shaped converter (case.py/normalize.py/convert.py) assumes JSON
metrics and a 4-token case name. Real RE2 differs, which this module handles:
  - case dirs are `<service>_<fault>/<instance>` (no system prefix; numbered subdirs)
  - metrics are `simple_metrics.csv` (wide CSV), columns `<service>_<base>` where
    base is cpu/mem/diskio/socket/latency-90/latency-50/error/workload
  - logs use `container_name` and nanosecond `timestamp`
  - traces use traceID/spanID/serviceName/operationName/startTimeMillis(ms)/
    duration(microseconds)/statusCode(grpc 0=OK)/parentSpanID, with no span kind

It maps those onto the canonical MetricRow/LogRow/TraceRow the FixtureStore serves,
reusing windowing, symptom synthesis, and the truth/fault maps. Service names are
reconciled to the trace serviceName so metrics, traces, and the ground-truth
service all agree (metrics call it `frontend`, traces `frontendservice`).
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Iterator

from sentinel.fixtures.schemas import (
    LogRow,
    MetricRow,
    PublicManifest,
    RootCause,
    TimeWindow,
    TraceRow,
)
from sentinel_rcaeval.convert import _write_jsonl
from sentinel_rcaeval.normalize import Window, _downsample, in_window, make_window, rebase
from sentinel_rcaeval.symptom import synthesize_symptom
from sentinel_rcaeval.truth import FAULT_CATEGORY, FAULT_INDICATOR, FAULT_TYPE, RCAEvalTruth

RE2_FAULTS = {"cpu", "mem", "disk", "socket", "delay", "loss"}


def _canonical(base: str) -> tuple[str, str, float]:
    """(canonical metric, unit, value scale). RE2 latency columns are in seconds,
    so they scale to milliseconds."""
    b = base.lower()
    if b == "cpu":
        return "cpu_utilization", "ratio", 1.0
    if b in ("mem", "memory"):
        return "memory_mb", "MB", 1.0
    if b in ("error", "errors"):
        return "request_error_rate", "ratio", 1.0
    if b == "latency-90":
        return "latency_p95_ms", "ms", 1000.0
    if b == "latency-50":
        return "latency_p50_ms", "ms", 1000.0
    return base, "count", 1.0


def iter_cases(system_root: Path, system: str) -> Iterator[tuple[str, str, str, str, Path]]:
    """Yield (case_id, service, fault, instance, instance_dir) for each real case.

    case_id is the 4-token `{system}_{service}_{fault}_{instance}` the offline
    scorecard's parse_case_name expects.
    """
    for fault_dir in sorted(p for p in Path(system_root).iterdir() if p.is_dir()):
        if "_" not in fault_dir.name:
            continue
        service, fault = fault_dir.name.rsplit("_", 1)
        if fault not in RE2_FAULTS:
            continue
        for inst_dir in sorted(p for p in fault_dir.iterdir() if p.is_dir()):
            yield f"{system}_{service}_{fault}_{inst_dir.name}", service, fault, inst_dir.name, inst_dir


def _trace_services(case_dir: Path) -> set[str]:
    path = case_dir / "traces.csv"
    if not path.exists():
        return set()
    services: set[str] = set()
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("serviceName") or "").strip()
            if name:
                services.add(name)
    return services


def _svc(name: str, trace_services: set[str]) -> str:
    """Reconcile a metric service name to the trace serviceName vocabulary."""
    if name in trace_services:
        return name
    if name + "service" in trace_services:
        return name + "service"
    return name


def load_metrics(case_dir: Path, window: Window, trace_services: set[str]) -> list[MetricRow]:
    with (case_dir / "simple_metrics.csv").open(newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        cols: list[tuple[int, str, str, str, float]] = []
        for i, h in enumerate(header):
            if i == 0 or "_" not in h:
                continue
            service, base = h.rsplit("_", 1)
            metric, unit, scale = _canonical(base)
            cols.append((i, _svc(service, trace_services), metric, unit, scale))
        rows: list[MetricRow] = []
        for r in reader:
            try:
                t = int(float(r[0]))
            except (ValueError, IndexError):
                continue
            if not in_window(t, window):
                continue
            rt = rebase(t, window)
            for i, service, metric, unit, scale in cols:
                v = r[i].strip() if i < len(r) else ""
                if not v or v.lower() in ("nan", "null"):
                    continue
                try:
                    value = float(v) * scale
                except ValueError:
                    continue
                rows.append(MetricRow(time=rt, service=service, metric=metric, value=value, unit=unit))
    return rows


def load_logs(case_dir: Path, window: Window, cap: int = 20000) -> list[LogRow]:
    path = case_dir / "logs.csv"
    if not path.exists():
        return []
    out: list[LogRow] = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("timestamp") or row.get("time") or "").strip()
            if not raw:
                continue
            try:
                t = int(float(raw) / 1e9)  # nanoseconds -> seconds
            except ValueError:
                continue
            if not in_window(t, window):
                continue
            out.append(
                LogRow(
                    time=rebase(t, window),
                    service=(row.get("container_name") or "unknown") or "unknown",
                    severity=(row.get("level") or "info") or "info",
                    message=(row.get("message") or "(no message)") or "(no message)",
                    trace_id=(row.get("trace_id") or None),
                )
            )
    return _downsample(out, cap, lambda r: r.severity.upper() in {"ERROR", "ERR", "CRITICAL", "FATAL", "WARN", "WARNING"})


def load_traces(case_dir: Path, window: Window, cap: int = 20000) -> list[TraceRow]:
    path = case_dir / "traces.csv"
    if not path.exists():
        return []
    out: list[TraceRow] = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("startTimeMillis") or "").strip()
            if not raw:
                continue
            try:
                t = int(int(raw) / 1000)  # milliseconds -> seconds
            except ValueError:
                continue
            if not in_window(t, window):
                continue
            trace_id = (row.get("traceID") or row.get("traceId") or "").strip()
            span_id = (row.get("spanID") or row.get("spanId") or "").strip()
            if not trace_id or not span_id:
                continue
            sc = (row.get("statusCode") or "").strip()
            try:
                is_err = bool(sc and float(sc) != 0.0)
            except ValueError:
                is_err = False
            try:
                dur_ms = float((row.get("duration") or "0").strip() or 0.0) / 1000.0  # microseconds -> ms
            except ValueError:
                dur_ms = 0.0
            out.append(
                TraceRow(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=(row.get("parentSpanID") or None),
                    time=rebase(t, window),
                    service=(row.get("serviceName") or "unknown") or "unknown",
                    operation=(row.get("operationName") or "unknown") or "unknown",
                    duration_ms=max(dur_ms, 0.0),
                    status="ERROR" if is_err else "OK",
                    attributes={"span.kind": "server"},
                )
            )
    return _downsample(out, cap, lambda r: r.status.upper() not in {"OK", "UNSET"})


def convert_re2_case(
    case_id: str,
    service: str,
    fault: str,
    case_dir: Path,
    out_root: Path,
    *,
    pre: int = 180,
    post: int = 300,
    cap: int = 20000,
) -> Path:
    inject = int((case_dir / "inject_time.txt").read_text(encoding="utf-8").strip())
    window = make_window(inject, pre=pre, post=post)
    trace_services = _trace_services(case_dir)

    metrics = load_metrics(case_dir, window, trace_services)
    logs = load_logs(case_dir, window, cap)
    traces = load_traces(case_dir, window, cap)
    symptom, alert = synthesize_symptom(metrics, window)

    out_dir = Path(out_root) / case_id
    public = out_dir / "public"
    eval_only = out_dir / "eval_only"
    public.mkdir(parents=True, exist_ok=True)
    eval_only.mkdir(parents=True, exist_ok=True)

    m = _write_jsonl(public / "metrics.jsonl", metrics)
    lg = _write_jsonl(public / "logs.jsonl", logs)
    tr = _write_jsonl(public / "traces.jsonl", traces)

    signals = ["metrics"] + (["logs"] if logs else []) + (["traces"] if traces else [])
    manifest = PublicManifest(
        scenario_id=case_id,
        source="rcaeval-re2",
        time_unit="seconds",
        window=TimeWindow(start=0, end=window.span_seconds),
        symptom=symptom,
        available_signals=signals,
        notes=[f"real RE2; rows metrics={m} logs={lg} traces={tr} (cap={cap})"],
        alerts=[alert],
    )
    (public / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    rc_service = _svc(service, trace_services)
    truth = RCAEvalTruth(
        scenario_id=case_id,
        root_cause=RootCause(kind="service", type=FAULT_TYPE.get(fault, fault), service=rc_service),
        accepted_services=[rc_service],
        root_cause_indicator=FAULT_INDICATOR.get(fault),
        fault_category=FAULT_CATEGORY.get(fault, "unknown"),
    )
    (eval_only / "truth.json").write_text(truth.model_dump_json(indent=2), encoding="utf-8")
    return out_dir
