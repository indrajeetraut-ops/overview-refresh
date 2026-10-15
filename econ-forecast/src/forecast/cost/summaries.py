"""`daily_expected.jsonl` and `period_summary.json` rows (design §8.2), built from
already-priced `SeriesCost`/`ScopeCost` (kernel.py) -- no pricing happens here."""

from __future__ import annotations

from datetime import date

import numpy as np

from .kernel import ScopeCost, SeriesCost
from .rates import type_rates
from .schemas import TOKEN_TYPES


def _quantiles(cost: np.ndarray) -> tuple[float, float, float, float]:
    """Mean and p10/p50/p90 of the per-sample sum over the masked dates (forecasting's
    own `allocation.quantiles`: percentiles from per-sample totals, never computed by
    adding per-series percentiles, §2.2)."""
    totals = np.sum(cost, axis=0)
    p10, p50, p90 = (float(x) for x in np.quantile(totals, [0.1, 0.5, 0.9], method="linear"))
    return float(np.mean(totals)), p10, p50, p90


def daily_expected_rows(series_costs: list[SeriesCost], sheet, dates: tuple[date, ...]) -> list[dict]:
    """One row per (date, series, agent) for `qualified`/`inactive` series (§8.2):
    `tokens` = mean over samples × mix share; `usd` = that × the type's own price
    (0 when the type carries zero mix, whether or not it has a price)."""
    rows: list[dict] = []
    for series in series_costs:
        if series.status == "inactive":
            for d in dates:
                rows.append(
                    {
                        "date": d.isoformat(),
                        "scope_type": series.scope_type,
                        "scope_name": series.scope_name,
                        "model": series.model,
                        "agent_type": None,
                        "usd": {tt: 0.0 for tt in TOKEN_TYPES},
                        "tokens": {tt: 0.0 for tt in TOKEN_TYPES},
                    }
                )
            continue
        if series.status != "qualified":
            continue
        tokens_mean = np.mean(series.tokens, axis=1)
        prices = type_rates(sheet, series.model, dates)
        for i, d in enumerate(dates):
            total = float(tokens_mean[i])
            for row in series.mix:
                tokens_by_type = {tt: total * row.get(tt, 0.0) for tt in TOKEN_TYPES}
                usd_by_type = {
                    tt: tokens_by_type[tt] * (prices[tt][i] or 0.0) for tt in TOKEN_TYPES
                }
                rows.append(
                    {
                        "date": d.isoformat(),
                        "scope_type": series.scope_type,
                        "scope_name": series.scope_name,
                        "model": series.model,
                        "agent_type": row["agent_type"],
                        "usd": usd_by_type,
                        "tokens": tokens_by_type,
                    }
                )
    return rows


def _period_entry(scope_type, scope_name, model, period, status, cost, assumptions) -> dict:
    mask = [period["start"] <= d <= period["end"] for d in period["dates"]]
    expected, p10, p50, p90 = _quantiles(cost[mask])
    return {
        "scope_type": scope_type,
        "scope_name": scope_name,
        "model": model,
        "period_type": period["type"],
        "start": period["start"].isoformat(),
        "end": period["end"].isoformat(),
        "status": status,
        "expected": expected,
        "p10": p10,
        "p50": p50,
        "p90": p90,
        "assumptions": list(assumptions),
    }


def period_summary_entries(
    series_costs: list[SeriesCost],
    scope_costs: list[ScopeCost],
    dates: tuple[date, ...],
    periods: tuple[dict, ...],
) -> list[dict]:
    """Per series and per scope, per period (§8.2): expected/p10/p50/p90 in USD.
    Only `qualified`/`inactive` series, and scopes that are not `incomplete`, ever
    get an entry -- an `incomplete` scope has no total (§8.2)."""
    periods_with_dates = [{**p, "dates": dates} for p in periods]
    entries: list[dict] = []
    for series in series_costs:
        if series.status not in ("qualified", "inactive"):
            continue
        for period in periods_with_dates:
            entries.append(
                _period_entry(
                    series.scope_type,
                    series.scope_name,
                    series.model,
                    period,
                    series.status,
                    series.cost,
                    series.assumptions,
                )
            )
    for scope in scope_costs:
        if scope.status == "incomplete":
            continue
        for period in periods_with_dates:
            entries.append(
                _period_entry(
                    scope.scope_type,
                    scope.scope_name,
                    None,
                    period,
                    scope.status,
                    scope.cost,
                    scope.assumptions,
                )
            )
    return entries
