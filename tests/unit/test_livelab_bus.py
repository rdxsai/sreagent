"""EventBus: the in-process seam between the run thread and SSE subscribers.
Sequenced frames, ring-buffer backlog for late joiners, JSONL journal for replay,
and a TraceLogger subclass that broadcasts agent records."""
from __future__ import annotations

import json
import threading

from sentinel.api.livelab.bus import BroadcastTraceLogger, EventBus
from sentinel.oss.trace import TraceContext


def test_seq_is_monotonic_and_frames_carry_event_and_data() -> None:
    bus = EventBus()
    s1 = bus.emit("phase", {"phase": "baseline"})
    s2 = bus.emit("lab", {"services": []})
    assert (s1, s2) == (1, 2)
    frames = bus.backlog()
    assert [f["seq"] for f in frames] == [1, 2]
    assert frames[0]["event"] == "phase"
    assert frames[0]["data"]["phase"] == "baseline"


def test_late_subscriber_gets_backlog_then_live_frames() -> None:
    bus = EventBus()
    bus.emit("phase", {"phase": "baseline"})
    sub = bus.subscribe()
    assert sub.get(timeout=1)["data"]["phase"] == "baseline"
    bus.emit("phase", {"phase": "injecting"})
    assert sub.get(timeout=1)["data"]["phase"] == "injecting"


def test_subscribe_after_seq_skips_older_frames() -> None:
    bus = EventBus()
    bus.emit("phase", {"phase": "baseline"})
    bus.emit("phase", {"phase": "injecting"})
    sub = bus.subscribe(after=1)
    assert sub.get(timeout=1)["data"]["phase"] == "injecting"


def test_close_delivers_done_and_ends_iteration() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    bus.emit("phase", {"phase": "done"})
    bus.close()
    frames = list(iter(lambda: sub.get(timeout=1), None))
    assert frames[-1]["event"] == "done"


def test_frames_are_journaled_as_jsonl(tmp_path) -> None:
    bus = EventBus(journal_dir=tmp_path)
    bus.emit("phase", {"phase": "baseline"})
    bus.emit("agent", {"kind": "manager"})
    lines = [json.loads(l) for l in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [l["event"] for l in lines] == ["phase", "agent"]
    assert all("at_ms" in l for l in lines)


def test_broadcast_trace_logger_writes_file_and_emits(tmp_path) -> None:
    bus = EventBus()
    sub = bus.subscribe()
    logger = BroadcastTraceLogger(tmp_path / "t.jsonl", bus)
    ctx = TraceContext(run_id="r", agent_id="manager")
    logger.manager(ctx, step="topology", edges=16)
    frame = sub.get(timeout=1)
    assert frame["event"] == "agent"
    assert frame["data"]["kind"] == "manager"
    assert frame["data"]["step"] == "topology"
    on_disk = json.loads((tmp_path / "t.jsonl").read_text())
    assert on_disk["step"] == "topology"


def test_concurrent_emitters_do_not_lose_or_duplicate_seq() -> None:
    bus = EventBus()

    def spam(n: int) -> None:
        for _ in range(n):
            bus.emit("lab", {})

    threads = [threading.Thread(target=spam, args=(50,)) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    seqs = [f["seq"] for f in bus.backlog()]
    assert seqs == list(range(1, 201))
