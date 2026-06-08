"""Thread-safe token-bucket rate limiter for external API calls.

The manager and its parallel investigator threads all funnel their Anthropic calls
through one shared limiter, so the fan-out cannot burst past a configured
requests-per-second ceiling. This is the proactive complement to the reactive
tenacity backoff on 429s in loop.py. The clock and sleep are injectable so the
behaviour is testable without real time.
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class RateLimiter:
    def __init__(
        self,
        rate_per_sec: float,
        burst: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._rate = rate_per_sec
        self._capacity = max(1, burst)
        self._tokens = float(self._capacity)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self._rate <= 0:
            return  # disabled
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            self._sleep(wait)
