"""Metric tools over the two recorded RED series (request_error_rate, latency_p95_ms).

These corroborate a trace-based hypothesis with the rate/latency signal and pin
the time of a level shift. They do not drive node-vs-edge attribution; that is
trace-based (see the traces tools).
"""

from __future__ import annotations

from statistics import fmean

from sentinel.errors import ToolInputError
from sentinel.tools.models import (
    CompareBaselineInput,
    CompareBaselineOutput,
    DetectShiftInput,
    DetectShiftOutput,
    ListSeriesOutput,
    MetricSeriesInput,
    NoArgs,
    Series,
    SeriesKey,
    SeriesPoint,
)
from sentinel.registry import tool
from sentinel.tools.store import TelemetryStore


def _unit_for(store: TelemetryStore, service: str, metric: str) -> str:
    for s, m, u in store.list_metric_keys():
        if s == service and m == metric:
            return u
    available = sorted({m for _s, m, _u in store.list_metric_keys()})
    services = sorted({s for s, _m, _u in store.list_metric_keys()})
    raise ToolInputError(
        message=f"no series for service={service!r} metric={metric!r}",
        hint="call metrics_list_series first; metric must be one of the available names",
        example={"service": services[0] if services else "payment", "metric": available[0] if available else "request_error_rate"},
    )


@tool(namespace="metrics")
def metrics_list_series(params: NoArgs, store: TelemetryStore) -> ListSeriesOutput:
    """List the available metric series as (service, metric, unit) triples.

    Use this to discover what is queryable before calling metrics_series. The
    demo exposes request_error_rate (ratio) and latency_p95_ms (ms) per service.
    """
    return ListSeriesOutput(
        series=[SeriesKey(service=s, metric=m, unit=u) for s, m, u in store.list_metric_keys()]
    )


@tool(namespace="metrics")
def metrics_series(params: MetricSeriesInput, store: TelemetryStore) -> Series:
    """Fetch one metric series for a service over an optional time window.

    Returns a summary (min/max/mean/last/count) over the full window plus a
    point sample capped at max_points. Use it to confirm a service's error rate
    or p95 latency rose after onset.
    """
    unit = _unit_for(store, params.service, params.metric)
    rows = store.metric_series(params.service, params.metric)
    rows = [
        r
        for r in rows
        if (params.start is None or r.time >= params.start)
        and (params.end is None or r.time <= params.end)
    ]
    values = [r.value for r in rows]
    summary = {
        "min": min(values) if values else 0.0,
        "max": max(values) if values else 0.0,
        "mean": fmean(values) if values else 0.0,
        "last": values[-1] if values else 0.0,
        "count": float(len(values)),
    }
    truncated = len(rows) > params.max_points
    if truncated:
        step = len(rows) / params.max_points
        sampled = [rows[min(len(rows) - 1, int(i * step))] for i in range(params.max_points)]
    else:
        sampled = rows
    return Series(
        service=params.service,
        metric=params.metric,
        unit=unit,
        points=[SeriesPoint(time=r.time, value=r.value) for r in sampled],
        summary=summary,
        truncated=truncated,
        note=(
            f"sampled {len(sampled)} of {len(rows)} points; summary covers all points"
            if truncated
            else None
        ),
    )


@tool(namespace="metrics")
def metrics_compare_baseline(params: CompareBaselineInput, store: TelemetryStore) -> CompareBaselineOutput:
    """Compare a metric's mean in a baseline window against a later window.

    Use it to quantify a post-onset shift: a pre-onset baseline window versus a
    post-onset compare window. `shifted` is true when the change exceeds ten
    percent of the baseline (with a floor, so a rise from a zero baseline counts).
    """
    _unit_for(store, params.service, params.metric)
    rows = store.metric_series(params.service, params.metric)

    def mean_in(lo: int, hi: int) -> float:
        vals = [r.value for r in rows if lo <= r.time <= hi]
        return fmean(vals) if vals else 0.0

    baseline = mean_in(params.baseline_start, params.baseline_end)
    compare = mean_in(params.compare_start, params.compare_end)
    delta = compare - baseline
    pct = (delta / baseline) if baseline else None
    shifted = abs(delta) > (0.1 * abs(baseline) + 1e-6)
    return CompareBaselineOutput(
        baseline_mean=baseline,
        compare_mean=compare,
        delta=delta,
        pct_change=pct,
        shifted=shifted,
    )


@tool(namespace="metrics")
def metrics_detect_shift(params: DetectShiftInput, store: TelemetryStore) -> DetectShiftOutput:
    """Find the time of the largest level shift in a metric series.

    Scans split points and returns the time where the mean before and after
    differ most, with the before/after means and the magnitude. Useful for
    pinning when a service's error rate or latency stepped up.
    """
    _unit_for(store, params.service, params.metric)
    rows = store.metric_series(params.service, params.metric)
    values = [r.value for r in rows]
    if len(rows) < 2:
        return DetectShiftOutput(
            shift_second=None,
            before_mean=fmean(values) if values else 0.0,
            after_mean=fmean(values) if values else 0.0,
            magnitude=0.0,
        )
    best: tuple[float, int, float, float] | None = None
    for i in range(1, len(rows)):
        before = fmean(values[:i])
        after = fmean(values[i:])
        mag = abs(after - before)
        if best is None or mag > best[0]:
            best = (mag, rows[i].time, before, after)
    magnitude, second, before_mean, after_mean = best
    return DetectShiftOutput(
        shift_second=second,
        before_mean=before_mean,
        after_mean=after_mean,
        magnitude=magnitude,
    )
