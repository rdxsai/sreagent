"""Structured logging and run tracing for the agent.

A thin wrapper over structlog so the agent runtime emits machine-readable events
(tool calls, subagent spawns, run start/stop) tagged with the agent id, which is
how you debug "why didn't it find the obvious thing." Console renderer by default,
JSON when SENTINEL_LOG_JSON=1; level from SENTINEL_LOG_LEVEL (default WARNING, so
tests stay quiet and an operator opts in by raising it).
"""

from __future__ import annotations

import logging
import os
import sys

import structlog

_configured = False


def configure(level: str | None = None, json_logs: bool | None = None) -> None:
    global _configured
    resolved = (level or os.environ.get("SENTINEL_LOG_LEVEL", "WARNING")).upper()
    use_json = (os.environ.get("SENTINEL_LOG_JSON", "0") == "1") if json_logs is None else json_logs
    renderer: object = (
        structlog.processors.JSONRenderer()
        if use_json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, resolved, logging.WARNING)),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str = "sentinel"):
    if not _configured:
        configure()
    return structlog.get_logger(name)
