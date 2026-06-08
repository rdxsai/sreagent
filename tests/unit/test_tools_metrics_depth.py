"""Golden tests for the metrics depth tools."""

from __future__ import annotations

from pathlib import Path

from sentinel.tools import metrics
from sentinel.tools.models import (
    ErrorBudgetInput,
    SaturationInput,
    SummaryAllInput,
    TopMoversInput,
)
from sentinel.tools.store import FixtureStore

ROOT = Path(__file__).resolve().parents[2]
FAILURE = FixtureStore(ROOT / "fixtures" / "payment_failure_001" / "public")
AD_CPU = FixtureStore(ROOT / "fixtures" / "ad_high_cpu_001" / "public")


def test_top_movers_surfaces_error_path() -> None:
    out = metrics.metrics_top_movers(
        TopMoversInput(metric="request_error_rate", onset_second=330), FAILURE
    )
    assert out.movers
    assert out.movers[0].delta > 0
    assert out.movers[0].service in {"payment", "checkout", "frontend"}


def test_resource_saturation_flags_ad_cpu() -> None:
    out = metrics.metrics_resource_saturation(SaturationInput(onset_second=330, cores_threshold=2.0), AD_CPU)
    assert any(c.service == "ad" and c.post_cores > 2.0 for c in out.saturated)


def test_resource_saturation_clean_on_payment_failure() -> None:
    out = metrics.metrics_resource_saturation(SaturationInput(onset_second=330, cores_threshold=2.0), FAILURE)
    assert out.saturated == []  # no CPU saturation in a payment error fault


def test_error_budget_breached_on_checkout() -> None:
    out = metrics.metrics_error_budget(ErrorBudgetInput(service="checkout", slo_error_rate=0.01), FAILURE)
    assert out.breached
    assert out.budget_burn > 1.0


def test_summary_all_sorted_by_error_rate() -> None:
    out = metrics.metrics_summary_all(SummaryAllInput(onset_second=330), FAILURE)
    assert out.services
    rates = [s.error_rate for s in out.services]
    assert rates == sorted(rates, reverse=True)
    assert out.services[0].error_rate > 0
