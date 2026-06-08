"""Typed exception hierarchy for Sentinel.

Tool errors are returned to the model as structured payloads, not raised as
opaque tracebacks. A `ToolInputError` carries an actionable hint and a correct
input example so the agent can fix a bad call on its own.
"""

from __future__ import annotations

from typing import Any


class SentinelError(Exception):
    """Base class for all Sentinel errors."""


class ToolError(SentinelError):
    """Base class for errors surfaced to the agent as structured tool results."""

    code: str = "tool_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
        example: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.hint = hint
        self.example = example

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "hint": self.hint,
                "example": self.example,
            }
        }


class ToolInputError(ToolError):
    """Raised by a tool when its input is semantically invalid (e.g. unknown service)."""

    code = "invalid_input"
