"""X5 forecasts (`server/api/forecast.py`). Pure functions -- no DB needed."""
from server.api import forecast as fc


# ------------------------------------------------------------------------------- envelope

def test_percentile_matches_hand_computed_fixture():
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert fc.percentile(values, 0.25) == 20.0
    assert fc.percentile(values, 0.5) == 30.0
    assert fc.percentile(values, 0.75) == 40.0
    assert fc.percentile(values, 0.9) == 46.0


def test_percentile_single_value_is_that_value():
    assert fc.percentile([42.0], 0.5) == 42.0
    assert fc.percentile([42.0], 0.99) == 42.0


def test_cost_per_pr_envelope_matches_a_fixture_distribution():
    """X5 Verify: envelope percentiles match a fixture distribution."""
    result = fc.cost_per_pr_envelope([10, 20, 30, 40, 50])
    assert result == {
        "min_usd_micros": 10, "p25_usd_micros": 20, "median_usd_micros": 30,
        "p75_usd_micros": 40, "p90_usd_micros": 46, "p99_usd_micros": 50,
        "max_usd_micros": 50,
    }


def test_cost_per_pr_envelope_is_none_cold_start_with_no_merged_prs():
    assert fc.cost_per_pr_envelope([]) is None


def test_dist_stats_matches_a_hand_computed_fixture():
    result = fc.dist_stats([10.0, 20.0, 30.0, 40.0, 50.0])
    assert result["avg"] == 30.0
    assert result["min"] == 10.0
    assert result["max"] == 50.0
    assert result["p50"] == 30.0
    assert result["p70"] == fc.percentile([10.0, 20.0, 30.0, 40.0, 50.0], 0.7)


def test_dist_stats_is_none_cold_start_with_no_values():
    assert fc.dist_stats([]) is None


# ------------------------------------------------------------------------------- forecast cone

def test_forecast_cone_widens_monotonically():
    """X5 Verify: forecast cone widens monotonically."""
    envelope = fc.cost_per_pr_envelope([10, 20, 30, 40, 50])
    cone = fc.forecast_cone(envelope, periods_ahead=6)
    widths = [c["band_hi_usd_micros"] - c["band_lo_usd_micros"] for c in cone]
    assert widths == sorted(widths)
    assert len(set(widths)) == len(widths)  # strictly increasing, no plateau


def test_forecast_cone_step_zero_is_the_handoff_continuous_with_today():
    envelope = fc.cost_per_pr_envelope([10, 20, 30, 40, 50])
    cone = fc.forecast_cone(envelope, periods_ahead=3)
    assert cone[0]["band_lo_usd_micros"] == envelope["p25_usd_micros"]
    assert cone[0]["band_hi_usd_micros"] == envelope["p75_usd_micros"]
    assert cone[0]["median_usd_micros"] == envelope["median_usd_micros"]


def test_forecast_cone_empty_with_no_envelope_or_negative_periods():
    assert fc.forecast_cone(None, 5) == []
    envelope = fc.cost_per_pr_envelope([10, 20, 30])
    assert fc.forecast_cone(envelope, -1) == []


# ------------------------------------------------------------------------------- budget burn-up

def test_budget_burnup_actual_and_projected_are_continuous_at_the_handoff():
    """X5 Verify: burn-up actual+projected are continuous at the handoff."""
    points = fc.budget_burnup([100, 100, 100], days_in_period=5)
    handoff = points[2]
    assert handoff["day"] == 3
    assert handoff["actual_usd_micros"] == 300
    assert handoff["projected_usd_micros"] == 300  # same value, not a different basis


def test_budget_burnup_actual_is_cumulative_and_projection_holds_the_average_rate_flat():
    points = fc.budget_burnup([100, 100, 100], days_in_period=5)
    assert [p.get("actual_usd_micros") for p in points[:3]] == [100, 200, 300]
    assert points[3] == {"day": 4, "projected_usd_micros": 400}
    assert points[4] == {"day": 5, "projected_usd_micros": 500}


def test_budget_burnup_is_none_cold_start_with_no_days_reported():
    assert fc.budget_burnup([], days_in_period=30) is None


# ------------------------------------------------------------------------------- cost-to-finish

def test_feature_cost_to_finish_equals_open_prs_times_envelope_bounds():
    """X5 Verify: cost-to-finish = openPRs × envelope bounds."""
    envelope = fc.cost_per_pr_envelope([10, 20, 30, 40, 50])
    result = fc.feature_cost_to_finish(2, envelope)
    assert result["low_usd_micros"] == 2 * envelope["p25_usd_micros"]
    assert result["mid_usd_micros"] == 2 * envelope["median_usd_micros"]
    assert result["high_usd_micros"] == 2 * envelope["p75_usd_micros"]
    assert result["open_prs"] == 2
    assert "2 open PRs" in result["basis"]


def test_feature_cost_to_finish_zero_open_prs_is_a_real_zero_not_cold_start():
    envelope = fc.cost_per_pr_envelope([10, 20, 30])
    result = fc.feature_cost_to_finish(0, envelope)
    assert result == {
        "open_prs": 0, "low_usd_micros": 0, "mid_usd_micros": 0, "high_usd_micros": 0,
        "basis": "No open PRs remain.",
    }


def test_feature_cost_to_finish_is_none_cold_start_with_no_envelope():
    assert fc.feature_cost_to_finish(3, None) is None


def test_feature_cost_to_finish_singular_pr_wording():
    envelope = fc.cost_per_pr_envelope([10, 20, 30])
    result = fc.feature_cost_to_finish(1, envelope)
    assert "1 open PR " in result["basis"]
    assert "PRs" not in result["basis"]
