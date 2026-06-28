import sentinel.tools  # noqa: F401  populate the registry
from sentinel.sandbox.client_gen import (
    code_tool_specs,
    generate_client_digest,
    generate_client_source,
)


def test_specs_exclude_terminals_and_orchestration():
    names = {s.name for s in code_tool_specs()}
    assert "metrics_detect_shift" in names
    assert "report_root_cause" not in names
    assert "report_finding" not in names
    assert not any(n.startswith("investigate_") for n in names)


def test_source_defines_namespaces_and_routes_through_rpc():
    src = generate_client_source(code_tool_specs())
    assert "class metrics:" in src
    assert "def detect_shift(service, metric):" in src
    assert 'return _rpc("metrics_detect_shift"' in src
    # the generated module runs and routes calls to the injected _rpc
    ns: dict = {}
    exec(src, ns)
    seen = {}
    ns["_rpc"] = lambda tool, args: seen.setdefault("call", (tool, args)) or {"ok": True}
    ns["metrics"].detect_shift("ad", "cpu_cores")
    assert seen["call"] == ("metrics_detect_shift", {"service": "ad", "metric": "cpu_cores"})


def test_optional_params_omit_unset_values():
    src = generate_client_source(code_tool_specs())
    ns: dict = {}
    exec(src, ns)
    calls = []
    ns["_rpc"] = lambda tool, args: calls.append((tool, args)) or {"ok": True}
    ns["traces"].find(service="ad")  # all other params optional
    assert calls[-1] == ("traces_find", {"service": "ad"})


def test_digest_lists_callable_signatures():
    digest = generate_client_digest(code_tool_specs())
    assert "metrics.detect_shift(service: str, metric: str)" in digest
    assert "report_root_cause" not in digest
