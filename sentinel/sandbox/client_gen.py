"""Generate a Python client the sandbox executes, from the typed registry.

Each registry tool becomes one staticmethod on a namespace class. The method
forwards its arguments to a module-level `_rpc(tool_name, args)` that the runtime
injects; the host answers over the socket. Optional arguments default to a
sentinel and are dropped if unset, so a tool's own default (for example
traces_find limit=50) is never clobbered by an explicit None.
"""

from __future__ import annotations

from collections import defaultdict

from sentinel.registry import REGISTRY, ToolSpec

CODE_TERMINALS: set[str] = {"report_root_cause", "report_finding", "report_change_verdict"}

_HEADER = '''\
class _Unset:
    __slots__ = ()
    def __repr__(self):
        return "_UNSET"

_UNSET = _Unset()
_rpc = None  # injected by the runtime before any user code runs
'''

_SCALARS = {str: "str", int: "int", float: "float", bool: "bool"}


def code_tool_specs() -> list[ToolSpec]:
    return [
        spec
        for spec in REGISTRY.specs()
        if spec.name not in CODE_TERMINALS and not spec.name.startswith("investigate_")
    ]


def _method_name(spec: ToolSpec) -> str:
    prefix = f"{spec.namespace}_"
    return spec.name[len(prefix):] if spec.name.startswith(prefix) else spec.name


def _split_fields(spec: ToolSpec) -> tuple[list[str], list[str]]:
    required, optional = [], []
    for name, field in spec.input_model.model_fields.items():
        (required if field.is_required() else optional).append(name)
    return required, optional


def _one_line(text: str) -> str:
    cleaned = " ".join((text or "").strip().split())
    first = cleaned.split(". ", 1)[0].strip() if cleaned else "SRE tool."
    return (first + ("." if not first.endswith(".") else "")).replace('"', "'")


def _type_label(annotation: object) -> str:
    if annotation in _SCALARS:
        return _SCALARS[annotation]
    text = str(annotation)
    if text.startswith("list"):
        return "list"
    if "| None" in text or "Optional" in text:
        inner = text.split(" | None")[0].replace("typing.Optional[", "").rstrip("]")
        return _SCALARS.get(inner, "str") + "?"  # best effort
    return "dict"


def generate_client_source(specs: list[ToolSpec]) -> str:
    by_ns: dict[str, list[ToolSpec]] = defaultdict(list)
    for spec in specs:
        by_ns[spec.namespace].append(spec)
    lines = [_HEADER]
    for ns in sorted(by_ns):
        lines.append(f"class {ns}:")
        for spec in sorted(by_ns[ns], key=lambda s: s.name):
            method = _method_name(spec)
            required, optional = _split_fields(spec)
            params = list(required) + [f"{name}=_UNSET" for name in optional]
            sig = ", ".join(params)
            kv = ", ".join(f'"{name}": {name}' for name in required + optional)
            lines.append("    @staticmethod")
            lines.append(f"    def {method}({sig}):")
            lines.append(f'        """{_one_line(spec.description)}"""')
            lines.append(f"        _a = {{{kv}}}")
            lines.append(
                f'        return _rpc("{spec.name}", '
                "{k: v for k, v in _a.items() if v is not _UNSET})"
            )
        lines.append("")
    return "\n".join(lines)


def generate_client_digest(specs: list[ToolSpec]) -> str:
    by_ns: dict[str, list[ToolSpec]] = defaultdict(list)
    for spec in specs:
        by_ns[spec.namespace].append(spec)
    out = ["You call this API from inside run_code. Available namespaces and methods:"]
    for ns in sorted(by_ns):
        out.append(f"\n{ns}:")
        for spec in sorted(by_ns[ns], key=lambda s: s.name):
            method = _method_name(spec)
            parts = []
            for name, field in spec.input_model.model_fields.items():
                label = _type_label(field.annotation)
                parts.append(name if field.is_required() else f"{name}={label}")
                if field.is_required():
                    parts[-1] = f"{name}: {label}"
            out.append(f"  {ns}.{method}({', '.join(parts)})  - {_one_line(spec.description)}")
    return "\n".join(out)
