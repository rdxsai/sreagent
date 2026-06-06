"""Raw telemetry recorder for the OpenTelemetry Demo lab."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from labs.otel.source import (
    JAEGER_API_BASE_PATH,
    OPENSEARCH_CONTAINER_NAME,
    OPENSEARCH_CONTAINER_PORT,
    docker_published_url,
    jaeger_query_base_url,
    prometheus_base_url,
)


def record_raw_telemetry(output_dir: Path) -> None:
    """Capture raw telemetry for a running scenario."""

    raise NotImplementedError("raw backend collection is not wired yet")


class RecorderError(RuntimeError):
    """Raised when a telemetry backend read fails."""


@dataclass(frozen=True)
class PrometheusClient:
    base_url: str
    timeout_seconds: float = 10.0

    @classmethod
    def from_environment(cls) -> PrometheusClient:
        return cls(prometheus_base_url())

    def query(self, query: str) -> dict[str, Any]:
        return _prometheus_data(
            _get_json(
                f"{self.base_url.rstrip('/')}/api/v1/query?{urlencode({'query': query})}",
                self.timeout_seconds,
            )
        )

    def query_range(
        self,
        query: str,
        *,
        start: float,
        end: float,
        step: str,
    ) -> dict[str, Any]:
        params = urlencode({"query": query, "start": start, "end": end, "step": step})
        return _prometheus_data(
            _get_json(
                f"{self.base_url.rstrip('/')}/api/v1/query_range?{params}",
                self.timeout_seconds,
            )
        )

    def label_values(self, label: str) -> list[str]:
        payload = _prometheus_data(
            _get_json(
                f"{self.base_url.rstrip('/')}/api/v1/label/{label}/values",
                self.timeout_seconds,
            )
        )
        if not isinstance(payload, list):
            raise RecorderError("Prometheus label values response was not a list")
        return [str(value) for value in payload]


@dataclass(frozen=True)
class JaegerClient:
    base_url: str
    timeout_seconds: float = 10.0

    @classmethod
    def from_environment(cls) -> JaegerClient:
        return cls(jaeger_query_base_url())

    @property
    def api_base_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{JAEGER_API_BASE_PATH}"

    def services(self) -> list[str]:
        payload = _get_json(f"{self.api_base_url}/services", self.timeout_seconds)
        data = payload.get("data")
        if not isinstance(data, list):
            raise RecorderError("Jaeger services response missing data list")
        return [str(service) for service in data]

    def traces(
        self,
        service: str,
        *,
        limit: int = 100,
        start_epoch_seconds: float | None = None,
        end_epoch_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        query_params: dict[str, str | int] = {"service": service, "limit": limit}
        if start_epoch_seconds is not None:
            query_params["start"] = int(start_epoch_seconds * 1_000_000)
        if end_epoch_seconds is not None:
            query_params["end"] = int(end_epoch_seconds * 1_000_000)
        params = urlencode(query_params)
        payload = _get_json(f"{self.api_base_url}/traces?{params}", self.timeout_seconds)
        data = payload.get("data")
        if not isinstance(data, list):
            raise RecorderError("Jaeger traces response missing data list")
        return [trace for trace in data if isinstance(trace, dict)]


@dataclass(frozen=True)
class OpenSearchClient:
    base_url: str
    timeout_seconds: float = 10.0

    @classmethod
    def from_environment(cls) -> OpenSearchClient:
        return cls(docker_published_url(OPENSEARCH_CONTAINER_NAME, OPENSEARCH_CONTAINER_PORT))

    def indices(self) -> list[str]:
        text = _get_text(f"{self.base_url.rstrip('/')}/_cat/indices?h=index", self.timeout_seconds)
        return [line.strip() for line in text.splitlines() if line.strip()]

    def search_logs(
        self,
        *,
        size: int = 100,
        start_epoch_seconds: float | None = None,
        end_epoch_seconds: float | None = None,
    ) -> list[dict[str, Any]]:
        if start_epoch_seconds is None and end_epoch_seconds is None:
            params = urlencode({"size": size})
            payload = _get_json(
                f"{self.base_url.rstrip('/')}/otel-logs*/_search?{params}",
                self.timeout_seconds,
            )
        else:
            payload = _post_json(
                f"{self.base_url.rstrip('/')}/otel-logs*/_search",
                _log_search_body(
                    size=size,
                    start_epoch_seconds=start_epoch_seconds,
                    end_epoch_seconds=end_epoch_seconds,
                ),
                self.timeout_seconds,
            )
        return opensearch_hits(payload)


def opensearch_hits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    hits = payload.get("hits")
    if not isinstance(hits, dict):
        raise RecorderError("OpenSearch response missing hits object")
    rows = hits.get("hits")
    if not isinstance(rows, list):
        raise RecorderError("OpenSearch response missing hits.hits list")
    return [row for row in rows if isinstance(row, dict)]


def _prometheus_data(payload: dict[str, Any]) -> Any:
    if payload.get("status") != "success":
        raise RecorderError("Prometheus response status was not success")
    return payload.get("data")


def _get_json(url: str, timeout_seconds: float) -> dict[str, Any]:
    text = _get_text(url, timeout_seconds)
    return _decode_json(text, url)


def _post_json(url: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            text = response.read().decode("utf-8")
    except (HTTPError, URLError, OSError) as exc:
        raise RecorderError(f"backend request failed for {url}: {exc}") from exc
    return _decode_json(text, url)


def _decode_json(text: str, url: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RecorderError(f"backend returned invalid JSON from {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RecorderError(f"backend returned non-object JSON from {url}")
    return payload


def _log_search_body(
    *,
    size: int,
    start_epoch_seconds: float | None,
    end_epoch_seconds: float | None,
) -> dict[str, Any]:
    bounds: dict[str, str] = {}
    if start_epoch_seconds is not None:
        bounds["gte"] = _iso_timestamp(start_epoch_seconds)
    if end_epoch_seconds is not None:
        bounds["lte"] = _iso_timestamp(end_epoch_seconds)
    return {
        "size": size,
        "sort": [{"observedTimestamp": {"order": "asc"}}],
        "query": {"range": {"observedTimestamp": bounds}},
    }


def _iso_timestamp(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _get_text(url: str, timeout_seconds: float) -> str:
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return response.read().decode("utf-8")
    except (HTTPError, URLError, OSError) as exc:
        raise RecorderError(f"backend request failed for {url}: {exc}") from exc
