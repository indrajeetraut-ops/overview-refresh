"""T6 pure helpers of `server/api/forecast.py`: `quantile_cdf`, `quantile_value`,
`compose_period`, and the `ForecastConfig` thresholds. No database. Every expected number is
worked out by hand in the comment next to it."""
from datetime import date

import pytest
from pydantic import ValidationError

from server.api import forecast as fc
from server.api.config import ApiConfig, ForecastConfig

GRID = list(range(0, 101, 5))                       # 21 percentiles
STEP50 = [1000 + 50 * i for i in range(21)]         # 1000, 1050, ..., 2000


# ------------------------------------------------------------------------------ quantile_cdf

def test_cdf_plan_worked_example():
    """Plan §3.6: budget 1700, actual to date 200 -> threshold 1500."""
    assert fc.quantile_cdf(GRID, STEP50, 1700 - 200) == 0.50
    assert fc.quantile_cdf(GRID, STEP50, 1525) == pytest.approx(0.525)   # (50 + 5*25/50) / 100
    assert fc.quantile_cdf(GRID, STEP50, 900) == 0.0
    assert fc.quantile_cdf(GRID, STEP50, 2100) == 1.0
    assert fc.quantile_cdf(GRID, STEP50, 2000) == 1.0


def test_cdf_at_the_ends_and_on_grid_values():
    assert fc.quantile_cdf(GRID, STEP50, 999) == 0.0
    assert fc.quantile_cdf(GRID, STEP50, 1000) == 0.0          # the minimum: P(X <= min) = 0 here
    assert fc.quantile_cdf(GRID, STEP50, 1050) == 0.05          # exactly on the 5th percentile
    assert fc.quantile_cdf(GRID, STEP50, 1975) == pytest.approx(0.975)   # (95 + 5*25/50) / 100
    assert fc.quantile_cdf(GRID, STEP50, 1999.99) < 1.0


def test_cdf_flat_run_jumps_to_its_top_percentile():
    # values 0 for the first three percentiles (0, 5, 10), then 100, 200, ...
    grid = [0, 5, 10, 15, 20]
    values = [0, 0, 0, 100, 200]
    assert fc.quantile_cdf(grid, values, -1) == 0.0
    assert fc.quantile_cdf(grid, values, 0) == 0.10             # largest i with v<=0 is 2 -> grid 10
    assert fc.quantile_cdf(grid, values, 50) == pytest.approx(0.125)   # 10 + 5*50/100 = 12.5
    # a flat run in the middle: 100, 100 -> the threshold 100 sits at the top of the run
    assert fc.quantile_cdf([0, 25, 50, 75, 100], [10, 100, 100, 300, 500], 100) == 0.50


def test_cdf_single_valued_distribution():
    grid = [0, 50, 100]
    values = [700, 700, 700]
    assert fc.quantile_cdf(grid, values, 699) == 0.0
    assert fc.quantile_cdf(grid, values, 700) == 1.0
    assert fc.quantile_cdf(grid, values, 10_000) == 1.0


def test_cdf_two_point_grid():
    assert fc.quantile_cdf([0, 100], [100, 300], 200) == 0.5
    assert fc.quantile_cdf([0, 100], [100, 300], 300) == 1.0


def test_cdf_is_non_decreasing_in_the_threshold():
    values = [0, 0, 3, 10, 10, 10, 40, 41, 90, 90, 90, 200, 200, 300, 301, 302, 400, 900, 901, 902, 1000]
    previous = -1.0
    for t in range(-5, 1100, 3):
        p = fc.quantile_cdf(GRID, values, t)
        assert 0.0 <= p <= 1.0 and p >= previous
        previous = p


# ------------------------------------------------------------------------------ quantile_value

def test_quantile_value_interpolates_and_clamps():
    assert fc.quantile_value(GRID, STEP50, 50) == 1500
    assert fc.quantile_value(GRID, STEP50, 52.5) == pytest.approx(1525)
    assert fc.quantile_value(GRID, STEP50, 0) == 1000
    assert fc.quantile_value(GRID, STEP50, 100) == 2000
    assert fc.quantile_value(GRID, STEP50, -5) == 1000           # clamped
    assert fc.quantile_value(GRID, STEP50, 140) == 2000
    assert fc.quantile_value([0, 40, 100], [0, 400, 1000], 70) == 700   # 400 + 600*30/60


def test_quantile_value_inverts_the_cdf_on_a_strictly_increasing_row():
    for pct in (3, 12.5, 50, 77, 99):
        v = fc.quantile_value(GRID, STEP50, pct)
        assert fc.quantile_cdf(GRID, STEP50, v) * 100 == pytest.approx(pct)


# ------------------------------------------------------------------------------ compose_period

