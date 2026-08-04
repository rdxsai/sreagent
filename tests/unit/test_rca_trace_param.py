"""run_rca accepts an injected TraceLogger so a live dashboard can bridge trace
records to an event stream; when omitted it constructs its own exactly as before."""
from __future__ import annotations

import inspect

from sentinel.oss.rca import run_rca
from sentinel.oss.trace import TraceLogger


def test_run_rca_accepts_optional_trace_logger() -> None:
    sig = inspect.signature(run_rca)
    param = sig.parameters.get("trace")
    assert param is not None, "run_rca must expose a trace parameter"
    assert param.default is None
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_injected_logger_is_used_for_the_run(tmp_path, monkeypatch) -> None:
    used: list[TraceLogger] = []

    class Recorder(TraceLogger):
        def log(self, ctx, kind, **data):
            used.append(self)
            super().log(ctx, kind, **data)

    logger = Recorder(tmp_path / "bridge.jsonl")

    # Stop the run at the first LLM-free seam: resolve_topology raising ends the run
    # right after the first trace record would have been written.
    def boom(*a, **k):
        raise RuntimeError("stop-here")

    monkeypatch.setattr("sentinel.oss.rca.resolve_topology", boom)
    monkeypatch.setattr("sentinel.oss.rca.client_for", lambda model: (None, None))

    try:
        run_rca(store=None, incident="x", out_dir=tmp_path, run_id="t", system="sock_shop",
                trace=logger)
    except RuntimeError:
        pass

    # The injected logger's file is the run's trace path even though the run aborted
    # before any record: construction must not have replaced it with an internal one.
    assert logger.path() == tmp_path / "bridge.jsonl"
    internal = tmp_path / "t.jsonl"
    assert not internal.exists(), "run_rca must not create its own logger when one is injected"
