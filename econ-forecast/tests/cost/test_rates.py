"""T6 accept: §2 example ($1.281/M); null rate + nonzero share -> unpriced; null
rate + zero share -> still priced."""

from datetime import date

import pytest

from common.model_pricing import ModelPriceEntry, RateSheet, Segment

from forecast.cost.rates import series_rates


def _sheet(entries: list[ModelPriceEntry], start=date(2026, 10, 1), end=date(2026, 10, 31)) -> RateSheet:
    return RateSheet(
        org_id="org-a", start=start, end=end, price_version="test-version", models=entries
    )


def _matched_entry(model="claude-sonnet-5", **rates) -> ModelPriceEntry:
    return ModelPriceEntry(
        usage_model_id=model,
        status="matched",
        model_id=model,
        vendor="anthropic",
        channel="anthropic",
        match_step="normalized",
        segments=[
            Segment(
                from_=date(2026, 10, 1),
                to=date(2026, 10, 31),
                rates={
                    "input": rates.get("input"),
                    "output": rates.get("output"),
                    "cache_read": rates.get("cache_read"),
                    "cache_write_5m": None,
                    "cache_write_1h": None,
                },
            )
        ],
    )


def test_design_example_blended_rate_is_1_281_per_million():
    entry = _matched_entry(input="0.000003", output="0.000015", cache_read="0.0000003")
    sheet = _sheet([entry])
    dates = (date(2026, 10, 5),)
    result = series_rates(
        sheet, "claude-sonnet-5",
        {"input": 0.20, "cache_read": 0.77, "output": 0.03},
        dates,
    )
    assert result.status == "priced"
    assert result.rate[0] == pytest.approx(0.000001281)
    tokens = 1_099_651.0
    assert tokens * result.rate[0] == pytest.approx(1.41, abs=5e-3)


def test_null_rate_with_nonzero_mix_share_is_unpriced():
    entry = _matched_entry(input="0.000003", output=None, cache_read="0.0000003")
    sheet = _sheet([entry])
    result = series_rates(
        sheet, "claude-sonnet-5",
        {"input": 0.20, "cache_read": 0.77, "output": 0.03},
        (date(2026, 10, 5),),
    )
    assert result.status == "unpriced"
    assert "output" in result.reason


def test_null_rate_with_zero_mix_share_is_still_priced():
    entry = _matched_entry(input="0.000003", output=None, cache_read="0.0000003")
    sheet = _sheet([entry])
    result = series_rates(
        sheet, "claude-sonnet-5",
        {"input": 0.20, "cache_read": 0.80, "output": 0.0},
        (date(2026, 10, 5),),
    )
    assert result.status == "priced"
    assert result.rate[0] == pytest.approx(0.20 * 0.000003 + 0.80 * 0.0000003)


def test_unmatched_model_is_unpriced_with_reason():
    entry = ModelPriceEntry(
        usage_model_id="claude-mystery",
        status="unmatched",
        reason="model id not recognized: 'claude-mystery'",
        candidates=[],
    )
    sheet = _sheet([entry])
    result = series_rates(sheet, "claude-mystery", {"output": 0.5}, (date(2026, 10, 5),))
    assert result.status == "unpriced"
    assert result.reason == "model id not recognized: 'claude-mystery'"


def test_model_absent_from_sheet_is_unpriced():
    sheet = _sheet([])
    result = series_rates(sheet, "claude-ghost", {"output": 0.5}, (date(2026, 10, 5),))
    assert result.status == "unpriced"
    assert "claude-ghost" in result.reason


def test_price_change_mid_range_uses_the_segment_in_effect():
    entry = ModelPriceEntry(
        usage_model_id="claude-sonnet-5",
        status="matched",
        model_id="claude-sonnet-5",
        vendor="anthropic",
        channel="anthropic",
        match_step="normalized",
        segments=[
            Segment(
                from_=date(2026, 10, 1), to=date(2026, 10, 15),
                rates={"input": "0.000003", "output": None, "cache_read": None,
                       "cache_write_5m": None, "cache_write_1h": None},
            ),
            Segment(
                from_=date(2026, 10, 16), to=date(2026, 10, 31),
                rates={"input": "0.000004", "output": None, "cache_read": None,
                       "cache_write_5m": None, "cache_write_1h": None},
            ),
        ],
    )
    sheet = _sheet([entry])
    result = series_rates(
        sheet, "claude-sonnet-5", {"input": 1.0},
        (date(2026, 10, 10), date(2026, 10, 20)),
    )
    assert result.status == "priced"
    assert result.rate[0] == pytest.approx(0.000003)
    assert result.rate[1] == pytest.approx(0.000004)


def test_missing_catalog_is_execution_error(tmp_path):
    from datetime import date

    import pytest
    from common.model_pricing.catalog import load_catalog  # noqa: F401 - import proves module path
    from forecast.cost.errors import ExecutionError
    from forecast.cost.rates import build_rate_sheet
    from unittest import mock

    from common.model_pricing.errors import CatalogError

    with mock.patch(
        "forecast.cost.rates._model_pricing_rate_sheet", side_effect=CatalogError("no catalog")
    ):
        with pytest.raises(ExecutionError, match="model pricing unavailable"):
            build_rate_sheet("talium.ai", ["m"], date(2026, 9, 1), date(2026, 9, 2))