DATES = [date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 11)]
BASES = [100, 250, 400]                              # row k, percentile index i -> BASES[k] + 10*i
CUM_Q = [[b + 10 * i for i in range(21)] for b in BASES]
TOTAL_Q = CUM_Q[-1]                                  # 400, 410, ..., 600


def _compose(**over):
    args = dict(start=date(2026, 10, 1), end=date(2026, 10, 11), forecast_from=DATES[0],
                dates=DATES, cumulative_expected=[110, 260, 410], cumulative_quantiles=CUM_Q,
                total_expected=410, total_quantiles=TOTAL_Q, actual_to_date=1000, budget=None,
                baseline_total_expected=None, grid=GRID, band_low=10, band_high=90)
    args.update(over)
    return fc.compose_period(**args)


def test_compose_landing_and_projected_are_actual_plus_forecast():
    p = _compose()
    assert p["start"] == date(2026, 10, 1) and p["end"] == date(2026, 10, 11)
    assert p["forecast_from"] == date(2026, 10, 9)
    assert p["actual_to_date_usd_micros"] == 1000
    # landing: 1000 + total.expected 410; p10 = row index 2 -> 1000 + 420; p50 = index 10 -> 1000 + 500;
    # p90 = index 18 -> 1000 + 580
    assert p["landing"] == {"expected_usd_micros": 1410, "p10_usd_micros": 1420,
                            "p50_usd_micros": 1500, "p90_usd_micros": 1580}
    # projected: day 0 -> 1000 + 110 (mean), band_low = index 2 of row 0 = 100 + 20, band_high = index 18 = 100 + 180
    assert p["projected"][0] == {"date": DATES[0], "expected_usd_micros": 1110,
                                 "low_usd_micros": 1120, "high_usd_micros": 1280}
    assert p["projected"][2] == {"date": DATES[2], "expected_usd_micros": 1410,
                                 "low_usd_micros": 1420, "high_usd_micros": 1580}
    assert [x["date"] for x in p["projected"]] == DATES
    # the last projected point is the landing (mean and band entries)
    assert p["projected"][-1]["expected_usd_micros"] == p["landing"]["expected_usd_micros"]
    assert p["projected"][-1]["low_usd_micros"] == p["landing"]["p10_usd_micros"]
    assert p["projected"][-1]["high_usd_micros"] == p["landing"]["p90_usd_micros"]


def test_compose_uses_the_configured_band_not_10_90():
    p = _compose(band_low=5, band_high=95)
    assert p["projected"][0]["low_usd_micros"] == 1000 + 100 + 10      # index 1
    assert p["projected"][0]["high_usd_micros"] == 1000 + 100 + 190    # index 19


def test_compose_within_budget_probability():
    # budget 1500, actual 1000 -> threshold 500 = total quantile index 10 -> 0.50
    assert _compose(budget=1500)["within_budget_probability"] == 0.50
    # budget 1525 -> threshold 525: segment 12..13 (520, 530): (60 + 5*5/10)/100 = 0.625
    assert _compose(budget=1525)["within_budget_probability"] == pytest.approx(0.625)
    # budget 1300 -> threshold 300 < 400 (the minimum): 0
    assert _compose(budget=1300)["within_budget_probability"] == 0.0
    # budget 2000 -> threshold 1000 > 600: 1
    assert _compose(budget=2000)["within_budget_probability"] == 1.0
    # no budget -> None
    assert _compose(budget=None)["within_budget_probability"] is None


def test_compose_spent_budget_is_zero_probability():
    assert _compose(budget=1000)["within_budget_probability"] == 0.0     # remaining exactly 0
    assert _compose(budget=900)["within_budget_probability"] == 0.0      # already over
    assert _compose(budget=0)["within_budget_probability"] == 0.0
    assert _compose(budget=-5)["within_budget_probability"] == 0.0


def test_compose_delta_vs_baseline():
    assert _compose(baseline_total_expected=400)["delta_vs_baseline_usd_micros"] == 10
    assert _compose(baseline_total_expected=500)["delta_vs_baseline_usd_micros"] == -90
    assert _compose(baseline_total_expected=410)["delta_vs_baseline_usd_micros"] == 0
    assert _compose(baseline_total_expected=None)["delta_vs_baseline_usd_micros"] == 0


def test_compose_future_period_has_no_actuals():
    p = _compose(start=DATES[0], actual_to_date=0, budget=450)
    assert p["actual_to_date_usd_micros"] == 0
    assert p["landing"]["expected_usd_micros"] == 410
    assert p["landing"]["p50_usd_micros"] == 500
    assert p["projected"][0]["expected_usd_micros"] == 110
    # threshold 450 -> index 4 (440): segment (440, 450) -> 450 is index 5: cdf = 25/100
    assert p["within_budget_probability"] == 0.25


