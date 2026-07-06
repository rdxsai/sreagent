"""NerdGraph client behavior against a mock transport: happy path, GraphQL
errors, retry on 429, and the TTL cache."""

import json

import httpx
import pytest

from sentinel.newrelic.client import BackendQueryError, NerdGraphClient, post_custom_events


def _graphql_ok(results):
    return {"data": {"actor": {"account": {"nrql": {"results": results}}}}}


def make_client(handler, **kwargs):
    return NerdGraphClient(
        8250926, "NRAK-test", transport=httpx.MockTransport(handler), rps=0, **kwargs
    )


def test_nrql_returns_results_and_sends_key_and_variables():
    seen = {}

    def handler(request):
        seen["api_key"] = request.headers["API-Key"]
        body = json.loads(request.content)
        seen["variables"] = body["variables"]
        return httpx.Response(200, json=_graphql_ok([{"count": 3}]))

    client = make_client(handler)
    assert client.nrql("SELECT count(*) FROM Span") == [{"count": 3}]
    assert seen["api_key"] == "NRAK-test"
    assert seen["variables"] == {"accountId": 8250926, "nrql": "SELECT count(*) FROM Span"}


def test_nrql_raises_backend_error_on_graphql_errors():
    def handler(request):
        return httpx.Response(200, json={"errors": [{"message": "boom"}]})

    with pytest.raises(BackendQueryError, match="boom"):
        make_client(handler).nrql("SELECT 1")


def test_nrql_retries_429_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={})
        return httpx.Response(200, json=_graphql_ok([{"ok": 1}]))

    client = make_client(handler, retry_wait_s=0)
    assert client.nrql("SELECT 1") == [{"ok": 1}]
    assert calls["n"] == 2


def test_nrql_caches_identical_queries():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=_graphql_ok([{"v": calls["n"]}]))

    client = make_client(handler)
    assert client.nrql("SELECT 1") == [{"v": 1}]
    assert client.nrql("SELECT 1") == [{"v": 1}]  # served from cache
    assert calls["n"] == 1


def test_post_custom_events_sends_license_key_and_batch():
    seen = {}

    def handler(request):
        seen["key"] = request.headers["Api-Key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True})

    events = [{"eventType": "SentinelChange", "changeId": "chg_1", "timestamp": 1}]
    post_custom_events(8250926, "lic", events, transport=httpx.MockTransport(handler))
    assert seen["key"] == "lic"
    assert seen["body"] == events


def test_client_stats_and_query_log_track_queries_cache_and_latency():
    def handler(request):
        return httpx.Response(200, json=_graphql_ok([{"v": 1}]))

    client = make_client(handler)
    client.nrql("SELECT 1")
    client.nrql("SELECT 1")  # cache hit
    client.nrql("SELECT 2")
    assert client.stats["queries"] == 2
    assert client.stats["cache_hits"] == 1
    assert len(client.query_log) == 3
    live, cached = client.query_log[0], client.query_log[1]
    assert live["cached"] is False and cached["cached"] is True
    assert live["query"] == "SELECT 1" and live["ms"] >= 0


def test_client_stats_count_retries():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={})
        return httpx.Response(200, json=_graphql_ok([{"ok": 1}]))

    client = make_client(handler, retry_wait_s=0)
    client.nrql("SELECT 1")
    assert client.stats["retries"] == 1
