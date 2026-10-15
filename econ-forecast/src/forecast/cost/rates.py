"""Rate sheet -> per-series rate arrays (design §2, §4, §7).

`model_pricing.rate_sheet()` returns exact-decimal segments; this module is the one
place that converts them to float64 (brief: "Decimal strings in the sheet, converted
to float64 once in rates.py") and turns a matched entry's segments plus a series'
(already agent-summed) mix into `rate[date] = Σ_token_type mix[t] × price[t, date]`
(§2.2). A series is `unpriced` when its model isn't `matched` in the sheet, or any
date in the run's range has a null rate for a token type the series' mix actually
uses (§7) -- `unpriced` is never silently priced at 0 (design table: "null = no
price, never 0").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from common.model_pricing import Catalog, RateSheet
from common.model_pricing import rate_sheet as _model_pricing_rate_sheet
from common.model_pricing.errors import CatalogError, ConfigError

from .errors import ExecutionError

from .schemas import TOKEN_TYPES


@dataclass(frozen=True)
class SeriesRate:
    """One series' per-date USD/token rate, or why it could not be priced."""

    status: str  # "priced" | "unpriced"
    rate: tuple[float, ...] | None  # float64, one per date in the requested range
    reason: str | None


def build_rate_sheet(
    org_id: str,
    model_ids: list[str],
    start: date,
    end: date,
    *,
    catalog: Catalog | None = None,
) -> RateSheet:
    """The run's rate sheet, built exactly once per run (§3.1: `start`/`end` =
    first/last forecast date) -- every distinct usage model the forecast names plus
    every scenario destination model, deduped and sorted, so scenario validation
    (the `to`-matched check, §6.4) and pricing (§2.2) share one sheet. `catalog`
    pins a version (reproducible runs, and tests that must not read the live
    catalog off cwd); `None` loads the latest at call time, same as
    `model_pricing.rate_sheet`'s own default."""
    try:
        return _model_pricing_rate_sheet(org_id, sorted(set(model_ids)), start, end, catalog=catalog)
    except (CatalogError, ConfigError) as exc:
        # A missing or broken price catalog is an environment failure, not bad
        # cost input: exit 1 with the pricing module's own message, no traceback.
        raise ExecutionError(f"model pricing unavailable: {exc}") from exc


def _segment_rate(segment, token_type: str) -> Decimal | None:
    raw = segment.rates.get(token_type)
    return Decimal(raw) if raw is not None else None


def matched_entry(sheet: RateSheet, model: str):
    """The sheet's `matched` entry for `model`, or `None` (not present, or present
    but `ambiguous`/`unmatched` -- callers treat both the same way, `unpriced`)."""
    entry = next((m for m in sheet.models if m.usage_model_id == model), None)
    if entry is None or entry.status != "matched":
        return None
    return entry


def type_rates(
    sheet: RateSheet, model: str, dates: tuple[date, ...]
) -> dict[str, tuple[float | None, ...]] | None:
    """Per-token-type USD/token float64, one per date (the sheet's one Decimal ->
    float64 conversion, brief: "converted to float64 once in rates.py"); `None` for
    a date outside every segment or with no price for that type (never 0, design
    table). `None` overall if `model` is not `matched` in the sheet."""
    entry = matched_entry(sheet, model)
    if entry is None:
        return None
    result: dict[str, list[float | None]] = {tt: [] for tt in TOKEN_TYPES}
    for d in dates:
        segment = next((s for s in entry.segments if s.from_ <= d <= s.to), None)
        for tt in TOKEN_TYPES:
            price = _segment_rate(segment, tt) if segment is not None else None
            result[tt].append(float(price) if price is not None else None)
    return {tt: tuple(values) for tt, values in result.items()}


def series_rates(
    sheet: RateSheet,
    model: str,
    mix_by_type: dict[str, float],
    dates: tuple[date, ...],
) -> SeriesRate:
    """`rate[date] = Σ_t mix[t] × price[t, date]` for one series (§2.2), given its
    model and its mix already summed over agents (`mix[series, token_type]`, §2.2
    note -- agent never affects price, only the reported breakdown does)."""
    entry = matched_entry(sheet, model)
    if entry is None:
        raw = next((m for m in sheet.models if m.usage_model_id == model), None)
        reason = raw.reason if raw is not None else f"model not in rate sheet: {model!r}"
        return SeriesRate(status="unpriced", rate=None, reason=reason)

    prices = type_rates(sheet, model, dates)
    used_types = [tt for tt in TOKEN_TYPES if mix_by_type.get(tt, 0.0) > 0]
    rates: list[float] = []
    for i, d in enumerate(dates):
        total = 0.0
        for tt in used_types:
            price = prices[tt][i]
            if price is None:
                return SeriesRate(
                    status="unpriced",
                    rate=None,
                    reason=f"no {tt} rate on {d.isoformat()} for a nonzero-mix token type",
                )
            total += mix_by_type[tt] * price
        rates.append(total)
    return SeriesRate(status="priced", rate=tuple(rates), reason=None)


def model_ids_for_run(series_entries) -> list[str]:
    """Every distinct model named by a run's series index entries, sorted."""
    return sorted({entry["model"] for entry in series_entries})
