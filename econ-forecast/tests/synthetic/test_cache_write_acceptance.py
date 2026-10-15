"""Cache-write contract acceptance helpers (design A5 worked example, harness units)."""
from datetime import date
from decimal import Decimal as D

import pytest
from common.model_pricing import ModelPriceEntry, RateSheet, Segment

from forecast.testing.cli_cache_write_acceptance import _rate, _table

RATES = {"input": D("0.000003"), "cache_write_5m": D("0.00000375"), "cache_write_1h": D("0.000006"),
         "cache_read": D("0.0000003"), "output": D("0.000015")}
ROW = {"input": 600, "cache_write_5m": 5_982, "cache_write_1h": 193_418,
       "cache_read": 800_000, "output": 30_000}


def test_dashboard_residual_of_the_worked_example_is_0_72_percent():
    split = sum(ROW[kind] * RATES[kind] for kind in ROW)
    # The dashboard prices a bare combined cache_write at the 1-hour rate.
    dashboard = (ROW["input"] * RATES["input"] + ROW["cache_read"] * RATES["cache_read"]
                 + ROW["output"] * RATES["output"]
                 + (ROW["cache_write_5m"] + ROW["cache_write_1h"]) * RATES["cache_write_1h"])
    assert abs(split - D("1.874741")) < D("0.000001")
    assert abs((dashboard - split) - D("0.013460")) < D("0.000001")
    assert float((dashboard - split) / split * 100) == pytest.approx(0.72, abs=0.005)


def _sheet():
    day = date(2026, 10, 1)
    return RateSheet(org_id="o", start=day, end=day, price_version="v", models=[
        ModelPriceEntry(usage_model_id="m", status="matched", model_id="m", vendor="x",
                        channel="x", match_step="normalized",
                        segments=[Segment(from_=day, to=day, rates={k: str(v) for k, v in RATES.items()})]),
        ModelPriceEntry(usage_model_id="u", status="unmatched")])


def test_rate_lookup_matched_and_unmatched():
    sheet = _sheet()
    assert _rate(sheet, "m", date(2026, 10, 1), "cache_write_1h") == D("0.000006")
    assert _rate(sheet, "u", date(2026, 10, 1), "input") is None
    assert _rate(sheet, "absent", date(2026, 10, 1), "input") is None


def test_table_marks_unpriced_models():
    result = {"window": {"start": "a", "end": "b", "days": 1}, "rate_sheet_price_version": "v",
              "models": {"m": {"request_level_usd": 1.0, "cost_kernel_usd": None,
                               "a1_cost_over_request": None, "dashboard_usd": 1.0,
                               "a2_dashboard_over_request": 1.007}},
              "overall": {"a2_dashboard_over_request": 1.007, "a2_residual_pct": 0.7}}
    assert "unpriced" in _table(result)


def test_worked_example_priced_through_the_pricer_request_path():
    """Design A5: the section 2.2 row through `Pricer.request_micros` (the harness's
    request-level path), against the dashboard's combined-write pricing."""
    import importlib.util
    from pathlib import Path

    from forecast.synthetic.org.pricing import OrgPricer

    # The model_pricing test helper builds a one-model catalog ($3 / $15 per M, 5m 3.75,
    # 1h 6, read 0.30) from 2026-01-01.
    helpers_path = Path(__file__).resolve().parents[3] / "tests" / "common" / "model_pricing" / "helpers.py"
    spec = importlib.util.spec_from_file_location("_pricing_test_helpers", helpers_path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        pricing = helpers.make_pricer(Path(tmp), [helpers.row("m", date(2026, 1, 1), per_million={
            "input": 3, "cache_write_5m": 3.75, "cache_write_1h": 6, "cache_read": 0.3, "output": 15})])
    pricer = OrgPricer(pricing)
    at = date(2026, 9, 1)
    plain = {"input": ROW["input"], "output": ROW["output"], "cache_read": ROW["cache_read"]}
    detail = [{"ttl_s": 300, "tokens": ROW["cache_write_5m"]},
              {"ttl_s": 3600, "tokens": ROW["cache_write_1h"]}]
    split = pricer.request_micros("m", plain, at, detail)
    dashboard = pricer.request_micros(
        "m", {**plain, "cache_write": ROW["cache_write_5m"] + ROW["cache_write_1h"]}, at)

    assert abs(split - 1_874_741) <= 1                       # $1.874741
    assert abs((dashboard - split) - 13_460) <= 1            # $0.013460
    assert (dashboard - split) / split * 100 == pytest.approx(0.72, abs=0.005)
