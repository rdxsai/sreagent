"""Window, re-base, and normalize RCAEval telemetry into Sentinel row schemas."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from sentinel.fixtures.schemas import MetricRow


@dataclass(frozen=True)
class Window:
    inject_time: int
    start_abs: int
    end_abs: int

    @property
    def span_seconds(self) -> int:
        return self.end_abs - self.start_abs

    @property
    def onset_second(self) -> int:
        return self.inject_time - self.start_abs


def make_window(inject_time: int, pre: int = 180, post: int = 300) -> Window:
    return Window(inject_time=inject_time, start_abs=inject_time - pre, end_abs=inject_time + post)


def rebase(t_abs: int, window: Window) -> int:
    return t_abs - window.start_abs


def in_window(t_abs: int, window: Window) -> bool:
    return window.start_abs <= t_abs <= window.end_abs


# Match a known metric suffix at the end of an RCAEval column; the prefix is the
# service. Ordered longest/most-specific first. Anything unmatched falls back to
# a last-underscore split with a neutral "count" unit.
_SUFFIX_MAP: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"_(error[_-]?rate|errors?|error[_-]?ratio)$", re.IGNORECASE), "request_error_rate", "ratio"),
    (re.compile(r"_(latency|duration|lat|resp(?:onse)?[_-]?time)(?:_p?\d+)?$", re.IGNORECASE), "latency_p95_ms", "ms"),
    (re.compile(r"_(cpu)(?:_\w+)?$", re.IGNORECASE), "cpu_utilization", "ratio"),
    (re.compile(r"_(mem|memory)(?:_\w+)?$", re.IGNORECASE), "memory_mb", "MB"),
]


def canonical_column(column: str) -> tuple[str, str, str]:
    for pat, metric, unit in _SUFFIX_MAP:
        m = pat.search(column)
        if m and column[: m.start()]:
            return column[: m.start()], metric, unit
    if "_" in column:
        service, metric = column.rsplit("_", 1)
        return service, metric, "count"
    return column, "value", "count"


@dataclass(frozen=True)
class MetricFrame:
    times: list[int]
    columns: dict[str, list[float]]


def load_metric_frame(path: Path) -> MetricFrame:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        times = [int(float(t)) for t in data["time"]]
        columns = {k: [float(x) for x in v] for k, v in data.items() if k != "time"}
        return MetricFrame(times=times, columns=columns)
    if isinstance(data, list):
        times = [int(float(r["time"])) for r in data]
        keys = [k for k in data[0] if k != "time"] if data else []
        columns = {k: [float(r[k]) for r in data] for k in keys}
        return MetricFrame(times=times, columns=columns)
    raise ValueError(f"unrecognized metrics.json shape in {path}")


def melt_metrics(frame: MetricFrame, window: Window) -> list[MetricRow]:
    rows: list[MetricRow] = []
    for column, values in frame.columns.items():
        service, metric, unit = canonical_column(column)
        for t_abs, value in zip(frame.times, values):
            if in_window(t_abs, window):
                rows.append(
                    MetricRow(time=rebase(t_abs, window), service=service, metric=metric, value=value, unit=unit)
                )
    return rows
