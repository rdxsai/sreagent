# tests/integration/test_code_agent.py
from pathlib import Path

import sentinel.tools  # noqa: F401
from sentinel.agent.codeagent import run_code_agent
from sentinel.tools.store import FixtureStore

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ad_high_cpu_001" / "public"


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Usage:
    input_tokens = 10
    output_tokens = 5
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Resp:
    def __init__(self, content, stop_reason="tool_use"):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Usage()


class _FakeMessages:
    def __init__(self, store):
        self._store = store
        self._turn = 0

    def create(self, **kwargs):
        self._turn += 1
        if self._turn == 1:
            code = 's = metrics.detect_shift("ad", "cpu_cores"); print(s["shift_second"])'
            return _Resp([_Block(type="tool_use", id="t1", name="run_code", input={"code": code})])
        known = [c.id for c in self._store.list_changes()]
        report = {
            "root_cause": {"kind": "service", "service": "ad", "type": "cpu_saturation"},
            "culprit_change_id": known[2],
            "ruled_out_change_ids": [c for i, c in enumerate(known) if i != 2],
            "evidence": ["ad cpu_cores shifted at 420"],
        }
        return _Resp([_Block(type="tool_use", id="t2", name="report_root_cause", input=report)])


class _FakeClient:
    def __init__(self, store):
        self.messages = _FakeMessages(store)


def test_code_agent_investigates_then_reports():
    store = FixtureStore(_FIXTURE)
    result = run_code_agent(
        _FakeClient(store), store, "Investigate the incident.",
        model="fake", effort="default", max_iters=6, max_tokens=1024,
        output_budget=100_000, max_tool_calls=50, executor_backend="local",
    )
    assert result.report is not None
    assert result.report["root_cause"]["service"] == "ad"
    assert "metrics_detect_shift" in result.internal_calls
