"""Fault control extracted from run_sockshop_live: inject/clear via an injected
subprocess runner so no docker is touched."""
from __future__ import annotations

import pytest

from labs.sockshop.faults import APP_SERVICES, VETTED_TARGETS, clear_cpu, inject_cpu, sweep_all


class RecordingRun:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))

        class _Done:
            returncode = 0

        return _Done()


def test_inject_cpu_spawns_one_detached_hog_per_count() -> None:
    run = RecordingRun()
    inject_cpu("shipping", hogs=3, run=run)
    assert len(run.calls) == 3
    for cmd in run.calls:
        assert cmd == ["docker", "exec", "-d", "shipping", "sh", "-c", "yes >/dev/null 2>&1"]


def test_clear_cpu_sweeps_with_pkill_three_times() -> None:
    run = RecordingRun()
    clear_cpu("orders", run=run)
    assert run.calls == [["docker", "exec", "orders", "pkill", "yes"]] * 3


def test_inject_rejects_unvetted_target() -> None:
    with pytest.raises(ValueError, match="carts-db"):
        inject_cpu("carts-db", run=RecordingRun())


def test_sweep_all_clears_every_vetted_target() -> None:
    run = RecordingRun()
    sweep_all(run=run)
    swept = {cmd[2] for cmd in run.calls}
    assert swept == set(VETTED_TARGETS)


def test_app_services_is_the_thirteen_node_lab() -> None:
    assert len(APP_SERVICES) == 13
    assert "otel-collector" not in APP_SERVICES
    assert "load-test" not in APP_SERVICES
    for t in VETTED_TARGETS:
        assert t in APP_SERVICES
