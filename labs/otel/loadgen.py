"""Self-contained load driver for scenario recording.

The demo's Locust load generator proved unreliable on this host (dynamic web-UI
port, crashes under load, and a wedged state generating ~0 req/s), so we drive
traffic ourselves through the frontend API. This is crash-proof (it's our own
process) and gives both signals we need:

- browse workers hit the homepage, product, and recommendation endpoints, the
  volume the latency faults (recommendation, ad) need to surface;
- checkout workers run full add-to-cart + checkout journeys, the checkout/payment
  traffic the payment faults need.

It also activates productCatalogFailure, which ships gated off by a no-op
targeting rule that returns the 'off' variant in both branches.
"""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from labs.otel.flagd import FlagdClient

_FRONTEND = "http://localhost:18080"
_CHECKOUT_BODY = {
    "email": "load@example.com",
    "address": {
        "streetAddress": "1600 Amphitheatre Parkway",
        "city": "Mountain View",
        "state": "CA",
        "country": "United States",
        "zipCode": "94043",
    },
    "userCurrency": "USD",
    "creditCard": {
        "creditCardNumber": "4432-8015-6152-0454",
        "creditCardCvv": 672,
        "creditCardExpirationYear": 2030,
        "creditCardExpirationMonth": 1,
    },
}


def _get(path: str) -> bytes | None:
    try:
        return urllib.request.urlopen(_FRONTEND + path, timeout=8).read()
    except Exception:
        return None


def _post(path: str, body: dict[str, Any]) -> None:
    req = urllib.request.Request(
        _FRONTEND + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except urllib.error.HTTPError:
        pass  # a 5xx under a fault is expected; the erroring span is what we record
    except Exception:
        pass


def _product_ids() -> list[str]:
    body = _get("/api/products")
    if not body:
        return ["OLJCESPC7Z", "0PUK6V6EV0"]
    try:
        return [p["id"] for p in json.loads(body)] or ["OLJCESPC7Z"]
    except Exception:
        return ["OLJCESPC7Z"]


class TrafficDriver:
    """Browse + checkout traffic from worker threads, independent of Locust."""

    def __init__(
        self,
        browse_threads: int = 6,
        checkout_threads: int = 3,
        recommend_threads: int = 0,
        browse_delay_seconds: float = 0.05,
        checkout_delay_seconds: float = 1.0,
    ) -> None:
        self._browse_threads = browse_threads
        self._checkout_threads = checkout_threads
        self._recommend_threads = recommend_threads
        self._browse_delay = browse_delay_seconds
        self._checkout_delay = checkout_delay_seconds
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []
        self._pids = ["OLJCESPC7Z"]
        self._counter = 0
        self._lock = threading.Lock()

    def _next_session(self) -> str:
        with self._lock:
            self._counter += 1
            return f"load-{self._counter}"

    def _browse_path(self, i: int) -> str:
        # Diverse product/recommendation requests matter: the recommendation cache
        # fault only builds latency when varied product ids force cache growth, so
        # we rotate ids rather than hammer one product (which would just cache-hit).
        choice = i % 5
        if choice == 0:
            return "/"
        if choice == 1:
            return "/api/products"
        if choice == 2:
            ids = ",".join(random.sample(self._pids, min(3, len(self._pids))))
            return f"/api/recommendations?productIds={ids}&sessionId=s{i}&currencyCode=USD"
        if choice == 3:
            return f"/api/products/{random.choice(self._pids)}"
        return "/api/data?contextKeys=telescopes"

    def _browse(self) -> None:
        i = 0
        while not self._stop.is_set():
            _get(self._browse_path(i))
            i += 1
            time.sleep(self._browse_delay)

    def _checkout(self) -> None:
        i = 0
        while not self._stop.is_set():
            sess = self._next_session()
            pid = self._pids[i % len(self._pids)]
            _get(f"/api/products/{pid}")
            _post(f"/api/cart?sessionId={sess}&currencyCode=USD", {"item": {"productId": pid, "quantity": 1}, "userId": sess})
            _post(f"/api/checkout?sessionId={sess}&currencyCode=USD", {**_CHECKOUT_BODY, "userId": sess})
            i += 1
            time.sleep(self._checkout_delay)

    def _recommend(self) -> None:
        # Dedicated diverse-product recommendation volume. The recommendation cache
        # fault is a leak that builds with request count over varied products, so it
        # needs sustained diverse traffic to surface latency within a recording window.
        i = 0
        while not self._stop.is_set():
            ids = ",".join(random.sample(self._pids, min(3, len(self._pids))))
            _get(f"/api/recommendations?productIds={ids}&sessionId=r{i}&currencyCode=USD")
            i += 1
            time.sleep(self._browse_delay)

    def start(self) -> None:
        self._pids = _product_ids()
        for _ in range(self._browse_threads):
            t = threading.Thread(target=self._browse, daemon=True)
            t.start()
            self._workers.append(t)
        for _ in range(self._checkout_threads):
            t = threading.Thread(target=self._checkout, daemon=True)
            t.start()
            self._workers.append(t)
        for _ in range(self._recommend_threads):
            t = threading.Thread(target=self._recommend, daemon=True)
            t.start()
            self._workers.append(t)

    def stop(self) -> None:
        self._stop.set()


def remove_product_catalog_targeting(fc: FlagdClient) -> Any:
    """Strip productCatalogFailure's no-op targeting so the flag can actually fire.

    Returns the saved targeting block (or None) for restoration. Leaves the flag at
    defaultVariant=off; the recorder flips it on at injection time.
    """
    doc = fc.read()
    flag = doc.get("flags", {}).get("productCatalogFailure")
    if not flag or "targeting" not in flag:
        return None
    saved = flag.get("targeting")
    flag.pop("targeting", None)
    flag["defaultVariant"] = "off"
    fc.write(doc)
    return saved


def restore_product_catalog_targeting(fc: FlagdClient, saved: Any) -> None:
    if saved is None:
        return
    doc = fc.read()
    flag = doc.get("flags", {}).get("productCatalogFailure")
    if flag is not None:
        flag["targeting"] = saved
        flag["defaultVariant"] = "off"
        fc.write(doc)
