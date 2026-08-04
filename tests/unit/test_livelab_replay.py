"""Replay: a journaled run re-streamed through the identical SSE contract with
waits compressed, as demo insurance."""
from __future__ import annotations

import json
import time
from pathlib import Path

from sentinel.api.livelab.replay import ReplayRun, compress_gap, list_replays, start_replay


def write_run(root: Path, run_id: str, *, target: str = "shipping",
              phase: str = "done") -> Path:
    d = root / run_id
    d.mkdir(parents=True)
    frames = [
        {"seq": 1, "event": "phase", "data": {"phase": "baseline"}, "at_ms": 0},
        {"seq": 2, "event": "phase", "data": {"phase": "injecting"}, "at_ms": 120_000},
        {"seq": 3, "event": "agent", "data": {"kind": "manager"}, "at_ms": 121_000},
        {"seq": 4, "event": "phase", "data": {"phase": "done"}, "at_ms": 130_000},
        {"seq": 5, "event": "done", "data": {}, "at_ms": 130_000},
    ]
    (d / "events.jsonl").write_text("\n".join(json.dumps(f) for f in frames) + "\n")
    (d / "run.json").write_text(json.dumps(
        {"run_id": run_id, "mode": "live", "target": target, "phase": phase,
         "started_ms": 1000, "preset": "proven"}))
    (d / "telemetry.jsonl").write_text(json.dumps(
        {"series": {"cpu": {"shipping": [[0, 1.0]]}}, "fetched_at_ms": 500}) + "\n")
    return d


def test_list_replays_reads_finished_runs(tmp_path) -> None:
    write_run(tmp_path, "run-a")
    write_run(tmp_path, "run-b", target="orders", phase="failed")
    (tmp_path / "not-a-run").mkdir()
    replays = list_replays(tmp_path)
    assert {r["run_id"] for r in replays} == {"run-a", "run-b"}
    by_id = {r["run_id"]: r for r in replays}
    assert by_id["run-a"]["target"] == "shipping"
    assert by_id["run-b"]["phase"] == "failed"


def test_compress_gap_scales_and_caps() -> None:
    assert compress_gap(120_000) == 5.0      # long waits collapse to 5s
    assert compress_gap(4000) == 1.0         # short gaps play at quarter speed
    assert compress_gap(0) == 0.0
    assert compress_gap(-50) == 0.0          # out-of-order timestamps never sleep


def test_replay_re_emits_frames_in_order_with_compressed_sleeps(tmp_path) -> None:
    src = write_run(tmp_path, "run-a")
    slept: list[float] = []
    run = ReplayRun("replay-1", src, sleep=slept.append)
    sub = run.bus.subscribe()
    run.start()
    run.join(timeout=5)

    frames = list(iter(lambda: sub.get(timeout=1), None))
    events = [f["event"] for f in frames]
    assert events == ["phase", "phase", "agent", "phase", "done"]
    # data payloads preserved verbatim; seq reassigned by the replay bus
    assert frames[1]["data"] == {"phase": "injecting"}
    assert [f["seq"] for f in frames] == [1, 2, 3, 4, 5]
    assert slept == [compress_gap(120_000), compress_gap(1000), compress_gap(9000)]


def test_replay_snapshot_marks_mode_and_source(tmp_path) -> None:
    src = write_run(tmp_path, "run-a")
    run = ReplayRun("replay-9", src, sleep=lambda s: None)
    snap = run.snapshot()
    assert snap["mode"] == "replay"
    assert snap["source_run_id"] == "run-a"
    assert snap["target"] == "shipping"


def test_replay_abort_stops_mid_stream(tmp_path) -> None:
    src = write_run(tmp_path, "run-a")
    gate_hit = {"n": 0}

    def slow_sleep(s: float) -> None:
        gate_hit["n"] += 1
        time.sleep(0.05)

    run = ReplayRun("replay-2", src, sleep=slow_sleep)
    run.start()
    while gate_hit["n"] == 0:
        time.sleep(0.005)
    run.abort()
    run.join(timeout=5)
    events = [f["event"] for f in run.bus.backlog()]
    assert "done" in events            # bus closes cleanly
    assert events.count("phase") < 3   # did not play to the end


def test_start_replay_resolves_source_by_run_id(tmp_path) -> None:
    write_run(tmp_path, "run-a")
    run = start_replay("run-a", tmp_path, sleep=lambda s: None)
    run.start()
    run.join(timeout=5)
    assert [f["event"] for f in run.bus.backlog()][-1] == "done"
