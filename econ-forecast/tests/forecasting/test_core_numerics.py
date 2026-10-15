"""Focused numerical oracles for ETS fit tiering, simulation, joint-bootstrap
nonnegative correction, and allocation (design doc §3, §5, §6)."""

from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np

from forecast.forecasting.allocation import allocation
from forecast.forecasting.ets import fit_ets
from forecast.forecasting.paths import simulate_joint


def _seasonal_trend_series(n=56, seed=0):
    """A deterministic 7-day seasonal + linear-trend + noise fixture (mirrors the
    numerical preflight fixture) -- long enough (>=28) for the full auto tier table.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    seasonal = np.tile([1, 0, -1, 2, -2, 0, 1], n // 7 + 1)[:n]
    return 100 + 0.5 * t + 3 * seasonal + rng.normal(0, 1.0, n)


def test_short_history_forces_level_only_tier():
    y = 50 + np.random.default_rng(1).normal(0, 3, 20)  # 14-27 days: level-only only
    fit, attempts = fit_ets(y, horizon=5)
    assert fit is not None
    assert len(attempts) == 1
    assert attempts[0]["trend"] is None and attempts[0]["seasonal"] is None


def test_long_history_prefers_seasonal_damped_trend_when_it_converges():
    y = _seasonal_trend_series()
    fit, attempts = fit_ets(y, horizon=21)
    assert fit is not None
    assert attempts[0]["trend"] == "add" and attempts[0]["seasonal"] == "add"
    assert attempts[0]["converged"] is True
    assert len(attempts) == 1  # first tier accepted; no fallback needed


def test_seasonal_none_never_attempts_a_seasonal_configuration():
    y = _seasonal_trend_series()
    fit, attempts = fit_ets(y, horizon=21, seasonal="none", trend="auto")
    assert fit is not None
    assert all(a["seasonal"] is None for a in attempts)
    # The design's fixed 28+ candidate table has no trend-only (seasonal=None,
    # trend="add") tier -- only seasonal+damped-trend, seasonal-only, and level-only.
    # Forcing seasonal off therefore collapses every candidate to level-only; trend=
    # "auto" has no remaining tier to express itself in. This is the documented
    # limitation of filtering the fixed table rather than inventing a new tier.
    assert len(attempts) == 1
    assert attempts[0]["trend"] is None


def test_trend_none_keeps_seasonal_only_tier_and_never_attempts_trend():
    y = _seasonal_trend_series()
    fit, attempts = fit_ets(y, horizon=21, seasonal="auto", trend="none")
    assert fit is not None
    assert all(a["trend"] is None for a in attempts)
    assert attempts[0]["seasonal"] == "add"  # seasonal-only tier tried before level-only


def test_seasonal_and_trend_none_always_level_only_regardless_of_length():
    y = _seasonal_trend_series()
    fit, attempts = fit_ets(y, horizon=21, seasonal="none", trend="none")
    assert fit is not None
    assert len(attempts) == 1
    assert attempts[0]["trend"] is None and attempts[0]["seasonal"] is None


def test_exact_constant_series_uses_analytic_branch_with_zero_residuals():
    y = np.full(30, 42.0)
    fit, attempts = fit_ets(y, horizon=5)
    assert fit is not None and fit.analytic is True
    assert attempts[0]["analytic"] is True
    # statsmodels' internal recursion leaves ~1e-15-scale floating-point noise even for
    # an exact-constant series with a degenerate (zero-variance) likelihood; the design's
    # analytic-branch contract is "no optimizer ran / no injected noise", not bitwise zero.
    np.testing.assert_allclose(fit.residuals, np.zeros(30), atol=1e-9)
    np.testing.assert_allclose(fit.forecast, np.full(5, 42.0), atol=1e-9)


def test_simulate_shape_and_bitwise_replay_with_explicit_errors():
    y = _seasonal_trend_series()
    fit, _ = fit_ets(y, horizon=10)
    errors = np.random.default_rng(2).normal(0, 1.0, size=(10, 6))
    first = fit.simulate(10, errors)
    replay = fit.simulate(10, errors)
    assert first.shape == (10, 6)
    np.testing.assert_array_equal(first, replay)


def test_simulate_zero_errors_reproduces_forecast_exactly():
    y = _seasonal_trend_series()
    fit, _ = fit_ets(y, horizon=10)
    zeros = np.zeros((10, 3))
    simulated = fit.simulate(10, zeros)
    for column in range(3):
        np.testing.assert_allclose(simulated[:, column], fit.forecast, atol=1e-7)


class _FakeFit:
    """Duck-typed stand-in for `ets.Fit`: `simulate_joint` only needs `.residuals`
    and a `.simulate(horizon, errors)` callable, letting the correction arithmetic be
    exercised with a hand-picked deterministic raw simulation grid."""

    def __init__(self, residuals, raw):
        self.residuals = residuals
        self._raw = np.asarray(raw, dtype=np.float64)

    def simulate(self, horizon, errors):
        return self._raw


def _dates(n=14, start="2026-07-01"):
    first = date.fromisoformat(start)
    return [first + timedelta(days=i) for i in range(n)]


def _item(raw, dates=None):
    dates = dates or _dates()
    scope = SimpleNamespace(org_id="org-a", scope_type="organization", scope_id="org-a")
    history = SimpleNamespace(scope=scope, model="model-a", dates=dates)
    return {
        "history": history,
        "status": "qualified",
        "reasons": [],
        "requested_idx": np.arange(raw.shape[0]),
        "fit": _FakeFit(np.zeros(len(dates)), raw),
        "paths": None,
    }


def test_correction_within_tolerance_stays_qualified_with_exact_stats():
    raw = np.array([[100.0, 100.0, 100.0, 100.0], [-5.0, 5.0, 15.0, 25.0]])
    item = _item(raw)
    simulate_joint([item], _dates()[:2], paths=4, seed=1, tolerance=0.05)
    assert item["status"] == "qualified"
    np.testing.assert_array_equal(item["paths"], [[100.0, 100.0, 100.0, 100.0], [0.0, 5.0, 15.0, 25.0]])
    correction = item["correction"]
    assert correction["removed_tokens"] == 5.0
    assert correction["corrected_tokens"] == 445.0
    assert correction["ratio"] == 5.0 / 445.0
    assert correction["passed"] is True


def test_correction_exceeding_tolerance_stays_qualified_but_flagged_not_a_new_status():
    """Design decision (veto on the earlier proposal): the reader derives scope
    status/risk_ready purely from series statuses, so a correction-ratio failure must
    NOT change the series status -- only `item["correction"]["passed"]` records it.
    """
    raw = np.array([[10.0, 10.0, 10.0, 10.0], [-50.0, -50.0, -50.0, -50.0]])
    item = _item(raw)
    simulate_joint([item], _dates()[:2], paths=4, seed=1, tolerance=0.05)
    assert item["status"] == "qualified"  # unchanged -- no "correction_failed" status
    correction = item["correction"]
    assert correction["removed_tokens"] == 200.0
    assert correction["corrected_tokens"] == 40.0
    assert correction["ratio"] == 5.0
    assert correction["passed"] is False
    np.testing.assert_array_equal(item["paths"], [[10.0, 10.0, 10.0, 10.0], [0.0, 0.0, 0.0, 0.0]])


def test_nonfinite_simulation_result_fails_sampling():
    raw = np.array([[1.0, np.inf], [1.0, 2.0]])
    item = _item(raw)
    simulate_joint([item], _dates()[:2], paths=2, seed=1, tolerance=0.05)
    assert item["status"] == "sampling_failed"
    assert "nonfinite" in item["reasons"][0]
    assert item["paths"] is None


def test_fewer_than_fourteen_shared_dates_fails_sampling():
    long_item = _item(np.zeros((2, 2)), dates=_dates(20))
    short_item = _item(np.zeros((2, 2)), dates=_dates(5))
    simulate_joint(
        [long_item, short_item], _dates(2), paths=2, seed=1, tolerance=0.05
    )
    for item in (long_item, short_item):
        assert item["status"] == "sampling_failed"
        assert "fewer than 14 shared residual dates" in item["reasons"][0]


def test_allocation_is_token_weighted_joint_agent_category_share():
    history = SimpleNamespace(
        categories={
            ("a", "input"): 90,
            ("a", "cache_read"): 10,
            ("a", "output"): 0,
            ("b", "input"): 0,
            ("b", "cache_read"): 0,
            ("b", "output"): 100,
        }
    )
    shares, tokens, total = allocation(history)
    assert total == 200 and tokens[0]["input"] == 90 and tokens[1]["output"] == 100
    assert shares == [
        {"agent_type": "a", "input": 0.45, "cache_write_5m": 0.0, "cache_write_1h": 0.0,
         "cache_read": 0.05, "output": 0.0},
        {"agent_type": "b", "input": 0.0, "cache_write_5m": 0.0, "cache_write_1h": 0.0,
         "cache_read": 0.0, "output": 0.5},
    ]
    assert sum(row[key] for row in shares for key in ("input", "cache_read", "output")) == 1
