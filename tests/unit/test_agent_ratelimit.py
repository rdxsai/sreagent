"""Tests for the token-bucket rate limiter (deterministic via injected clock/sleep)."""

from __future__ import annotations

from sentinel.agent.ratelimit import RateLimiter


def test_disabled_rate_is_noop() -> None:
    sleeps: list[float] = []
    rl = RateLimiter(0, 5, clock=lambda: 0.0, sleep=sleeps.append)
    for _ in range(10):
        rl.acquire()
    assert sleeps == []


def test_burst_then_throttles() -> None:
    t = [0.0]
    sleeps: list[float] = []

    def fake_sleep(w: float) -> None:
        sleeps.append(w)
        t[0] += w  # advance the clock so tokens refill

    rl = RateLimiter(rate_per_sec=2.0, burst=3, clock=lambda: t[0], sleep=fake_sleep)
    for _ in range(3):  # burst capacity: no waiting
        rl.acquire()
    assert sleeps == []
    rl.acquire()  # 4th call: bucket empty, must wait ~ 1 token / 2 per sec = 0.5s
    assert len(sleeps) == 1 and abs(sleeps[0] - 0.5) < 1e-6
