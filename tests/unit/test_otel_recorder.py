from __future__ import annotations

import pytest

from labs.otel.recorder import RecorderError, opensearch_hits


def test_opensearch_hits_extracts_hit_rows() -> None:
    payload = {
        "hits": {
            "hits": [
                {"_index": "otel-logs-2026-06-05", "_source": {"body": "hello"}},
                {"_index": "otel-logs-2026-06-05", "_source": {"body": "world"}},
            ]
        }
    }

    assert [hit["_source"]["body"] for hit in opensearch_hits(payload)] == ["hello", "world"]


def test_opensearch_hits_rejects_missing_hit_list() -> None:
    with pytest.raises(RecorderError):
        opensearch_hits({"hits": {}})
