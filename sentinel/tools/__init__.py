"""Telemetry tools that read only the public fixture and never touch ground truth.

Importing this package registers every tool into `sentinel.registry.REGISTRY`.
"""

from sentinel.registry import REGISTRY
from sentinel.tools import (
    changes,
    correlate,
    hypothesis,
    investigate,
    logs,
    metrics,
    report,
    topology,
    traces,
)
from sentinel.tools.store import FixtureStore, TelemetryStore

__all__ = [
    "REGISTRY",
    "FixtureStore",
    "TelemetryStore",
    "changes",
    "correlate",
    "hypothesis",
    "investigate",
    "logs",
    "metrics",
    "report",
    "topology",
    "traces",
]
