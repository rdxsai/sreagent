from pathlib import Path

import sentinel.tools  # noqa: F401
from sentinel.tools.store import FixtureStore
from sentinel_tool_eval.harness import _build_code_result  # added in Step 3


def test_code_result_carries_internal_calls_and_two_tools():
    store = FixtureStore(Path("fixtures/ad_high_cpu_001/public"))
    # a stand-in CodeAgentResult-like object
    class _Loop:
        calls = ["run_code", "report_root_cause"]
        tool_errors = 0
        iterations = 2
        stop = "reported"
        usage = {"input": 1, "output": 1}
        feedback = ""
        denials = 0
    class _Res:
        report = {"culprit_change_id": "chg_0003"}
        loop = _Loop()
        internal_calls = ["metrics_detect_shift", "changes_rank_culprit"]
        internal_events = [{"tool": "metrics_detect_shift"}]
    tr = _build_code_result("ad_high_cpu_001", _Res(), {"correct": True})
    assert tr.tool_mode == "code"
    assert tr.manager_exposed_tools == 2
    assert tr.internal_calls == ["metrics_detect_shift", "changes_rank_culprit"]
