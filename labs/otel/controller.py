"""Scenario control entrypoints for the OpenTelemetry Demo lab."""

from pathlib import Path


def record_scenario(config_path: Path, output_dir: Path) -> None:
    """Record one scenario from a scenario config file.

    Live control is intentionally not wired until the OpenTelemetry Demo SHA,
    flagd API, workload command, and telemetry backends are verified.
    """

    raise NotImplementedError("live OpenTelemetry Demo control is not wired yet")
