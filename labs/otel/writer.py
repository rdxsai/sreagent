"""Write sealed OpenTelemetry lab fixture directories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from labs.otel.redactor import assert_no_banned_tokens
from sentinel.fixtures.schemas import (
    ChangeEvent,
    LogRow,
    MetricRow,
    PrivateTruth,
    PublicManifest,
    Topology,
    TraceRow,
)


class FixtureWriteError(ValueError):
    """Raised when a fixture write would break the public/private seal."""


def write_fixture(
    scenario_dir: Path,
    *,
    manifest: PublicManifest,
    topology: Topology,
    metrics: list[MetricRow],
    logs: list[LogRow],
    traces: list[TraceRow],
    changes: list[ChangeEvent],
    truth: PrivateTruth,
    injection_log: dict[str, Any] | None = None,
    runbooks: list[dict[str, Any]] | None = None,
) -> None:
    """Write one scenario fixture and fail if public output leaks banned tokens."""

    if manifest.scenario_id != truth.scenario_id:
        raise FixtureWriteError("manifest and truth scenario_id values must match")

    root = scenario_dir.resolve()
    public_dir = root / "public"
    eval_dir = root / "eval_only"
    public_dir.mkdir(parents=True, exist_ok=True)
    eval_dir.mkdir(parents=True, exist_ok=True)

    _write_json(public_dir / "manifest.json", manifest)
    _write_json(public_dir / "topology.json", topology)
    _write_jsonl(public_dir / "metrics.jsonl", sorted(metrics, key=_metric_key))
    _write_jsonl(public_dir / "logs.jsonl", sorted(logs, key=_log_key))
    _write_jsonl(public_dir / "traces.jsonl", sorted(traces, key=_trace_key))
    _write_jsonl(public_dir / "changes.jsonl", sorted(changes, key=_change_key))
    _write_jsonl(public_dir / "runbooks.jsonl", runbooks or [])

    assert_no_banned_tokens(public_dir)

    _write_json(eval_dir / "truth.json", truth)
    if injection_log is not None:
        _write_plain_json(eval_dir / "injection_log.json", injection_log)


def _write_json(path: Path, model: BaseModel) -> None:
    path.write_text(
        json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_plain_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[BaseModel] | list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            if isinstance(row, BaseModel):
                payload = row.model_dump(mode="json")
            else:
                payload = row
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def _metric_key(row: MetricRow) -> tuple[int, str, str, float]:
    return (row.time, row.service, row.metric, row.value)


def _log_key(row: LogRow) -> tuple[int, str, str, str]:
    return (row.time, row.service, row.severity, row.message)


def _trace_key(row: TraceRow) -> tuple[int, str, str]:
    return (row.time, row.trace_id, row.span_id)


def _change_key(row: ChangeEvent) -> tuple[int, str]:
    return (row.time, row.id)
