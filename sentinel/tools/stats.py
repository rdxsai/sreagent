"""The shared onset-step detector: one implementation for every layer.

Both the topology anomaly overlay (manager side) and the metric tools (worker
side) judge "did this series step at onset" -- when the two owned separate
copies they drifted, and the worker copy stayed level-based while live New
Relic exports integrating gauges whose faults are SLOPE changes (observed: an
ad cpu 'utilization' at level ~18 where a 64x busy-loop is a ramp invisible to
mean comparison). This module is the single copy.

Detection = robust effect size, not a ratio flag:
  z = (post_median - pre_median) / max(MAD_sigma, floors)
with per-family delta floors so near-zero noisy bases (live error rates)
cannot fire, a placebo onset-alignment test that DEMOTES drift and
contaminated baselines rather than erasing them, and a slope pass (the same
test over first differences) so sustained slope changes fire on integrating
series while single-bucket spikes do not move the median of diffs.
"""

from __future__ import annotations

Z_MIN = 3.0
_ALIGN_RATIO = 1.25   # onset z must beat 1.25x the max placebo z, else demoted
_ALIGN_DEMOTE = 0.25  # demotion factor for misaligned steps (contaminated baseline)

# per-metric (mad_eps, abs_delta_floor, rel_delta_floor); family fallback below
_FLOORS_BY_METRIC = {
    "request_error_rate": (0.002, 0.01, 0.5),
    "latency_p95_ms": (2.0, 15.0, 0.15),
    "latency_p50_ms": (1.0, 10.0, 0.15),
    "cpu_utilization": (0.01, 0.03, 0.15),
    "memory_mb": (5.0, 20.0, 0.10),
}
_FLOORS_BY_FAMILY = {
    "error": (0.002, 0.01, 0.5),
    "latency": (2.0, 15.0, 0.15),
    "resource": (1e-9, 0.0, 0.15),
}


def family(metric: str) -> str:
    m = metric.lower()
    if "latency" in m or "duration" in m:
        return "latency"
    if "error" in m:
        return "error"
    return "resource"


def _median(vals: list[float]) -> float:
    s = sorted(vals)
    n = len(s)
    return 0.0 if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)


def _mad_sigma(vals: list[float]) -> float:
    m = _median(vals)
    return 1.4826 * _median([abs(v - m) for v in vals])


def _level_z(pre: list[float], post: list[float], mad_eps: float, d_abs: float,
             d_rel: float) -> float:
    """Robust median-step effect size with floors and placebo onset-alignment."""
    if len(pre) < 4 or not post:
        return 0.0
    pre_med, post_med = _median(pre), _median(post)
    delta = post_med - pre_med
    if delta <= 0:
        return 0.0
    if not (delta >= d_abs or (pre_med > 0 and delta >= d_rel * pre_med)):
        return 0.0
    denom = max(_mad_sigma(pre), mad_eps, 0.01 * abs(pre_med))
    z = delta / denom
    # onset alignment: a step equally present at placebo splits inside the baseline is
    # drift/noise (or a contaminated baseline) -- demote it rather than erase it.
    third = len(pre) // 3
    placebo = 0.0
    for cut in (third, 2 * third):
        if 4 <= cut <= len(pre) - 4:
            p_pre, p_post = pre[:cut], pre[cut:]
            p_delta = _median(p_post) - _median(p_pre)
            p_denom = max(_mad_sigma(p_pre), mad_eps, 0.01 * abs(_median(p_pre)))
            placebo = max(placebo, abs(p_delta) / p_denom)
    if placebo and z <= _ALIGN_RATIO * placebo:
        z *= _ALIGN_DEMOTE
    return z


def _diffs(vals: list[float]) -> list[float]:
    return [b - a for a, b in zip(vals, vals[1:])]


def step_z(pre: list[float], post: list[float], metric: str) -> float:
    """Onset effect size = max of a LEVEL step and a SLOPE step.

    Instantaneous series (latency, error rate, cpu ratio) step in level. But some
    live backends export integrating/cumulative gauges where a fault is a slope
    change, invisible to a level test whose denominator floor scales with the
    level. A slope change is a level step in the first differences, and the
    median of diffs only moves when the new slope is SUSTAINED, so the same
    machinery applies with tighter floors.
    """
    mad_eps, d_abs, d_rel = _FLOORS_BY_METRIC.get(metric) or _FLOORS_BY_FAMILY[family(metric)]
    z = _level_z(pre, post, mad_eps, d_abs, d_rel)
    if len(pre) >= 6 and len(post) >= 3:
        z = max(z, _level_z(_diffs(pre), _diffs(post), mad_eps / 10, 0.0, 0.0))
    return z
