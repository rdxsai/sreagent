"""Deterministic replay loader for public fixture directories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, JsonValue

from sentinel.fixtures.schemas import (
    ChangeEvent,
    LogRow,
    MetricRow,
    PublicFixture,
    PublicManifest,
    TraceRow,
)


class FixtureAccessError(ValueError):
    """Raised when code attempts to load non-public fixture data."""


ModelT = TypeVar("ModelT", bound=BaseModel)


def load_public_fixture(public_dir: Path) -> PublicFixture:
    """Load and sort the observable fixture files from a public directory."""

    root = _require_public_dir(public_dir)
    metrics = _load_jsonl(root / "metrics.jsonl", MetricRow)
    logs = _load_jsonl(root / "logs.jsonl", LogRow)
    traces = _load_jsonl(root / "traces.jsonl", TraceRow)
    changes = _load_jsonl(root / "changes.jsonl", ChangeEvent)
    runbooks = _load_jsonl_dicts(root / "runbooks.jsonl")

    return PublicFixture(
        root=root,
        manifest=_load_json(root / "manifest.json", PublicManifest),
        metrics=sorted(metrics, key=_metric_key),
        logs=sorted(logs, key=_log_key),
        traces=sorted(traces, key=_trace_key),
        changes=sorted(changes, key=_change_key),
        runbooks=sorted(runbooks, key=_canonical_json),
    )


def _require_public_dir(public_dir: Path) -> Path:
    root = public_dir.resolve()
    if root.name != "public":
        raise FixtureAccessError("fixture replay requires a public/ directory")
    if "eval_only" in root.parts or "raw" in root.parts:
        raise FixtureAccessError("fixture replay cannot read eval_only/ or raw/")
    if not root.is_dir():
        raise FixtureAccessError(f"public fixture directory does not exist: {root}")
    return root


def _load_json(path: Path, model_type: type[ModelT]) -> ModelT:
    with path.open(encoding="utf-8") as handle:
        return model_type.model_validate(json.load(handle))


def _load_jsonl(path: Path, model_type: type[ModelT]) -> list[ModelT]:
    rows: list[ModelT] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(model_type.model_validate(json.loads(line)))
    return rows


def _load_jsonl_dicts(path: Path) -> list[dict[str, JsonValue]]:
    rows: list[dict[str, JsonValue]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"expected JSON object row in {path}")
                rows.append(value)
    return rows


def _metric_key(row: MetricRow) -> tuple[int, str, str, str, float]:
    return (row.time, row.service, row.metric, _canonical_json(row.attributes), row.value)


def _log_key(row: LogRow) -> tuple[int, str, str, str, str]:
    return (row.time, row.service, row.severity, row.message, row.trace_id or "")


def _trace_key(row: TraceRow) -> tuple[int, str, str, str]:
    return (row.time, row.trace_id, row.span_id, row.parent_span_id or "")


def _change_key(row: ChangeEvent) -> tuple[int, str]:
    return (row.time, row.id)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
