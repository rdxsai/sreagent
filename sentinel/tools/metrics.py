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
    ErrorBudgetInput,
    ErrorBudgetOutput,
    ListSeriesOutput,
    MetricMover,
    MetricSeriesInput,
    NoArgs,
    SaturationInput,
    SaturationOutput,
    Series,
    SeriesKey,
    SeriesPoint,
    ServiceCpu,
    ServiceSnapshot,
    SummaryAllInput,
    SummaryAllOutput,
    TopMoversInput,
    TopMoversOutput,
)
from sentinel.registry import tool
from sentinel.tools.store import TelemetryStore


def _pre_post(store: TelemetryStore, service: str, metric: str, onset: int) -> tuple[float, float]:
    rows = store.metric_series(service, metric)
    pre = [r.value for r in rows if r.time < onset]
    post = [r.value for r in rows if r.time >= onset]
    return (fmean(pre) if pre else 0.0, fmean(post) if post else 0.0)


def _services_with(store: TelemetryStore, metric: str) -> list[str]:
    return sorted({s for s, m, _u in store.list_metric_keys() if m == metric})


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


@tool(namespace="metrics")
def metrics_top_movers(params: TopMoversInput, store: TelemetryStore) -> TopMoversOutput:
    """Rank services by how much one metric changed across onset.

    For the chosen metric (request_error_rate, latency_p95_ms, or cpu_cores),
    returns every service's pre vs post mean and the delta, largest increase first.
    One call surfaces which service moved most, the likely fault site, instead of
    querying each service's series individually.
    """
    movers = []
    for service in _services_with(store, params.metric):
        pre, post = _pre_post(store, service, params.metric, params.onset_second)
        movers.append(MetricMover(service=service, pre_mean=pre, post_mean=post, delta=post - pre))
    movers.sort(key=lambda m: m.delta, reverse=True)
    return TopMoversOutput(metric=params.metric, movers=movers[: params.limit])


@tool(namespace="metrics")
def metrics_resource_saturation(params: SaturationInput, store: TelemetryStore) -> SaturationOutput:
    """Find services whose CPU is saturated (cores in use above a threshold).

    Uses cpu_cores (rate of the cpu-usage counter, reliable here unlike the
    utilization gauge). Catches a CPU-saturation fault whose request latency may
    stay normal, so traces/latency tools would miss it. Default threshold 2 cores;
    baseline app services sit near 0.05.
    """
    saturated = []
    for service in _services_with(store, "cpu_cores"):
        pre, post = _pre_post(store, service, "cpu_cores", params.onset_second)
        if post > params.cores_threshold:
            saturated.append(ServiceCpu(service=service, pre_cores=pre, post_cores=post))
    saturated.sort(key=lambda c: c.post_cores, reverse=True)
    return SaturationOutput(saturated=saturated)


@tool(namespace="metrics")
def metrics_error_budget(params: ErrorBudgetInput, store: TelemetryStore) -> ErrorBudgetOutput:
    """Check a service's error rate against an SLO and report budget burn.

    Returns the observed mean error rate over the window, the SLO, the burn ratio
    (observed / SLO), and whether it breached. Use it to frame how far a user-facing
    service is past its acceptable error budget.
    """
    rows = store.metric_series(params.service, "request_error_rate")
    rows = [
        r for r in rows
        if (params.start is None or r.time >= params.start) and (params.end is None or r.time <= params.end)
    ]
    observed = fmean([r.value for r in rows]) if rows else 0.0
    slo = params.slo_error_rate
    burn = observed / slo if slo > 0 else float("inf")
    return ErrorBudgetOutput(
        service=params.service,
        observed_error_rate=observed,
        slo_error_rate=slo,
        budget_burn=burn,
        breached=observed > slo,
    )


@tool(namespace="metrics")
def metrics_summary_all(params: SummaryAllInput, store: TelemetryStore) -> SummaryAllOutput:
    """Snapshot every service's error rate, p95 latency, and CPU at once.

    The dashboard view: one call gives the post-onset mean of all three RED/resource
    signals per service, sorted by error rate. Use it to orient at the start of an
    investigation before drilling into the worst service.
    """
    services = sorted(
        {s for s, _m, _u in store.list_metric_keys()}
    )
    snapshots = []
    for service in services:
        _pre_e, err = _pre_post(store, service, "request_error_rate", params.onset_second)
        _pre_l, lat = _pre_post(store, service, "latency_p95_ms", params.onset_second)
        _pre_c, cpu = _pre_post(store, service, "cpu_cores", params.onset_second)
        snapshots.append(ServiceSnapshot(service=service, error_rate=err, latency_p95_ms=lat, cpu_cores=cpu))
    snapshots.sort(key=lambda s: s.error_rate, reverse=True)
    return SummaryAllOutput(services=snapshots)
