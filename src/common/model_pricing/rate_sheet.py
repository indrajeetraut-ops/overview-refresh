"""Rate sheet: a date-range price extract for one org's usage models (`rate-sheet/1.0`,
model-pricing design §9.1; cost design §4).

`model_pricing` owns matching, channel, and date rules; cost (or any other consumer)
never loads or names a catalog itself -- it calls `rate_sheet()` once per run and reads
the result. This module is a thin caller of `catalog.py`'s existing lookup machinery
(`load_catalog` -> `Catalog.for_org` -> `OrgPrices.resolve`/`rates`): it does not
reimplement matching or the forward/backward/retired date rules, it only turns a
per-date rate lookup into the segment form a report or downstream calculation wants
(collapsed, gapless, one entry per contiguous run of equal rates).

    from datetime import date
    from common.model_pricing import rate_sheet

    sheet = rate_sheet("talium.ai", ["claude-sonnet-5", "claude-sonnet"],
                        date(2026, 10, 1), date(2026, 10, 31))
    sheet.to_json()   # exact rate-sheet/1.0 shape
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from common.model_pricing.catalog import Catalog, MatchResult, OrgPrices, TOKEN_TYPES, load_catalog
from common.model_pricing.decimals import to_rate_str


class Segment(BaseModel):
    """One contiguous run of dates, inclusive, with identical rates (§4). `from_`'s
    JSON name is `from` -- a Python keyword, hence the trailing underscore and the
    alias; construct with either name (`populate_by_name`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: date = Field(alias="from")
    to: date
    #: USD-per-token exact decimal strings, one per `TOKEN_TYPES` entry, always
    #: present; `None` = no price for that token type on this segment, never `"0"`
    #: (§2.2). Retired = every value `None`.
    rates: dict[str, str | None]


class ModelPriceEntry(BaseModel):
    """One usage model's price mapping over the sheet's range (§4). `matched` carries
    `model_id`/`vendor`/`channel`/`match_step`/`segments`; `ambiguous`/`unmatched`
    carry `reason`/`candidates` instead and have no `segments` key -- both shapes live
    in one model so field order matches §4's examples exactly, with the fields the
    other status doesn't use left `None` and dropped by `exclude_none` at
    serialization (`RateSheet.to_json`)."""

    model_config = ConfigDict(extra="forbid")

    usage_model_id: str
    status: Literal["matched", "ambiguous", "unmatched"]
    model_id: str | None = None
    vendor: str | None = None
    channel: str | None = None
    match_step: str | None = None  # "alias" | "normalized" | "canonical"
    segments: list[Segment] | None = None
    reason: str | None = None
    #: Present (possibly empty) only for `ambiguous`/`unmatched`; `None` (dropped) for
    #: `matched`.
    candidates: list[str] | None = None


class RateSheet(BaseModel):
    """`rate-sheet/1.0` (model-pricing design §4): the price mapping `rate_sheet()`
    returns, exactly the JSON shape a cost run persists."""

    model_config = ConfigDict(extra="forbid")

    org_id: str
    start: date
    end: date
    #: The catalog version this sheet was built from (`Catalog.version`) -- what
    #: "price_version" in a cost run's `request.json`/report traces back to.
    price_version: str
    models: list[ModelPriceEntry]

    def to_json(self, **kwargs) -> str:
        """`model_dump_json(by_alias=True, exclude_none=True)` -- the one call every
        writer should use so `Segment.from_` serializes as `"from"` and a non-matched
        entry's unused fields stay absent, without every caller re-deriving the same
        two kwargs."""
        kwargs.setdefault("by_alias", True)
        kwargs.setdefault("exclude_none", True)
        return self.model_dump_json(**kwargs)


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _build_segments(prices: OrgPrices, match: MatchResult, start: date, end: date) -> list[Segment]:
    """One `Segment` per contiguous run of dates whose full rate dict compares equal
    (Decimal equality, so `Decimal("2E-6")` and `Decimal("0.000002")` merge) -- covers
    `start..end` inclusive, no gaps or overlaps, a price change splits into two
    segments, a retired range becomes a segment with every rate `None` (§4)."""
    segments: list[Segment] = []
    seg_start: date | None = None
    seg_rates: dict[str, Decimal | None] | None = None
    prev_date: date | None = None

    def _flush(end_date: date) -> None:
        assert seg_start is not None and seg_rates is not None
        segments.append(
            Segment(
                from_=seg_start,
                to=end_date,
                rates={
                    tt: (to_rate_str(seg_rates[tt]) if seg_rates[tt] is not None else None)
                    for tt in TOKEN_TYPES
                },
            )
        )

    for d in _daterange(start, end):
        rates = prices.rates(match, d)
        if seg_rates is None:
            seg_start, seg_rates = d, rates
        elif rates != seg_rates:
            _flush(prev_date)  # type: ignore[arg-type]
            seg_start, seg_rates = d, rates
        prev_date = d

    if seg_start is not None:
        _flush(prev_date)  # type: ignore[arg-type]
    return segments


def rate_sheet(
    org_id: str,
    model_ids: list[str],
    start: date,
    end: date,
    *,
    catalog: Catalog | None = None,
) -> RateSheet:
    """The price mapping for `model_ids` over `start..end` inclusive (§4), for `org_id`.

    Built on the existing lookup path only: `load_catalog()` (or the injected
    `catalog`, so tests and reproducible reruns can pin a version) -> `for_org` ->
    `resolve` per model -> `rates` per date for `matched` models, collapsed into
    segments. Matching, channel selection, and the forward/backward/retired date
    rules all come from `catalog.py`/`normalize.py` -- nothing here decides a price.

    `catalog=None` loads the latest version at call time (`load_catalog()`); passing
    a `Catalog` pins the version, the same reproducibility rule `load_catalog` itself
    documents (model-pricing design §9 "version binding").
    """
    if start > end:
        raise ValueError(f"rate_sheet: start ({start}) is after end ({end})")

    cat = catalog if catalog is not None else load_catalog()
    prices = cat.for_org(org_id)

    entries: list[ModelPriceEntry] = []
    for usage_model_id in model_ids:
        match = prices.resolve(usage_model_id)
        if match.status != "matched":
            entries.append(
                ModelPriceEntry(
                    usage_model_id=usage_model_id,
                    status=match.status,
                    reason=match.reason,
                    candidates=list(match.candidates),
                )
            )
            continue

        entries.append(
            ModelPriceEntry(
                usage_model_id=usage_model_id,
                status="matched",
                model_id=match.model_id,
                vendor=match.vendor,
                channel=match.channel,
                match_step=match.step,
                segments=_build_segments(prices, match, start, end),
            )
        )

    return RateSheet(org_id=org_id, start=start, end=end, price_version=cat.version, models=entries)
