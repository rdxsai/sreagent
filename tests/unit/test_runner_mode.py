from sentinel.agent.runner import _defaults


def test_defaults_expose_tool_mode_and_backend(monkeypatch):
    monkeypatch.setenv("SENTINEL_TOOL_MODE", "code")
    monkeypatch.setenv("SENTINEL_CODE_BACKEND", "local")
    d = _defaults()
    assert d["tool_mode"] == "code"
    assert d["code_backend"] == "local"
