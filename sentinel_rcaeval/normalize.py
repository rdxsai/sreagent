"""Window, re-base, and normalize RCAEval telemetry into Sentinel row schemas."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Window:
    inject_time: int
    start_abs: int
    end_abs: int

    @property
    def span_seconds(self) -> int:
        return self.end_abs - self.start_abs

    @property
    def onset_second(self) -> int:
        return self.inject_time - self.start_abs


def make_window(inject_time: int, pre: int = 180, post: int = 300) -> Window:
    return Window(inject_time=inject_time, start_abs=inject_time - pre, end_abs=inject_time + post)


def rebase(t_abs: int, window: Window) -> int:
    return t_abs - window.start_abs


def in_window(t_abs: int, window: Window) -> bool:
    return window.start_abs <= t_abs <= window.end_abs
