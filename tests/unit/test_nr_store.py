"""NewRelicStore against a fake client: protocol behavior, hydration
pagination and dedup, local filtering, and time mapping."""

from sentinel.fixtures.schemas import DerivedAlert
from sentinel.newrelic.store import NewRelicStore

W = 1_751_700_000_000
END = W + 600_000  # 600 s window
NOW = END + 120_000  # eval runs after the window closed


def span(ts, trace, sid, parent=None, service="payment", status=None, kind="server", name="op"):
    d = {
        "timestamp": ts, "trace.id": trace, "id": sid, "parent.id": parent,
        "duration.ms": 1.0, "name": name, "service.name": service, "span.kind": kind,
    }
    if status:
        d["otel.status_code"] = status
    return d


class FakeClient:
    """Answers nrql() from a list of (substring, results) rules; records queries."""

    def __init__(self, rules):
        self.rules = rules
        self.queries = []

    def nrql(self, query):
        self.queries.append(query)
        for needle, results in self.rules:
            if callable(results):
                if needle in query:
                    return results(query)
            elif needle in query:
                return results
        return []


def make_store(rules, **kwargs):
    client = FakeClient(rules)
    alert = DerivedAlert(
        alertname="UserFacingDegradation", severity="critical", starts_at_second=300,
        value=0.05, expr="live", fingerprint="f1",
    )
    store = NewRelicStore(
        client, window_start_ms=W, window_end_ms=END, alerts=[alert],
        now_ms=lambda: NOW, **kwargs,
    )
    return store, client


def test_window_is_relative_seconds():
    store, _ = make_store([])
    w = store.window()
    assert (w.start, w.end) == (0, 600)


def test_alerts_returns_injected_alerts():
    store, _ = make_store([])
    assert store.alerts()[0].alertname == "UserFacingDegradation"


def test_list_services_parses_uniques():
    store, _ = make_store([("uniques(service.name", [{"uniques.service.name": ["cart", "payment"]}])])
    assert store.list_services() == ["cart", "payment"]


def test_list_metric_keys_advertises_only_supported_metrics():
    store, _ = make_store([("uniques(service.name", [{"uniques.service.name": ["payment"]}])])
    keys = store.list_metric_keys()
    assert ("payment", "latency_p95_ms", "ms") in keys
    assert ("payment", "request_error_rate", "ratio") in keys
    assert not any(m == "cpu_cores" for _, m, _ in keys)


def test_metric_series_maps_timeseries_and_unknown_metric_is_empty():
    results = [{"beginTimeSeconds": W // 1000 + 10, "endTimeSeconds": W // 1000 + 20, "p95": 55.0}]
    store, client = make_store([("percentile(duration.ms, 95)", results)])
    rows = store.metric_series("payment", "latency_p95_ms")
    assert [(r.time, r.value, r.unit) for r in rows] == [(10, 55.0, "ms")]
    assert store.metric_series("payment", "cpu_cores") == []


def test_hydration_dedups_and_serves_span_methods_once():
    rows = [
        span(W + 1000, "t1", "s1"),
        span(W + 1000, "t1", "s1"),  # duplicate export
        span(W + 2000, "t1", "s2", parent="s1", service="checkout", status="ERROR"),
    ]
    store, client = make_store([("FROM Span SINCE", rows)])
    assert len(store.all_spans()) == 2
    assert [c.span_id for c in store.children_of("s1")] == ["s2"]
    errors = store.find_spans(status="ERROR")
    assert [e.span_id for e in errors] == ["s2"]
    before = len([q for q in client.queries if "FROM Span SINCE" in q])
    store.all_spans()  # second call must not re-query
    after = len([q for q in client.queries if "FROM Span SINCE" in q])
    assert before == after


def test_hydration_splits_full_chunks():
    def answer(query):
        # parse SINCE a UNTIL b from the query text
        parts = query.split("SINCE ")[1].split(" UNTIL ")
        a, b = int(parts[0]), int(parts[1].split(" ")[0])
        if b - a > 15_000:
            return [span(a + i, f"t{a}", f"s{a}-{i}") for i in range(5000)]
        return [span(a + 1, f"t{a}", f"s{a}-small")]

    store, client = make_store([("FROM Span SINCE", answer)], chunk_ms=30_000)
    store.all_spans()
    span_queries = [q for q in client.queries if "FROM Span SINCE" in q]
    assert len(span_queries) > 20  # 20 base chunks, full ones split


def test_get_trace_queries_directly_without_hydration():
    store, client = make_store([("WHERE trace.id = 't9'", [span(W + 1000, "t9", "s1")])])
    trace = store.get_trace("t9")
    assert [s.span_id for s in trace] == ["s1"]
    assert not any("FROM Span SINCE" in q and "trace.id" not in q for q in client.queries)


def test_search_logs_applies_severity_and_limit_locally():
    logs = [
        {"timestamp": W + 1000, "message": "info msg", "severity.text": "INFO", "service.name": "cart"},
        {"timestamp": W + 2000, "message": "bad thing", "severity.text": "ERROR", "service.name": "cart"},
        {"timestamp": W + 3000, "message": "worse thing", "severity.text": "FATAL", "service.name": "cart"},
    ]
    store, _ = make_store([("FROM Log", logs)])
    out = store.search_logs(service="cart", severity_min="error", limit=1)
    assert [r.message for r in out] == ["bad thing"]


def test_list_changes_filters_by_service_and_window_relative_time():
    events = [
        {"timestamp": W + 300_000, "changeId": "chg_0003", "service": "payment",
         "kind": "runtime_config_change", "summary": "payment config changed",
         "diffTouches": "charge_handler"},
        {"timestamp": W + 200_000, "changeId": "chg_0001", "service": "frontend",
         "kind": "deploy", "summary": "frontend deploy", "diffTouches": "copy"},
    ]
    store, _ = make_store([("FROM SentinelChange", events)])
    all_changes = store.list_changes()
    assert [c.id for c in all_changes] == ["chg_0001", "chg_0003"]
    assert [c.id for c in store.list_changes(service="payment")] == ["chg_0003"]
    assert all_changes[1].time == 300


def test_satisfies_telemetry_store_protocol():
    store, _ = make_store([])
    for method in [
        "window", "alerts", "list_services", "list_metric_keys", "metric_series",
        "all_spans", "find_spans", "get_trace", "children_of", "callee_of",
        "search_logs", "logs_for_trace", "list_changes",
    ]:
        assert callable(getattr(store, method)), method


def test_metric_series_memory_mb_uses_container_metric():
    results = [{"beginTimeSeconds": W // 1000 + 20, "endTimeSeconds": W // 1000 + 30, "memory_mb": 512.0}]
    store, client = make_store([("container.memory.usage.total", results)])
    rows = store.metric_series("recommendation", "memory_mb")
    assert [(r.time, r.value, r.unit) for r in rows] == [(20, 512.0, "MB")]
    assert ("recommendation", "memory_mb", "MB") not in []  # placeholder for keys check below


def test_list_metric_keys_includes_memory():
    store, _ = make_store([("uniques(service.name", [{"uniques.service.name": ["recommendation"]}])])
    assert ("recommendation", "memory_mb", "MB") in store.list_metric_keys()


def test_store_stats_expose_hydration_volume():
    rows = [span(W + 1000, "t1", "s1"), span(W + 2000, "t1", "s2", parent="s1")]
    store, _ = make_store([("FROM Span SINCE", rows)])
    store.all_spans()
    assert store.stats["hydrated_spans"] == 2
    assert store.stats["hydration_pages"] >= 1
