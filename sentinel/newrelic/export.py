"""Export a live incident window from New Relic into fixture-format JSONL.

Snapshots the exact telemetry the agent saw (spans, logs, metric series,
change events) into the run directory so a live run stays inspectable and
replayable after New Relic's retention window expires.
"""

from __future__ import annotations

import json
from pathlib import Path

from sentinel.newrelic.store import NewRelicStore


def _write_jsonl(path: Path, rows: list) -> int:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.model_dump(), sort_keys=True) + "\n")
    return len(rows)


def export_window(store: NewRelicStore, out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    metric_rows = []
    for service, metric, _unit in store.list_metric_keys():
        metric_rows.extend(store.metric_series(service, metric))
    return {
        "spans": _write_jsonl(out_dir / "traces.jsonl", store.all_spans()),
        "logs": _write_jsonl(out_dir / "logs.jsonl", store.search_logs()),
        "metrics": _write_jsonl(out_dir / "metrics.jsonl", metric_rows),
        "changes": _write_jsonl(out_dir / "changes.jsonl", store.list_changes()),
    }