def test_compose_returns_plain_integers():
    p = _compose(budget=1500)
    values = [p["actual_to_date_usd_micros"], p["delta_vs_baseline_usd_micros"], *p["landing"].values()]
    for pt in p["projected"]:
        values += [pt["expected_usd_micros"], pt["low_usd_micros"], pt["high_usd_micros"]]
    assert all(type(v) is int for v in values)


def test_compose_rounds_interpolated_landing_percentiles():
    # grid without 10/50/90: [0, 30, 100] with values [0, 300, 1000]; p50 = 300 + 700*20/70 = 500
    p = fc.compose_period(
        start=date(2026, 11, 1), end=date(2026, 11, 1), forecast_from=date(2026, 11, 1),
        dates=[date(2026, 11, 1)], cumulative_expected=[500], cumulative_quantiles=[[0, 300, 1000]],
        total_expected=500, total_quantiles=[0, 300, 1000], actual_to_date=0, budget=None,
        baseline_total_expected=None, grid=[0, 30, 100], band_low=30, band_high=100)
    assert p["landing"]["p50_usd_micros"] == 500
    assert p["landing"]["p10_usd_micros"] == 100        # 300 * 10/30
    assert p["landing"]["p90_usd_micros"] == 900        # 300 + 700*60/70
    assert p["projected"][0]["low_usd_micros"] == 300 and p["projected"][0]["high_usd_micros"] == 1000


# ------------------------------------------------------------------------------ config

def test_forecast_config_defaults():
    cfg = ApiConfig().forecast
    assert (cfg.runway_lasts_below, cfg.runway_runs_out_above) == (0.2, 0.8)
    assert (cfg.runway_min_probability, cfg.planning_confidence) == (0.05, 0.8)
    assert cfg.envelope_trailing_days == 90 and cfg.envelope_min_prs == 5      # unchanged


@pytest.mark.parametrize("kwargs", [
    {"planning_confidence": 0}, {"planning_confidence": 1.01}, {"planning_confidence": -0.1},
    {"runway_lasts_below": 0}, {"runway_lasts_below": 1.5},
    {"runway_runs_out_above": 0}, {"runway_runs_out_above": 1.2},
    {"runway_min_probability": -0.01}, {"runway_min_probability": 1.1},
    {"runway_lasts_below": 0.9, "runway_runs_out_above": 0.8},          # lasts_below > runs_out_above
    {"runway_min_probability": 0.3, "runway_lasts_below": 0.2},          # min_probability > lasts_below
])
def test_forecast_config_rejects_invalid_values(kwargs):
    with pytest.raises(ValidationError):
        ForecastConfig(**kwargs)


@pytest.mark.parametrize("kwargs", [
    {"planning_confidence": 1}, {"runway_min_probability": 0},
    {"runway_lasts_below": 0.5, "runway_runs_out_above": 0.5},
    {"runway_min_probability": 0.2, "runway_lasts_below": 0.2},
])
def test_forecast_config_accepts_the_boundaries(kwargs):
    assert ForecastConfig(**kwargs)


def test_api_yaml_forecast_block_matches_the_defaults():
    from server.api.config import load_config
    assert load_config("config/api.yaml").forecast == ForecastConfig()


# ------------------------------------------------------------------------------ spent budgets

def test_within_probability_is_exactly_zero_once_nothing_is_left():
    """`remaining <= 0` is 0.0 even where the distribution itself has mass at 0: an inactive
    scope (every quantile 0) with the budget exactly spent would otherwise read as 1.0."""
    zeros = [0] * 21
    args = dict(start=date(2026, 10, 1), end=date(2026, 10, 1), forecast_from=date(2026, 10, 1),
                dates=[date(2026, 10, 1)], cumulative_expected=[0], cumulative_quantiles=[zeros],
                total_expected=0, total_quantiles=zeros, baseline_total_expected=None,
                grid=GRID, band_low=10, band_high=90)
    assert fc.quantile_cdf(GRID, zeros, 0) == 1.0                     # the raw cdf of an all-zero row
    assert fc.compose_period(actual_to_date=700, budget=700, **args)["within_budget_probability"] == 0.0
    assert fc.compose_period(actual_to_date=700, budget=699, **args)["within_budget_probability"] == 0.0
    assert fc.compose_period(actual_to_date=0, budget=0, **args)["within_budget_probability"] == 0.0
    assert fc.compose_period(actual_to_date=0, budget=1, **args)["within_budget_probability"] == 1.0
    assert fc.compose_period(actual_to_date=0, budget=None, **args)["within_budget_probability"] is None
