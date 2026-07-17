import inspect

from sentinel_tool_eval.harness import run_task_with


def test_run_task_with_accepts_grader_kwarg():
    sig = inspect.signature(run_task_with)
    assert "grader" in sig.parameters
    assert sig.parameters["grader"].kind == inspect.Parameter.KEYWORD_ONLY
