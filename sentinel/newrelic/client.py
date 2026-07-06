"""NerdGraph client: rate-limited, retrying, TTL-cached NRQL execution.

One shared client serves the whole store. Proactive throttle via the existing
token-bucket RateLimiter; reactive retry with exponential backoff on 429/5xx
and transport errors. NRQL text travels as a GraphQL variable so no NRQL
escaping can break the GraphQL document.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from sentinel.agent.ratelimit import RateLimiter

_GRAPHQL = (
    "query($accountId: Int!, $nrql: Nrql!) { actor { account(id: $accountId) "
    "{ nrql(query: $nrql, timeout: 60) { results } } } }"
)
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class BackendQueryError(RuntimeError):
    """A NerdGraph call failed after retries or returned GraphQL errors."""


def load_env_var(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


class NerdGraphClient:
    def __init__(
        self,
        account_id: int,
        api_key: str,
        *,
        endpoint: str = "https://api.newrelic.com/graphql",
        rps: float = 5.0,
        timeout_s: int = 60,
        cache_ttl_s: float = 120.0,
        retry_wait_s: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._account_id = account_id
        self._api_key = api_key
        self._endpoint = endpoint
        self._limiter = RateLimiter(rps, burst=max(1, int(rps)))
        self._http = httpx.Client(timeout=timeout_s + 10, transport=transport)
        self._cache_ttl = cache_ttl_s
        self._retry_wait = retry_wait_s
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> NerdGraphClient:
        key = load_env_var("NEW_RELIC_USER_KEY")
        account = load_env_var("NEW_RELIC_ACCOUNT_ID")
        if not key or not account:
            raise BackendQueryError(
                "NEW_RELIC_USER_KEY and NEW_RELIC_ACCOUNT_ID must be set (env or .env)"
            )
        return cls(int(account), key)

    def nrql(self, query: str) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(query)
            if hit and now - hit[0] < self._cache_ttl:
                return hit[1]
        results = self._execute(query)
        with self._lock:
            self._cache[query] = (time.monotonic(), results)
        return results

    def _execute(self, query: str) -> list[dict]:
        @retry(
            retry=retry_if_exception(_retryable),
            wait=wait_exponential(multiplier=self._retry_wait, max=30),
            stop=stop_after_attempt(5),
            reraise=True,
        )
        def _post() -> list[dict]:
            self._limiter.acquire()
            response = self._http.post(
                self._endpoint,
                json={
                    "query": _GRAPHQL,
                    "variables": {"accountId": self._account_id, "nrql": query},
                },
                headers={"API-Key": self._api_key, "Content-Type": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                messages = "; ".join(e.get("message", "") for e in payload["errors"])
                raise BackendQueryError(f"NerdGraph error: {messages}")
            results = payload["data"]["actor"]["account"]["nrql"]["results"]
            return results if isinstance(results, list) else []

        try:
            return _post()
        except httpx.HTTPError as exc:
            raise BackendQueryError(f"NerdGraph request failed: {exc}") from exc


def post_custom_events(
    account_id: int,
    license_key: str,
    events: list[dict],
    *,
    transport: httpx.BaseTransport | None = None,
) -> None:
    url = f"https://insights-collector.newrelic.com/v1/accounts/{account_id}/events"
    with httpx.Client(timeout=30, transport=transport) as http:
        response = http.post(url, json=events, headers={"Api-Key": license_key})
        response.raise_for_status()
