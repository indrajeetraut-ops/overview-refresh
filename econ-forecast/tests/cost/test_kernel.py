"""T7 accept: §2 example day -> $1.41; scope p90 != Sigma model p90; inactive ->
zeros; incomplete -> no scope entry (that last one lives in test_summaries.py,
since it's a `period_summary_entries` property)."""

from datetime import date

import numpy as np
import pytest
from common.model_pricing import ModelPriceEntry, RateSheet, Segment

from forecast.forecasting.dataset import PreparedDataset, ScopeMeta

from forecast.cost.kernel import SeriesInput, price_one_series, price_scope


def _dataset(scopes, models, usage, coverage) -> PreparedDataset:
    return PreparedDataset(
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
        timezone="UTC",
        models=models,
        scopes=scopes,
        usage=usage,
        coverage=coverage,
        startup=[],
        hashes={},
        source_state={},
    )


def _matched_sheet(model, input_rate, output_rate, cache_read_rate, start, end) -> RateSheet:
    return RateSheet(
        org_id="org-a",
        start=start,
        end=end,
        price_version="test-version",
        models=[
            ModelPriceEntry(
                usage_model_id=model,
                status="matched",
                model_id=model,
                vendor="anthropic",
                channel="anthropic",
                match_step="normalized",
                segments=[
                    Segment(
                        from_=start,
                        to=end,
                        rates={
                            "input": input_rate,
                            "output": output_rate,
                            "cache_read": cache_read_rate,
                            "cache_write_5m": None,
                            "cache_write_1h": None,
                        },
                    )
                ],
            )
        ],
    )


def test_design_example_day_prices_to_1_41(tmp_path):
    org_id, model, agent = "org-a", "model-a", "agent-a"
    history_day = date(2026, 9, 1)
    forecast_day = date(2026, 10, 1)
    scopes = {
        (org_id, "organization", org_id): ScopeMeta(
            org_id, "organization", org_id, "Org A", frozenset([("u1", agent)])
        )
    }
    models = {org_id: {model: frozenset([agent])}}
    usage = {
        (org_id, "organization", org_id, model, history_day, agent, "input"): 200_000.0,
        (org_id, "organization", org_id, model, history_day, agent, "cache_read"): 770_000.0,
        (org_id, "organization", org_id, model, history_day, agent, "output"): 30_000.0,
    }
    coverage = {(history_day, org_id, "u1", agent): "complete"}
    dataset = _dataset(scopes, models, usage, coverage)
    sheet = _matched_sheet(model, "0.000003", "0.000015", "0.0000003", forecast_day, forecast_day)

    tokens = np.asarray([[1_099_651.0]], dtype=np.float64)  # [dates=1, samples=1]
    series = SeriesInput(
        org_id=org_id, scope_type="organization", scope_id=org_id, scope_name="Org A",
        model=model, status="qualified", tokens=tokens,
    )
    result = price_one_series(series, dataset, sheet, (forecast_day,), history_day, max_age_days=30)

    assert result.status == "qualified"
    assert result.cost[0, 0] == pytest.approx(1.41, abs=5e-3)


def test_inactive_series_prices_to_zero():
    zeros = np.zeros((3, 4), dtype=np.float64)
    series = SeriesInput(
        org_id="org-a", scope_type="team", scope_id="team1", scope_name="Team A",
        model="model-a", status="inactive", tokens=zeros,
        assumptions=("uncalibrated_model_estimate",),
    )
    result = price_one_series(series, dataset=None, sheet=None, dates=(), origin=date(2026, 9, 1), max_age_days=30)
    assert result.status == "inactive"
    assert result.cost is zeros
    assert "continued_inactivity" in result.assumptions


def test_unusable_forecast_status_passes_through_with_no_path():
    series = SeriesInput(
        org_id="org-a", scope_type="team", scope_id="team1", scope_name="Team A",
        model="model-a", status="insufficient_history", tokens=None,
        reasons=("fewer than minimum consecutive covered days ending at origin",),
    )
    result = price_one_series(series, dataset=None, sheet=None, dates=(), origin=date(2026, 9, 1), max_age_days=30)
    assert result.status == "insufficient_history"
    assert result.cost is None and result.tokens is None


def test_unmatched_model_yields_unpriced_status():
    org_id, model, agent = "org-a", "model-a", "agent-a"
    day = date(2026, 9, 1)
    forecast_day = date(2026, 10, 1)
    scopes = {
        (org_id, "organization", org_id): ScopeMeta(
            org_id, "organization", org_id, "Org A", frozenset([("u1", agent)])
        )
    }
    models = {org_id: {model: frozenset([agent])}}
    usage = {(org_id, "organization", org_id, model, day, agent, "output"): 100.0}
    coverage = {(day, org_id, "u1", agent): "complete"}
    dataset = _dataset(scopes, models, usage, coverage)
    sheet = RateSheet(org_id=org_id, start=forecast_day, end=forecast_day, price_version="v", models=[])

    tokens = np.asarray([[100.0]], dtype=np.float64)
    series = SeriesInput(
        org_id=org_id, scope_type="organization", scope_id=org_id, scope_name="Org A",
        model=model, status="qualified", tokens=tokens,
    )
    result = price_one_series(series, dataset, sheet, (forecast_day,), day, max_age_days=30)
    assert result.status == "unpriced"
    assert result.cost is None
    assert "fixed_mix_estimate" in result.assumptions


