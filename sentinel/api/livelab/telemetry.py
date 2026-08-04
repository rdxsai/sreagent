"""Chart telemetry for the dashboard, read straight from New Relic so the page
shows exactly the data the agent investigates (trailing ingest by ~60-90s).

One FACET TIMESERIES query per metric covers all services per poll; every live
response is journaled so a finished run replays its charts frame by frame.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

# metric key in the response -> NRQL select expression. The pinned collector
# (otel/opentelemetry-collector-contrib:0.151.0) emits container.cpu.utilization
# already in percent-of-core (verified live: a 3-hog fault reads ~175, idle ~1-5),
# so no rescaling: what the chart shows is what New Relic stores.
_METRICS = {
    "cpu": "average(container.cpu.utilization)",
    "mem": "average(container.memory.percent)",
}


def _quoted(services: list[str]) -> str:
    return ", ".join(f"'{s}'" for s in services)


def _fold_rows(rows: list[dict]) -> dict[str, list[list[float]]]:
    """FACET TIMESERIES rows -> {service: [[t_ms, value], ...]} sorted by time.
    The facet arrives as a string or a single-element list depending on the query;
    the value key is whatever aggregate key isn't a timestamp/facet field."""
    series: dict[str, list[list[float]]] = {}
    for row in rows:
        facet = row.get("facet")
        if isinstance(facet, list):
            facet = facet[0] if facet else None
        if facet is None:
            facet = row.get("container.name")
        begin = row.get("beginTimeSeconds")
        if facet is None or begin is None:
            continue
        value = next((v for k, v in row.items()
                      if k not in ("facet", "container.name", "beginTimeSeconds", "endTimeSeconds")
                      and isinstance(v, (int, float))), None)
        if value is None:
            continue
        series.setdefault(str(facet), []).append([int(begin) * 1000, float(value)])
    for points in series.values():
        points.sort(key=lambda p: p[0])
    return series


class TelemetryReader:
    def __init__(self, nrql: Callable[[str], list[dict]], *, journal_dir: Path | None,
                 clock: Callable[[], float] = time.time) -> None:
        self._nrql = nrql
        self._clock = clock
        self._journal_path: Path | None = None
        if journal_dir is not None:
            journal_dir.mkdir(parents=True, exist_ok=True)
            self._journal_path = journal_dir / "telemetry.jsonl"

    def series(self, services: list[str], since_ms: int, until_ms: int) -> dict:
        out: dict = {"series": {}, "fetched_at_ms": int(self._clock() * 1000)}
        for key, select in _METRICS.items():
            q = (f"SELECT {select} FROM Metric "
                 f"WHERE container.name IN ({_quoted(services)}) "
                 f"FACET container.name TIMESERIES 15 seconds "
                 f"SINCE {since_ms} UNTIL {until_ms}")
            out["series"][key] = _fold_rows(self._nrql(q))
        if self._journal_path is not None:
            with self._journal_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(out) + "\n")
        return out

    def ingest_age_s(self) -> float | None:
        rows = self._nrql("SELECT latest(timestamp) FROM Metric SINCE 10 minutes ago")
        if not rows:
            return None
        latest_ms = rows[0].get("latest.timestamp")
        if latest_ms is None:
            return None
        return self._clock() - float(latest_ms) / 1000.0

    def cpu_now(self, service: str) -> float | None:
        """Average CPU (percent of a core, can exceed 100) over the last 90s; the
        recovery health callable (health = badness, drops when the fault clears)."""
        rows = self._nrql(
            "SELECT average(container.cpu.utilization) AS p FROM Metric "
            f"WHERE container.name = '{service}' SINCE 90 seconds ago"
        )
        if not rows:
            return None
        value = rows[0].get("p")
        return None if value is None else float(value)


class ReplayTelemetryReader:
    """Serves the journaled poll responses of a finished run: the frame whose
    fetched_at_ms is closest at-or-before the requested moment."""

    def __init__(self, journal_path: Path) -> None:
        self._frames = [json.loads(line)
                        for line in journal_path.read_text().splitlines() if line.strip()]
        self._frames.sort(key=lambda f: f["fetched_at_ms"])

    def at(self, at_ms: int) -> dict:
        chosen = self._frames[0] if self._frames else {"series": {}, "fetched_at_ms": 0}
        for frame in self._frames:
            if frame["fetched_at_ms"] <= at_ms:
                chosen = frame
            else:
                break
        return chosen
