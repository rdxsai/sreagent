"""Component 1: side-effect classification. A mutate/notify tool must never reach the
read-only investigation path (sandbox SDK + manager catalog); read tools still do."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from sentinel.registry import REGISTRY
from sentinel.sandbox.client_gen import code_tool_specs


class _In(BaseModel):
    x: int = 0


class _Out(BaseModel):
    ok: bool = True


def test_effect_defaults_to_read_for_existing_tools():
    import sentinel.tools  # noqa: F401  (register the 52 tools)
    reads = [s for s in REGISTRY.specs() if s.effect == "read"]
    assert len(reads) >= 50  # all existing tools default to read
    assert all(s.effect == "read" for s in code_tool_specs())


def test_mutate_tool_excluded_from_sandbox_and_catalog():
    @REGISTRY.tool(namespace="acttest", effect="mutate")
    def acttest_restart(params: _In, store: object) -> _Out:  # noqa: ARG001
        """A mutate action tool for the test."""
        return _Out()

    @REGISTRY.tool(namespace="acttest", effect="read")
    def acttest_probe(params: _In, store: object) -> _Out:  # noqa: ARG001
        """A read tool for the test."""
        return _Out()

    try:
        names = {s.name for s in code_tool_specs()}
        assert "acttest_restart" not in names       # mutate excluded from sandbox SDK
        assert "acttest_probe" in names              # read included

        # oss/catalog derives from code_tool_specs, so the manager catalog is also clean
        from sentinel.oss.catalog import all_tool_names, sdk_for
        assert "acttest_restart" not in all_tool_names()
        assert "acttest_restart" not in sdk_for(["acttest_restart", "acttest_probe"])
    finally:
        REGISTRY._specs.pop("acttest_restart", None)
        REGISTRY._specs.pop("acttest_probe", None)


def test_invalid_effect_rejected():
    with pytest.raises(ValueError, match="effect must be"):
        @REGISTRY.tool(namespace="acttest", effect="destroy")
        def acttest_bad(params: _In, store: object) -> _Out:  # noqa: ARG001
            """bad."""
            return _Out()
