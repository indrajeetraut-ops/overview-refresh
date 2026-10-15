"""Model pricing: one master rate table, keyed by (date, channel, model_id) (design
docs/modules/model-pricing.md).

    from common.model_pricing import load_catalog, TOKEN_TYPES

    catalog = load_catalog(version=None)          # None = current version
    prices = catalog.for_org("talium.ai")         # applies channel config (design §4.3)
    match = prices.resolve("claude-sonnet-5")
    prices.rate(match, "output", date(2026, 10, 1))

    from common.model_pricing import rate_sheet
    rate_sheet("talium.ai", ["claude-sonnet-5"], start, end)   # design §4

Public surface re-exported here is filled in as the package is built out (T7/T8 add
`load_catalog`, `Catalog`, `OrgPrices`, `MatchResult`, `TOKEN_TYPES`, `render_mapping`).
"""
from common.model_pricing.catalog import (
    TOKEN_TYPES,
    Catalog,
    MatchResult,
    OrgPrices,
    load_catalog,
)
from common.model_pricing.pricer import (
    HistoryRow,
    Pricer,
    load_history,
    price_to_micros,
    split_cache_write,
)
from common.model_pricing.rate_sheet import ModelPriceEntry, RateSheet, Segment, rate_sheet
from common.model_pricing.reports import render_mapping

__all__ = [
    "load_catalog",
    "Catalog",
    "OrgPrices",
    "MatchResult",
    "TOKEN_TYPES",
    "render_mapping",
    "rate_sheet",
    "RateSheet",
    "ModelPriceEntry",
    "Segment",
    "Pricer",
    "HistoryRow",
    "load_history",
    "price_to_micros",
    "split_cache_write",
]
