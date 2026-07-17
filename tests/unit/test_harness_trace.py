from types import SimpleNamespace

import sentinel_tool_eval.harness as h


def test_run_task_with_plumbs_manager_trace(monkeypatch):
    fake_loop = SimpleNamespace(
        calls=[], tool_errors=0, iterations=1, stop="reported", usage={}, feedback="", denials=0
    )
    fake_result = SimpleNamespace(
        report={"root_cause": {"kind": "service", "service": "x"}},
        manager_loop=fake_loop,
        worker_calls=[],
        worker_usage={},
        subagents=0,
        findings=[],
        trace=[{"type": "tool_call", "tool": "metrics_series"}],
    )
    monkeypatch.setattr(h, "run_manager", lambda *a, **k: fake_result)
    out = h.run_task_with(
        client=None, store=None, truth=None, prompt="p", scenario_id="s",
        grader=lambda report, truth: {"correct": True},
    )
    assert out.trace == [{"type": "tool_call", "tool": "metrics_series"}]
