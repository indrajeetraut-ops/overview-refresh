"""Forecasting for the MVP plan's deep-dives (Track X, X5): cost-per-PR envelope, the
forecast cone drawn off it, budget burn-up, and feature cost-to-finish.

Lives at `api/` (not `api/manager/`) because it's shared: E1 (`/v1/economics`, manager-scoped)
and D3 (`/v1/features/{issueKey}`'s cost-to-finish) both consume it, and D3 is reachable from
either surface. Framework-free (aggregate.py's ground rule 1): every function takes plain
values -- lists of dollar figures, an already-built envelope dict -- and returns plain dicts.
Deterministic throughout, no RNG (X5's own Verify line): a forecast is a fixed extrapolation
over the scope's own trailing distribution, never a simulated sample path.
"""
from __future__ import annotations

from datetime import date, timedelta


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (numpy's default "linear" method), reimplemented here
    rather than pulling in numpy for one call. `values` need not be pre-sorted. `pct` in
    [0, 1]. Callers guarantee `values` is non-empty."""
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def cost_per_pr_envelope(pr_costs_usd_micros: list[int]) -> dict | None:
    """X5: the cost-per-PR distribution envelope over a scope's trailing merged-PR costs --
    median + p25/p75 band + p90/p99 tail, the shape `EnvPoint`/`Dist` charts read. `None`
    (cold-start), not a zero envelope, when the scope has no merged PRs to draw from."""
    if not pr_costs_usd_micros:
        return None
    values = [float(c) for c in pr_costs_usd_micros]
    return {
        "min_usd_micros": min(pr_costs_usd_micros),
        "p25_usd_micros": round(percentile(values, 0.25)),
        "median_usd_micros": round(percentile(values, 0.5)),
        "p75_usd_micros": round(percentile(values, 0.75)),
        "p90_usd_micros": round(percentile(values, 0.90)),
        "p99_usd_micros": round(percentile(values, 0.99)),
        "max_usd_micros": max(pr_costs_usd_micros),
    }


#: A fixed per-step widening rate, not fit from the scope's own trailing volatility.
#: TODO(X5-config): move to config once a scope's own variance is used instead of a flat rate.
_CONE_WIDEN_RATE = 0.15


def forecast_cone(envelope: dict, periods_ahead: int) -> list[dict]:
    """X5: a widening forecast cone anchored on `envelope`'s median/p25/p75 -- each step
    ahead widens the band around the (held-flat) median by `_CONE_WIDEN_RATE`, so the band
    strictly widens step over step (Verify: "forecast cone widens monotonically") with no
    RNG. Step 0 *is* the handoff point (band == today's own band), so an actual line and a
    forecast line drawn from this are continuous there by construction, not just by a
    chart-side patch."""
    if envelope is None or periods_ahead < 0:
        return []
    median = envelope["median_usd_micros"]
    lo0 = envelope["p25_usd_micros"]
    hi0 = envelope["p75_usd_micros"]
    cone = []
    for step in range(periods_ahead + 1):
        widen = 1 + _CONE_WIDEN_RATE * step
        cone.append({
            "step": step,
            "median_usd_micros": median,
            "band_lo_usd_micros": round(median - (median - lo0) * widen),
            "band_hi_usd_micros": round(median + (hi0 - median) * widen),
        })
    return cone


def budget_burnup(daily_actuals_usd_micros: list[int], days_in_period: int) -> list[dict] | None:
    """X5: period-end budget burn-up -- cumulative actual-to-date, then a flat-rate
    projection (today's average daily spend, held constant) out to `days_in_period`.
    `None` (cold-start) with no days reported yet in the period.

    The handoff day carries *both* `actual_usd_micros` and `projected_usd_micros`, equal, so
    a chart's actual and projected lines are continuous there (Verify) rather than jumping
    between two different bases at the same day.

    TODO(X5-trend): a flat rate off today's own average, not a fit against the period's
    trend so far (mock.ts's own prototype applied a growth-acceleration fudge factor on the
    projected leg -- deliberately dropped here as an unprincipled flourish, not a real
    model)."""
    if not daily_actuals_usd_micros:
        return None
    n = len(daily_actuals_usd_micros)
    points: list[dict] = []
    running = 0
    for v in daily_actuals_usd_micros:
        running += v or 0
        points.append({"day": len(points) + 1, "actual_usd_micros": running})
    points[-1]["projected_usd_micros"] = points[-1]["actual_usd_micros"]
    per_day_rate = points[-1]["actual_usd_micros"] / n
    for day in range(n + 1, days_in_period + 1):
        points.append({"day": day, "projected_usd_micros": round(per_day_rate * day)})
    return points


def dist_stats(values: list[float]) -> dict | None:
    """E1's `Dist` (avg/min/p50/p70/p95/max) over a scope's own cost- or time-to-merge
    values -- a different, coarser percentile cut than `cost_per_pr_envelope`'s (p25/p75/p90/
    p99), matching the mockup's own `Dist` shape rather than reusing the envelope's. `None`
    (cold-start) with nothing to summarize.

    Unlike every `_usd_micros` figure elsewhere in this API, `Dist`'s fields carry **no**
    fixed unit of their own -- the mockup's own `Dist` is unitless numbers plus a separate
    `unit: "$" | "d"` the chart is told (`PercentileBar`'s own prop). Callers passing a cost
    `Dist` must convert to whole dollars before calling this (`TODO` caught once, live: E1's
    first cut passed raw usd_micros through unconverted -- always divide out of micros at
    the call site, never inside this generic helper, which has no way to know which unit a
    given `values` list is already in).
    """
    if not values:
        return None
    return {
        "avg": sum(values) / len(values),
        "min": min(values),
        "p50": percentile(values, 0.5),
        "p70": percentile(values, 0.7),
        "p95": percentile(values, 0.95),
        "max": max(values),
    }


def feature_cost_to_finish(open_prs: int, envelope: dict | None) -> dict | None:
    """X5: `openPRs × the recent $/PR envelope` -> low/mid/high plus the `basis` string the
    UI reads verbatim (never recomputed client-side). `None` (cold-start) only when there is
    no envelope to base an estimate on (no merged-PR history yet in this scope) -- zero open
    PRs is a real, legitimate zero (nothing left to finish), not an absent state."""
    if envelope is None:
        return None
    if not open_prs:
        return {
            "open_prs": 0, "low_usd_micros": 0, "mid_usd_micros": 0, "high_usd_micros": 0,
            "basis": "No open PRs remain.",
        }
    median = envelope["median_usd_micros"]
    p75 = envelope["p75_usd_micros"]
    basis = (f"{open_prs} open PR{'s' if open_prs != 1 else ''} × the recent $/PR envelope "
             f"(median ${median / 1_000_000:.0f}, p75 ${p75 / 1_000_000:.0f})")
    return {
        "open_prs": open_prs,
        "low_usd_micros": open_prs * envelope["p25_usd_micros"],
        "mid_usd_micros": open_prs * median,
        "high_usd_micros": open_prs * p75,
        "basis": basis,
    }


# ============================================================================================
# Precomputed budget forecast (docs/plan/2026-10-05-forecast-dashboard-integration.md §3.6,
# §3.8). Pure helpers over the stored run: plain lists and numbers in, plain dicts out. Money is
# integer usd_micros, probabilities are floats in [0, 1].
# ============================================================================================

#: Probabilities are compared against thresholds after rounding to this many decimals, so that
#: `1 - 0.8` (0.19999999999999996) is the 0.2 it means. Reported values are never rounded.
_PROB_DIGITS = 12

#: The three run-out quantiles of `budget_runway` (`runs_out_on.p10/p50/p90`).
_RUNOUT_QUANTILES = (("p10", 0.1), ("p50", 0.5), ("p90", 0.9))


def _round_prob(p: float) -> float:
    return round(p, _PROB_DIGITS)


def quantile_cdf(grid: list[int], values: list[int], threshold: float) -> float:
    """`P(X <= threshold)` for a variable known by its quantile function: `values[i]` is the
    `grid[i]`-th percentile (`grid` ints 0..100 strictly increasing, `values` non-decreasing).

    `0.0` below `values[0]`. Otherwise take the **largest** index `i` with `values[i] <=
    threshold`: the last index gives `1.0`; any other is linear interpolation inside the
    strictly increasing segment `i..i+1`. A flat run of equal values therefore jumps to its top
    percentile. Example: grid 0..100 step 5 with values 1000, 1050, ..., 2000 gives 0.50 at 1500,
    0.525 at 1525, 0.0 at 900 and 1.0 at 2100."""
    if threshold < values[0]:
        return 0.0
    i = max(k for k in range(len(values)) if values[k] <= threshold)
    if i == len(values) - 1:
        return 1.0
    lo, hi = values[i], values[i + 1]
    return (grid[i] + (grid[i + 1] - grid[i]) * (threshold - lo) / (hi - lo)) / 100


def quantile_value(grid: list[int], values: list[int], pct: float) -> float:
    """The inverse of `quantile_cdf`: the value at percentile `pct` (in percent), by linear
    interpolation between grid entries; `pct` is clamped to `[grid[0], grid[-1]]`. Exact at a
    grid entry."""
    if pct <= grid[0]:
        return float(values[0])
    if pct >= grid[-1]:
        return float(values[-1])
    i = max(k for k in range(len(grid)) if grid[k] <= pct)
    if grid[i] == pct:
        return float(values[i])
    return values[i] + (values[i + 1] - values[i]) * (pct - grid[i]) / (grid[i + 1] - grid[i])


def _micros(x: float) -> int:
    return int(round(x))


def compose_period(*, start: date, end: date, forecast_from: date, dates: list[date],
                   cumulative_expected: list[int], cumulative_quantiles: list[list[int]],
                   total_expected: int, total_quantiles: list[int], actual_to_date: int,
                   budget: int | None, baseline_total_expected: int | None, grid: list[int],
                   band_low: int, band_high: int) -> dict:
    """One `ForecastPeriod`'s numbers (without `runway`, which `budget_runway` adds).

    `dates`, `cumulative_expected` and `cumulative_quantiles` run `forecast_from..end`;
    `total_*` is the last cumulative entry (the whole forecast part of the period).
    `actual_to_date` is the spend before `forecast_from` (0 for a future period).

    * `landing` = `actual_to_date` + `total`: the expected value and the 10/50/90th percentiles
      (interpolated on the grid, exact when the grid holds them).
    * `projected[k]` = `actual_to_date` + the cumulative mean and the `band_low` / `band_high`
      grid entries on `dates[k]`.
    * `within_budget_probability` = P(total <= budget - actual_to_date); `None` without a budget
      and `0.0` once the budget is already spent.
    * `delta_vs_baseline` = this landing's expected minus the baseline's (`baseline_total_expected`
      is the baseline's `total.expected`; both share `actual_to_date`); 0 without a baseline."""
    i_low, i_high = grid.index(band_low), grid.index(band_high)
    projected = [
        {"date": d, "expected_usd_micros": actual_to_date + cumulative_expected[k],
         "low_usd_micros": actual_to_date + cumulative_quantiles[k][i_low],
         "high_usd_micros": actual_to_date + cumulative_quantiles[k][i_high]}
        for k, d in enumerate(dates)
    ]
    landing = {
        "expected_usd_micros": actual_to_date + total_expected,
        "p10_usd_micros": actual_to_date + _micros(quantile_value(grid, total_quantiles, 10)),
        "p50_usd_micros": actual_to_date + _micros(quantile_value(grid, total_quantiles, 50)),
        "p90_usd_micros": actual_to_date + _micros(quantile_value(grid, total_quantiles, 90)),
    }
    within: float | None = None
    if budget is not None:
        remaining = budget - actual_to_date
        within = 0.0 if remaining <= 0 else quantile_cdf(grid, total_quantiles, remaining)
    delta = 0 if baseline_total_expected is None else total_expected - baseline_total_expected
    return {
        "start": start, "end": end, "forecast_from": forecast_from,
        "actual_to_date_usd_micros": actual_to_date,
        "projected": projected, "landing": landing,
        "within_budget_probability": within,
        "delta_vs_baseline_usd_micros": delta,
    }


def first_date_reaching(daily: dict[date, int], start: date, last: date, budget: int) -> date:
    """The first date in `start..last` on which the running sum of `daily` (missing days are 0)
    reached `budget`; `start` for a budget of 0 or below. Falls back to `last` if the sum never
    gets there (cannot happen when the caller found the budget already spent)."""
    if budget <= 0:
        return start
    running = 0
    d = start
    while d <= last:
        running += daily.get(d, 0)
        if running >= budget:
            return d
        d += timedelta(days=1)
    return last


def budget_runway(*, start: date, end: date, dates: list[date],
                  cumulative_quantiles: list[list[int]], grid: list[int], actual_to_date: int,
                  budget: int, within_budget_probability: float, daily_actuals: dict[date, int],
                  lasts_below: float, runs_out_above: float, min_probability: float) -> dict:
    """Will the period's `budget` last? (plan §3.8). Call only for the current period of a scope
    with a budget; `within_budget_probability` is the period's own figure, so the two always sum
    to exactly 1.

    With `R = budget - actual_to_date` and `dates` / `cumulative_quantiles` running
    `forecast_from..end`:

    * `R <= 0` -> `exhausted`: `runs_out_probability` 1.0, and all three `runs_out_on` dates are
      the first date the cumulative *actual* spend (`daily_actuals`, from `start`) reached the
      budget (`start` itself for a budget of 0 or below); `days_short` = `end` minus that date.
    * otherwise `P(out by d) = 1 - quantile_cdf(row_d, R)` and `runs_out_probability` =
      `1 - within_budget_probability`. `runs_out_on.{p10,p50,p90}` is the earliest `d` with
      `P(out by d) >= q * P(out)` for q = 0.1, 0.5, 0.9 (the date it runs out, given that it
      does); all three are `None` below `min_probability`. `days_short` = `end - runs_out_on.p50`,
      `None` when that is `None`.
    * `status`: `lasts` below `lasts_below`, `runs_out` above `runs_out_above`, else `at_risk`.
      Probabilities are compared after rounding to 12 decimals (see `_PROB_DIGITS`).

    Monotonicity: raising the budget never raises `runs_out_probability`; `P(out by d)` never
    falls as `d` grows; `p10 <= p50 <= p90`. The date `p50` is NOT guaranteed never to move
    earlier when the budget rises: the ratio `P(out by d) / P(out)` is not monotone in the
    budget unless the distributions are hazard-rate ordered. On the example run this happened in
    44 of 28,000 budget steps, always a single day, always with P(out) between 0.051 and 0.091."""
    if actual_to_date >= budget:
        crossed = first_date_reaching(daily_actuals, start, dates[0] - timedelta(days=1)
                                      if dates else end, budget)
        return {"status": "exhausted", "runs_out_probability": 1.0,
                "runs_out_on": {"p10": crossed, "p50": crossed, "p90": crossed},
                "days_short": (end - crossed).days}
    remaining = budget - actual_to_date
    p_out = 1 - within_budget_probability
    p_cmp = _round_prob(p_out)
    out_by = [1 - quantile_cdf(grid, row, remaining) for row in cumulative_quantiles]
    runs_out_on: dict[str, date | None] = {"p10": None, "p50": None, "p90": None}
    if p_cmp >= _round_prob(min_probability):
        for name, q in _RUNOUT_QUANTILES:
            target = _round_prob(q * p_out)
            # The last row is the period total, so the search always ends there at the latest.
            runs_out_on[name] = next(
                (dates[k] for k, p in enumerate(out_by) if _round_prob(p) >= target), dates[-1])
    if p_cmp < _round_prob(lasts_below):
        status = "lasts"
    elif p_cmp > _round_prob(runs_out_above):
        status = "runs_out"
    else:
        status = "at_risk"
    p50 = runs_out_on["p50"]
    return {"status": status, "runs_out_probability": p_out, "runs_out_on": runs_out_on,
            "days_short": None if p50 is None else (end - p50).days}


def period_runway(*, current: bool, budget: int | None, **kwargs) -> dict | None:
    """`budget_runway` for a period that has one: `None` for a future period (nothing spent yet,
    no pace to judge) and for a scope without a budget. `kwargs` are `budget_runway`'s."""
    if not current or budget is None:
        return None
    return budget_runway(budget=budget, **kwargs)
