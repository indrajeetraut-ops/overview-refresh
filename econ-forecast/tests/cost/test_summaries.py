"""T7 accept (remaining): scope p90 != Sigma model p90; incomplete scope -> no
period_summary entry; daily_expected rows split by mix."""

from datetime import date

import numpy as np
import pytest
from common.model_pricing import ModelPriceEntry, RateSheet, Segment

from forecast.cost.kernel import ScopeCost, SeriesCost, price_scope
from forecast.cost.summaries import daily_expected_rows, period_summary_entries


def _period(period_type, start, end):
    return {"type": period_type, "start": start, "end": end}


def test_scope_p90_is_not_the_sum_of_series_p90s():
    dates = (date(2026, 10, 1),)
    rng = np.random.default_rng(42)
    cost_a = rng.uniform(0, 100, size=(1, 1000))
    cost_b = rng.uniform(0, 100, size=(1, 1000))
    series_a = SeriesCost(
        org_id="org-a", scope_type="team", scope_id="team1", scope_name="Team A",
        model="model-a", status="qualified", cost=cost_a, tokens=cost_a, mix=(), reason=None,
    )
    series_b = SeriesCost(
        org_id="org-a", scope_type="team", scope_id="team1", scope_name="Team A",
        model="model-b", status="qualified", cost=cost_b, tokens=cost_b, mix=(), reason=None,
    )
    scope = price_scope("org-a", "team", "team1", "Team A", [series_a, series_b], dates, 1000)
    assert scope.status == "complete"

    periods = (_period("requested", dates[0], dates[0]),)
    entries = period_summary_entries([series_a, series_b], [scope], dates, periods)
    by_model = {e["model"]: e for e in entries}
    scope_p90 = by_model[None]["p90"]
    series_p90_sum = by_model["model-a"]["p90"] + by_model["model-b"]["p90"]
    assert scope_p90 != pytest.approx(series_p90_sum, rel=1e-6)


def test_incomplete_scope_gets_no_period_summary_entry():
    dates = (date(2026, 10, 1),)
    qualified = SeriesCost(
        org_id="org-a", scope_type="team", scope_id="team1", scope_name="Team A",
        model="model-a", status="qualified", cost=np.ones((1, 2)), tokens=np.ones((1, 2)),
        mix=(), reason=None,
    )
    incomplete_scope = ScopeCost(
        org_id="org-a", scope_type="team", scope_id="team1", scope_name="Team A",
        status="incomplete", cost=None,
    )
    periods = (_period("requested", dates[0], dates[0]),)
    entries = period_summary_entries([qualified], [incomplete_scope], dates, periods)
    assert all(e["model"] is not None for e in entries)
    assert len(entries) == 1  # the one qualified series, no scope total


def test_daily_expected_rows_split_tokens_and_usd_by_mix():
    day = date(2026, 10, 1)
    model = "model-a"
    sheet = RateSheet(
        org_id="org-a", start=day, end=day, price_version="v",
        models=[
            ModelPriceEntry(
                usage_model_id=model, status="matched", model_id=model, vendor="anthropic",
                channel="anthropic", match_step="normalized",
                segments=[
                    Segment(
                        from_=day, to=day,
                        rates={"input": "0.000003", "output": "0.000015", "cache_read": "0.0000003",
                               "cache_write_5m": None, "cache_write_1h": None},
                    )
                ],
            )
        ],
    )
    tokens = np.asarray([[1_000_000.0, 1_000_000.0]], dtype=np.float64)  # 1 date, 2 samples
    series = SeriesCost(
        org_id="org-a", scope_type="team", scope_id="team1", scope_name="Team A",
        model=model, status="qualified", cost=None, tokens=tokens,
        mix=({"agent_type": "claude", "input": 0.2, "cache_write_5m": 0.0, "cache_write_1h": 0.0,
              "cache_read": 0.7, "output": 0.1},),
        reason=None,
    )
    rows = daily_expected_rows([series], sheet, (day,))
    assert len(rows) == 1
    row = rows[0]
    assert row["agent_type"] == "claude"
    assert row["tokens"]["input"] == pytest.approx(200_000.0)
    assert row["tokens"]["cache_read"] == pytest.approx(700_000.0)
    assert row["tokens"]["output"] == pytest.approx(100_000.0)
    assert row["usd"]["input"] == pytest.approx(200_000.0 * 0.000003)
    assert row["usd"]["cache_read"] == pytest.approx(700_000.0 * 0.0000003)
    assert row["usd"]["output"] == pytest.approx(100_000.0 * 0.000015)


def test_daily_expected_rows_for_inactive_series_are_all_zero():
    day = date(2026, 10, 1)
    series = SeriesCost(
        org_id="org-a", scope_type="team", scope_id="team1", scope_name="Team A",
        model="model-a", status="inactive", cost=None, tokens=None, mix=(), reason=None,
    )
    rows = daily_expected_rows([series], sheet=None, dates=(day,))
    assert len(rows) == 1
    assert rows[0]["agent_type"] is None
    assert all(v == 0.0 for v in rows[0]["usd"].values())
    assert all(v == 0.0 for v in rows[0]["tokens"].values())
