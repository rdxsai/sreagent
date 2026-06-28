"""Layer 2: shape raw sandbox stdout for the model.

The execution layer (inside the sandbox) stays raw so the script can process
full tool results. Only the final stdout that crosses back to the model is
truncated and annotated here. Exit is 0 on a clean run, 1 when the script raised.
"""

from __future__ import annotations


def _duration(ms: int) -> str:
    return f"{ms}ms" if ms < 1000 else f"{ms / 1000:.1f}s"


def present(
    stdout: str,
    error: str | None,
    duration_ms: int,
    *,
    max_lines: int = 200,
    max_chars: int = 50_000,
) -> str:
    body = stdout or ""
    lines = body.splitlines()
    if len(lines) > max_lines or len(body) > max_chars:
        kept = "\n".join(lines[:max_lines])[:max_chars]
        body = (
            f"{kept}\n\n--- output truncated ({len(lines)} lines, {len(body)} chars) ---\n"
            "Re-run with a narrower print(): filter or slice before printing."
        )
    if not body.strip():
        body = "(no stdout; remember to print() the facts you need)"
    if error:
        body = f"{body}\n[stderr]\n{error.strip()}"
    exit_code = 1 if error else 0
    return f"{body}\n[exit:{exit_code} | {_duration(duration_ms)}]"
