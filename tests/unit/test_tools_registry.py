"""Registry behavior: schema derivation, dispatch, validation errors, subsetting."""

from __future__ import annotations

from pydantic import BaseModel, Field

from sentinel.errors import ToolInputError
from sentinel.registry import ToolRegistry


class _AddInput(BaseModel):
    a: int = Field(description="first addend")
    b: int = Field(description="second addend")


class _AddOutput(BaseModel):
    total: int


def _build() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(namespace="math")
    def add_numbers(params: _AddInput, store: object) -> _AddOutput:
        """Add two integers and return the total."""
        return _AddOutput(total=params.a + params.b)

    @reg.tool(namespace="math")
    def boom(params: _AddInput, store: object) -> _AddOutput:
        """Always raises a typed tool error."""
        raise ToolInputError(
            code="bad_input",
            message="a must be positive",
            hint="pass a >= 1",
            example={"a": 1, "b": 2},
        )

    return reg


def test_registers_name_namespace_and_models() -> None:
    reg = _build()
    spec = reg.get("add_numbers")
    assert spec.name == "add_numbers"
    assert spec.namespace == "math"
    assert spec.input_model is _AddInput
    assert spec.output_model is _AddOutput
    assert "Add two integers" in spec.description


def test_anthropic_schema_shape() -> None:
    reg = _build()
    schema = next(s for s in reg.anthropic_schemas() if s["name"] == "add_numbers")
    assert schema["description"]
    assert schema["input_schema"]["type"] == "object"
    assert set(schema["input_schema"]["properties"]) == {"a", "b"}


def test_dispatch_valid_returns_output_dict() -> None:
    reg = _build()
    out = reg.dispatch("add_numbers", {"a": 2, "b": 3}, store=None)
    assert out == {"total": 5}


def test_dispatch_validation_error_is_structured_not_raised() -> None:
    reg = _build()
    out = reg.dispatch("add_numbers", {"a": "not-an-int", "b": 3}, store=None)
    assert "error" in out
    assert out["error"]["code"] == "invalid_input"
    assert "a" in out["error"]["message"]
    assert out["error"]["example"] is not None


def test_dispatch_tool_input_error_is_structured() -> None:
    reg = _build()
    out = reg.dispatch("boom", {"a": 2, "b": 3}, store=None)
    assert out["error"]["code"] == "bad_input"
    assert out["error"]["hint"] == "pass a >= 1"


def test_unknown_tool_raises_keyerror() -> None:
    reg = _build()
    try:
        reg.get("does_not_exist")
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown tool")


def test_subset_filters_by_namespace_and_name() -> None:
    reg = _build()
    only_math = reg.subset(namespaces={"math"})
    assert {s["name"] for s in only_math} == {"add_numbers", "boom"}
    only_add = reg.subset(names={"add_numbers"})
    assert {s["name"] for s in only_add} == {"add_numbers"}
