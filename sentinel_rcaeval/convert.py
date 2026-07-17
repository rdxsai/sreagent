"""Convert one RCAEval case into a FixtureStore-loadable public fixture."""

from __future__ import annotations

import json
from pathlib import Path

from sentinel.fixtures.schemas import PublicManifest, TimeWindow
from sentinel_rcaeval.case import RCAEvalCase, load_case, read_inject_time
from sentinel_rcaeval.normalize import (
    load_metric_frame,
    make_window,
    map_logs,
    map_traces,
    melt_metrics,
)
from sentinel_rcaeval.symptom import synthesize_symptom
from sentinel_rcaeval.truth import build_truth


def _write_jsonl(path: Path, rows: list) -> int:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row.model_dump(), sort_keys=True) + "\n")
    return len(rows)


def convert_case(
    case_or_dir: RCAEvalCase | Path,
    out_root: Path,
    *,
    pre: int = 180,
    post: int = 300,
    cap: int = 20000,
) -> Path:
    case = case_or_dir if isinstance(case_or_dir, RCAEvalCase) else load_case(Path(case_or_dir))
    window = make_window(read_inject_time(case.inject_time_path), pre=pre, post=post)

    metrics = melt_metrics(load_metric_frame(case.metrics_path), window)
    logs_all = map_logs(case.logs_path, window, cap=cap) if case.logs_path.exists() else []
    traces_all = map_traces(case.traces_path, window, cap=cap) if case.traces_path.exists() else []
    symptom, alert = synthesize_symptom(metrics, window)

    available_signals = ["metrics"]
    if case.logs_path.exists():
        available_signals.append("logs")
    if case.traces_path.exists():
        available_signals.append("traces")

    out_dir = Path(out_root) / case.case_id
    public = out_dir / "public"
    eval_only = out_dir / "eval_only"
    public.mkdir(parents=True, exist_ok=True)
    eval_only.mkdir(parents=True, exist_ok=True)

    m = _write_jsonl(public / "metrics.jsonl", metrics)
    lg = _write_jsonl(public / "logs.jsonl", logs_all)
    tr = _write_jsonl(public / "traces.jsonl", traces_all)

    manifest = PublicManifest(
        # scenario_id encodes the case identity (system_service_fault_instance),
        # matching the existing OTel fixtures' convention. It is never surfaced to
        # the agent (build_task_prompt and the store protocol expose only
        # symptom/alerts/window), so it does not hand over the answer; it must not
        # be added to any agent-facing surface.
        scenario_id=case.case_id,
        source="rcaeval-re2",
        time_unit="seconds",
        window=TimeWindow(start=0, end=window.span_seconds),
        symptom=symptom,
        available_signals=available_signals,
        notes=[f"converted from RCAEval; rows metrics={m} logs={lg} traces={tr} (cap={cap})"],
        alerts=[alert],
    )
    (public / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    truth = build_truth(case)
    (eval_only / "truth.json").write_text(truth.model_dump_json(indent=2), encoding="utf-8")
    return out_dir
