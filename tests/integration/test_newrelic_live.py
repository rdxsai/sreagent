"""Round-trip against the real New Relic account. Skipped without keys."""

import pytest

from sentinel.newrelic.client import NerdGraphClient, load_env_var

pytestmark = pytest.mark.skipif(
    not (load_env_var("NEW_RELIC_USER_KEY") and load_env_var("NEW_RELIC_ACCOUNT_ID")),
    reason="New Relic credentials not configured",
)


def test_nrql_round_trip():
    client = NerdGraphClient.from_env()
    results = client.nrql("SELECT count(*) FROM Metric SINCE 1 hour ago")
    assert isinstance(results, list) and results


def test_span_page_shape_when_demo_streaming():
    client = NerdGraphClient.from_env()
    rows = client.nrql(
        "SELECT timestamp, trace.id, id, name, service.name FROM Span SINCE 30 minutes ago LIMIT 10"
    )
    if not rows:
        pytest.skip("no spans in the account; demo not streaming yet")
    assert {"timestamp", "trace.id", "id"} <= set(rows[0])
