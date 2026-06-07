"""Schema-driven tool registry.

A tool is a plain function `fn(params: InModel, store) -> OutModel`. The
`@registry.tool(namespace=...)` decorator reads the function's type hints as the
single source of truth for its input and output models, derives the Anthropic
tool schema from the input model, and registers everything under one name.

Selection is model-driven: the agent is handed the schemas and picks tools by
their descriptions. There is no hand-routed dispatch.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, get_type_hints

from pydantic import BaseModel, ValidationError

from sentinel.errors import ToolError


@dataclass(frozen=True)
class ToolSpec:
    name: str
    namespace: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    fn: Callable[..., BaseModel]


def _clean_doc(doc: str | None) -> str:
    if not doc:
        return ""
    return inspect.cleandoc(doc).strip()


def _example_for(model: type[BaseModel]) -> dict[str, Any]:
    """A minimal, type-shaped example of a valid input, to steer a failed call."""
    placeholders: dict[type, Any] = {int: 0, float: 0.0, str: "string", bool: False}
    example: dict[str, Any] = {}
    for field_name, field in model.model_fields.items():
        if field.is_required():
            example[field_name] = placeholders.get(field.annotation, None)  # type: ignore[arg-type]
    return example


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def tool(self, namespace: str) -> Callable[[Callable[..., BaseModel]], Callable[..., BaseModel]]:
        def decorator(fn: Callable[..., BaseModel]) -> Callable[..., BaseModel]:
            hints = get_type_hints(fn)
            if "params" not in hints or "return" not in hints:
                raise TypeError(
                    f"tool {fn.__name__} must annotate a `params` input model and a return model"
                )
            spec = ToolSpec(
                name=fn.__name__,
                namespace=namespace,
                description=_clean_doc(fn.__doc__),
                input_model=hints["params"],
                output_model=hints["return"],
                fn=fn,
            )
            self.register(spec)
            return fn

        return decorator

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"duplicate tool name: {spec.name}")
        self._specs[spec.name] = spec

    def names(self) -> list[str]:
        return sorted(self._specs)

    def specs(self) -> list[ToolSpec]:
        return [self._specs[name] for name in self.names()]

    def get(self, name: str) -> ToolSpec:
        if name not in self._specs:
            raise KeyError(f"unknown tool: {name}")
        return self._specs[name]

    def _selected(
        self, namespaces: set[str] | None, names: set[str] | None
    ) -> list[ToolSpec]:
        specs = self.specs()
        if namespaces is not None:
            specs = [s for s in specs if s.namespace in namespaces]
        if names is not None:
            specs = [s for s in specs if s.name in names]
        return specs

    def anthropic_schemas(
        self, namespaces: set[str] | None = None, names: set[str] | None = None
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_model.model_json_schema(),
            }
            for spec in self._selected(namespaces, names)
        ]

    def subset(
        self, namespaces: set[str] | None = None, names: set[str] | None = None
    ) -> list[dict[str, Any]]:
        return self.anthropic_schemas(namespaces=namespaces, names=names)

    def dispatch(self, name: str, raw_input: dict[str, Any], store: Any) -> dict[str, Any]:
        spec = self.get(name)
        try:
            params = spec.input_model.model_validate(raw_input)
        except ValidationError as exc:
            detail = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in exc.errors()[:5]
            )
            return {
                "error": {
                    "code": "invalid_input",
                    "message": f"invalid input for {name}: {detail}",
                    "hint": "fix the field(s) named above and retry",
                    "example": _example_for(spec.input_model),
                }
            }
        try:
            out = spec.fn(params, store)
        except ToolError as exc:
            return exc.as_dict()
        return out.model_dump(mode="json")


REGISTRY = ToolRegistry()
tool = REGISTRY.tool
