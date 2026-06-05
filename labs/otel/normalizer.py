"""Normalize backend-specific telemetry into Sentinel public fixture schemas."""

from pathlib import Path


def normalize_raw_capture(raw_dir: Path, public_dir: Path) -> None:
    """Convert raw captures to public JSON and JSONL fixture files."""

    raise NotImplementedError("raw capture normalization is not wired yet")
