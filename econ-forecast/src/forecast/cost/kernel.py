"""Baseline pricing loop (design §2.2): per-series `tokens × rate`, scope
accumulation per sample, one scope's arrays live at a time.

`price_one_series` is reused unchanged for a scenario run (design §2.2: "price
tokens' with the baseline logic") -- `scenario.py` (T8) only ever changes what
`SeriesInput.tokens` holds before handing the list here; this module never knows
whether a token array came straight from the forecast or was mutated by a scenario.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np

from forecast.forecasting.dataset import PreparedDataset

from .mix import MixResult, compute_mix
from .rates import series_rates
from .schemas import HALF_LIFE_DAYS, OWN_MIX_HALF_WEIGHT_DAYS, TOKEN_TYPES

#: Cost series statuses that carry a usable `[dates, samples]` array (§7).
PRICED_STATUSES = ("qualified", "inactive")
#: The full cost series status vocabulary (§7): forecast statuses passed through
#: unchanged, plus the new `unpriced`.
SERIES_STATUSES = (
    "qualified",
    "unpriced",
    "inactive",
    "insufficient_history",
    "insufficient_startup",
    "sampling_failed",
)


@dataclass(frozen=True)
class SeriesInput:
    """One series ready for pricing, forecast token paths unless a scenario (T8)
    replaced them. `status` is the *forecast* status (§7 left column); `tokens` is
    `None` exactly when that status has no usable path."""

    org_id: str
    scope_type: str
    scope_id: str
    scope_name: str
    model: str
    status: str
    tokens: np.ndarray | None
    assumptions: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    #: A scenario-supplied mix to use verbatim instead of `compute_mix` (§6.2: "not
    #: used anywhere -> source mix, `scenario_mix_borrowed`") -- `None` for every
    #: baseline series and most scenario ones.
    mix_override: tuple[dict, ...] | None = None
    #: The borrowed-from source series' covered-day window (§8.2 `mix.json`: a
    #: borrowed-mix entry reports the *source's* window, `active_days: 0`,
    #: `own_weight: 0.0`) -- only meaningful together with `mix_override`.
    mix_override_window: tuple[date, date] | None = None


@dataclass(frozen=True)
class SeriesCost:
    """One series' priced result (§7 right column). `cost`/`tokens` are `None`
    unless `status` is `qualified` or `inactive`. `mix` is the blended per-agent
    shares actually used to price it (empty unless `qualified`)."""

    org_id: str
    scope_type: str
    scope_id: str
    scope_name: str
    model: str
    status: str
    cost: np.ndarray | None
    tokens: np.ndarray | None
    mix: tuple[dict, ...]
    reason: str | None
    assumptions: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    #: The full mix computation (`active_days`, `own_weight`, `window_*`) for
    #: `mix.json` (§8.2); `None` when priced from a scenario's `mix_override`
    #: (§6.2 `scenario_mix_borrowed`), which carries no window/weight of its own.
    mix_result: MixResult | None = None


@dataclass(frozen=True)
class ScopeCost:
    """One scope's accumulated cost (§2.2: "scope_cost += cost"). `cost` is `None`
    for `incomplete` (§8.2: "scope total only for complete, conditional, inactive")."""

    org_id: str
    scope_type: str
    scope_id: str
    scope_name: str
    status: str
    cost: np.ndarray | None
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)


def _mix_by_type(shares: tuple[dict, ...]) -> dict[str, float]:
    totals = {tt: 0.0 for tt in TOKEN_TYPES}
    for row in shares:
        for tt in TOKEN_TYPES:
            totals[tt] += row.get(tt, 0.0)
    return totals


def price_series(tokens: np.ndarray, rate: tuple[float, ...]) -> np.ndarray:
    """`cost[d, s] = tokens[d, s] × rate[d]` (§2.2)."""
    rate_arr = np.asarray(rate, dtype=np.float64)
    return tokens * rate_arr[:, None]


def price_one_series(
    series: SeriesInput,
    dataset: PreparedDataset,
    sheet,
    dates: tuple[date, ...],
    origin: date,
    max_age_days: int,
    *,
    half_life_days: int = HALF_LIFE_DAYS,
    own_mix_half_weight_days: int = OWN_MIX_HALF_WEIGHT_DAYS,
) -> SeriesCost:
    """Price one series (§2.2, §7). `dates` is the run's full requested date range
    (shared across every series); `origin`/`max_age_days` bound the mix window (§5),
    always evaluated against the forecast's own historical cutoff, never the future
    `dates` being priced."""
    base = dict(
        org_id=series.org_id,
        scope_type=series.scope_type,
        scope_id=series.scope_id,
        scope_name=series.scope_name,
        model=series.model,
    )
    # §9: `fixed_mix_estimate` replaces the forecast's `fixed_allocation_shares` on
    # every priced series (kept for statuses that carry no path at all).
    carried_assumptions = tuple(a for a in series.assumptions if a != "fixed_allocation_shares")
    if series.status == "inactive":
        # Forecast's inactive path is already all-zero (artifacts.py's own
        # invariant) -- reused as-is for both `tokens` and `cost`.
        return SeriesCost(
            **base,
            status="inactive",
            cost=series.tokens,
            tokens=series.tokens,
            mix=(),
            reason=None,
            assumptions=(*carried_assumptions, "continued_inactivity"),
            reasons=series.reasons,
        )
    if series.status != "qualified":
        # insufficient_history / insufficient_startup / sampling_failed: no path,
        # unchanged (§7 last row).
        return SeriesCost(
            **base,
            status=series.status,
            cost=None,
            tokens=None,
            mix=(),
            reason=None,
            assumptions=series.assumptions,
            reasons=series.reasons,
        )

    mix_result = None
    if series.mix_override is not None:
        shares = series.mix_override
        window_start, window_end = (
            series.mix_override_window if series.mix_override_window else (None, None)
        )
        # §6.2 scenario_mix_borrowed / §8.2 mix.json: report the *source* series'
        # window, with active_days/own_weight zeroed (this series contributed none
        # of its own weighted usage to the mix it was priced with).
        mix_result = MixResult(
            shares=shares, active_days=0, own_weight=0.0,
            window_start=window_start, window_end=window_end,
        )
    else:
        parent = None if series.scope_type == "organization" else ("organization", series.org_id)
        mix_result = compute_mix(
            dataset,
            series.org_id,
            series.scope_type,
            series.scope_id,
            series.model,
            origin,
            max_age_days,
            parent=parent,
            half_life_days=half_life_days,
            own_mix_half_weight_days=own_mix_half_weight_days,
        )
        shares = mix_result.shares
    rate = series_rates(sheet, series.model, _mix_by_type(shares), dates)
    if rate.status == "unpriced":
        return SeriesCost(
            **base,
            status="unpriced",
            cost=None,
            tokens=None,
            mix=shares,
            reason=rate.reason,
            assumptions=(*carried_assumptions, "fixed_mix_estimate"),
            reasons=(*series.reasons, rate.reason),
            mix_result=mix_result,
        )
    cost = price_series(series.tokens, rate.rate)
    return SeriesCost(
        **base,
        status="qualified",
        cost=cost,
        tokens=series.tokens,
        mix=shares,
        reason=None,
        assumptions=(*carried_assumptions, "fixed_mix_estimate"),
        reasons=series.reasons,
        mix_result=mix_result,
    )


def _scope_status(statuses: set[str]) -> str:
    """Same set-based rule as forecasting's own scope status (§7): cost only adds
    `unpriced` to the "no usable path" bucket that already forces `incomplete`."""
    if statuses == {"qualified"}:
        return "complete"
    if statuses <= {"qualified", "inactive"} and "qualified" in statuses:
        return "conditional"
    if statuses == {"inactive"}:
        return "inactive"
    return "incomplete"


def price_scope(
    org_id: str,
    scope_type: str,
    scope_id: str,
    scope_name: str,
    series_costs: list[SeriesCost],
    dates: tuple[date, ...],
    samples: int,
) -> ScopeCost:
    """Accumulate a scope's priced series (§2.2). `cost` stays `None` when the
    scope is `incomplete` -- no scope total is ever computed for one (§8.2)."""
    statuses = {s.status for s in series_costs}
    status = _scope_status(statuses)
    cost = None
    if status != "incomplete":
        cost = np.zeros((len(dates), samples), dtype=np.float64)
        for s in series_costs:
            if s.cost is not None:
                cost = cost + s.cost
    assumptions = tuple(sorted({a for s in series_costs for a in s.assumptions}))
    reasons = tuple(r for s in series_costs for r in s.reasons)
    return ScopeCost(org_id, scope_type, scope_id, scope_name, status, cost, assumptions, reasons)
