"""Golden tests for the topology tools."""

from __future__ import annotations

from pathlib import Path

from sentinel.tools import topology, traces
from sentinel.tools.models import (
    CriticalPathInput,
    NoArgs,
    OnsetWindowInput,
    ServiceInput,
)
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
FAILURE = FixtureStore(ROOT / "fixtures" / "payment_failure_001" / "public")
UNREACHABLE = FixtureStore(ROOT / "fixtures" / "payment_unreachable_001" / "public")


def test_dependencies_of_checkout() -> None:
    out = topology.topology_dependencies(ServiceInput(service="checkout"), FAILURE)
    assert "frontend" in out.callers
    assert {"payment", "cart", "currency"} <= set(out.callees)


def test_blast_radius_payment() -> None:
    out = topology.topology_blast_radius(ServiceInput(service="payment"), FAILURE)
    assert {"checkout", "frontend"} <= set(out.upstream)  # both transitively call payment
    assert "payment" not in out.upstream  # excludes self
    assert out.downstream == []  # payment depends on nothing downstream in this demo


def test_critical_path_frontend_to_payment() -> None:
    out = topology.topology_critical_path(CriticalPathInput(target="payment"), FAILURE)
    assert out.found
    assert out.path == ["frontend", "checkout", "payment"]


def test_critical_path_unreachable_target() -> None:
    out = topology.topology_critical_path(CriticalPathInput(target="no-such-service"), FAILURE)
    assert not out.found
    assert out.path == []


def test_locate_origin_service_fault() -> None:
    out = topology.topology_locate_origin(NoArgs(), FAILURE)
    assert out.classification == "service"
    assert out.origin_service == "payment"
    assert out.terminal_has_server_error
    assert out.path[-1] == "payment"


def test_locate_origin_edge_fault() -> None:
    out = topology.topology_locate_origin(NoArgs(), UNREACHABLE)
    assert out.classification == "edge"
    assert out.origin_service is None
    assert not out.terminal_has_server_error  # payment never reached -> server spans clean
    assert out.path[-1] == "payment"


def test_compare_surfaces_new_error_edges_after_onset() -> None:
    onset = traces.traces_first_error_time(NoArgs(), FAILURE).second
    assert onset is not None
    out = topology.topology_compare(OnsetWindowInput(onset_second=onset), FAILURE)
    pairs = {(e.caller, e.callee) for e in out.new_error_edges}
    assert ("checkout", "payment") in pairs
    assert ("frontend", "checkout") in pairs