def test_scope_status_complete_conditional_incomplete():
    from forecast.cost.kernel import SeriesCost

    dates = (date(2026, 10, 1),)
    qualified = SeriesCost(
        org_id="org-a", scope_type="team", scope_id="team1", scope_name="Team A",
        model="model-a", status="qualified", cost=np.ones((1, 2)), tokens=np.ones((1, 2)),
        mix=(), reason=None,
    )
    inactive = SeriesCost(
        org_id="org-a", scope_type="team", scope_id="team1", scope_name="Team A",
        model="model-b", status="inactive", cost=np.zeros((1, 2)), tokens=np.zeros((1, 2)),
        mix=(), reason=None,
    )
    missing = SeriesCost(
        org_id="org-a", scope_type="team", scope_id="team1", scope_name="Team A",
        model="model-c", status="insufficient_history", cost=None, tokens=None,
        mix=(), reason=None,
    )

    complete = price_scope("org-a", "team", "team1", "Team A", [qualified], dates, 2)
    assert complete.status == "complete"
    np.testing.assert_array_equal(complete.cost, qualified.cost)

    conditional = price_scope("org-a", "team", "team1", "Team A", [qualified, inactive], dates, 2)
    assert conditional.status == "conditional"
    np.testing.assert_array_equal(conditional.cost, qualified.cost + inactive.cost)

    incomplete = price_scope("org-a", "team", "team1", "Team A", [qualified, missing], dates, 2)
    assert incomplete.status == "incomplete"
    assert incomplete.cost is None


def test_mix_override_prices_with_the_borrowed_shares_and_reports_the_source_window():
    """Fix 3: a scenario's `scenario_mix_borrowed` series (§6.2) still needs a real
    `mix_result` for mix.json -- borrowed shares, `active_days: 0`, `own_weight:
    0.0`, and the *source* series' window, not its own (it drew no weighted usage)."""
    org_id, model, agent = "org-a", "model-a", "agent-a"
    forecast_day = date(2026, 10, 1)
    origin = date(2026, 9, 1)
    borrowed_shares = ({"agent_type": agent, "input": 0.2, "cache_write_5m": 0.0,
                        "cache_write_1h": 0.0, "cache_read": 0.7, "output": 0.1},)
    source_window = (date(2026, 8, 1), date(2026, 8, 31))
    sheet = _matched_sheet(model, "0.000003", "0.000015", "0.0000003", forecast_day, forecast_day)
    tokens = np.asarray([[500.0]], dtype=np.float64)
    series = SeriesInput(
        org_id=org_id, scope_type="team", scope_id="team1", scope_name="Team A",
        model=model, status="qualified", tokens=tokens,
        assumptions=("scenario_mix_borrowed",),
        mix_override=borrowed_shares, mix_override_window=source_window,
    )
    result = price_one_series(series, dataset=None, sheet=sheet, dates=(forecast_day,), origin=origin, max_age_days=30)

    assert result.status == "qualified"
    assert result.mix == borrowed_shares
    assert result.mix_result is not None
    assert result.mix_result.active_days == 0
    assert result.mix_result.own_weight == 0.0
    assert (result.mix_result.window_start, result.mix_result.window_end) == source_window


_FIVE_RATES = {"input": "0.000003", "cache_write_5m": "0.00000375", "cache_write_1h": "0.000006",
               "cache_read": "0.0000003", "output": "0.000015"}


def _five_type_run(write_rates):
    org_id, model, agent = "org-a", "model-a", "agent-a"
    history_day, forecast_day = date(2026, 9, 1), date(2026, 10, 1)
    scopes = {(org_id, "organization", org_id): ScopeMeta(
        org_id, "organization", org_id, "Org A", frozenset([("u1", agent)]))}
    models = {org_id: {model: frozenset([agent])}}
    # Cache-write contract design 2.2 row: T=1,000,000, hit .80, writes .997, 1h share .97.
    amounts = {"input": 600.0, "cache_write_5m": 5_982.0, "cache_write_1h": 193_418.0,
               "cache_read": 800_000.0, "output": 30_000.0}
    usage = {(org_id, "organization", org_id, model, history_day, agent, kind): value
             for kind, value in amounts.items()}
    dataset = _dataset(scopes, models, usage, {(history_day, org_id, "u1", agent): "complete"})
    rates = {**_FIVE_RATES, **write_rates}
    sheet = RateSheet(
        org_id=org_id, start=forecast_day, end=forecast_day, price_version="v",
        models=[ModelPriceEntry(
            usage_model_id=model, status="matched", model_id=model, vendor="anthropic",
            channel="anthropic", match_step="normalized",
            segments=[Segment(from_=forecast_day, to=forecast_day, rates=rates)])])
    series = SeriesInput(
        org_id=org_id, scope_type="organization", scope_id=org_id, scope_name="Org A",
        model=model, status="qualified",
        tokens=np.asarray([[sum(amounts.values())]], dtype=np.float64))
    return price_one_series(series, dataset, sheet, (forecast_day,), history_day, max_age_days=30)


def test_five_type_row_prices_each_type_at_its_own_rate():
    result = _five_type_run({})
    assert result.status == "qualified"
    assert result.cost[0, 0] == pytest.approx(1.874741, abs=1e-6)
    # Today's contract priced the writes at the plain input rate: 1.29 -> ratio 1.45.
    assert result.cost[0, 0] / 1.29 == pytest.approx(1.45, abs=5e-3)


def test_null_write_rate_keeps_the_series_unpriced_never_zero():
    result = _five_type_run({"cache_write_1h": None})
    assert result.status == "unpriced"
    assert result.cost is None
