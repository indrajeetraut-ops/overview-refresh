"""Independent hand-oracle checks for frozen history and allocation contracts."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from forecast.forecasting.allocation import TOKEN_TYPES, allocation
from forecast.forecasting.config import load_config
from forecast.forecasting.dataset import PreparedDataset, ScopeMeta, load_dataset
from forecast.forecasting.errors import InputError
from forecast.forecasting.history import SeriesHistory, build_histories


def _one_history(dataset, origin, *, minimum=14, active=3, allocation_days=28):
    loaded = load_dataset(dataset)
    scope = loaded.scopes[("org-a", "organization", "org-a")]
    histories, _ = build_histories(loaded, [scope], origin, lookback=90, minimum=minimum,
                                   active_days=active, allocation_days=allocation_days)
    assert len(histories) == 1
    return histories[0]


def test_hist01_counts_distinct_dates_not_rows(daily_dataset_factory, tmp_path):
    thirteen = daily_dataset_factory(tmp_path / "thirteen", days=13, users=("u1", "u2", "u3", "u4"),
                                     token_fn=lambda *_: (10, 0, 0))
    history = _one_history(thirteen, date(2026, 7, 13))
    assert len(history.dates) == 13
    assert history.status == "insufficient_history"
    fourteen = daily_dataset_factory(tmp_path / "fourteen", days=14, users=("u1", "u2", "u3", "u4"),
                                     token_fn=lambda *_: (10, 0, 0))
    history = _one_history(fourteen, date(2026, 7, 14))
    assert len(history.dates) == 14
    assert history.status == "qualified"


@pytest.mark.parametrize("outage_day,retained", [(10, 21), (25, 6), (31, 0)])
def test_hist02_outage_truncates_continuous_suffix(daily_dataset_factory, tmp_path, outage_day, retained):
    dataset = daily_dataset_factory(
        tmp_path / f"outage-{outage_day}", start=date(2026, 8, 1), days=31,
        token_fn=lambda *_: (10, 0, 0),
        coverage_fn=lambda index, *_: "partial" if index == outage_day - 1 else "complete",
    )
    history = _one_history(dataset, date(2026, 8, 31))
    assert len(history.dates) == retained
    assert history.status == ("qualified" if retained >= 14 else "insufficient_history")


def test_hist03_fourteen_days_cross_month_boundary(daily_dataset_factory, tmp_path):
    dataset = daily_dataset_factory(tmp_path / "cross-month", start=date(2026, 8, 25), days=14,
                                    token_fn=lambda *_: (5, 0, 0))
    history = _one_history(dataset, date(2026, 9, 7))
    assert history.dates == [date(2026, 8, 25) + timedelta(days=i) for i in range(14)]
    assert history.status == "qualified"


def test_hist04_first_x3_streak_and_x1_x7_calendar_rules(daily_dataset_factory, tmp_path):
    sequence = [0, 10, 0, 20, 30, 40, 0, 50] + [10] * 12
    dataset = daily_dataset_factory(tmp_path / "x3", days=len(sequence),
                                    token_fn=lambda index, *_: (sequence[index], 0, 0))
    history = _one_history(dataset, date(2026, 7, len(sequence)), active=3)
    assert history.startup_start == date(2026, 7, 4)
    assert history.dates[0] == date(2026, 7, 4)

    single = daily_dataset_factory(tmp_path / "x1", days=14,
                                   token_fn=lambda index, *_: (1 if index == 0 else 0, 0, 0))
    assert _one_history(single, date(2026, 7, 14), active=1).status == "qualified"
    weekdays = daily_dataset_factory(
        tmp_path / "x7", start=date(2026, 7, 6), days=21,
        token_fn=lambda index, *_: (10 if (date(2026, 7, 6) + timedelta(days=index)).weekday() < 5 else 0, 0, 0),
    )
    history = _one_history(weekdays, date(2026, 7, 26), active=7)
    assert history.status == "insufficient_startup"
    assert history.startup_start is None


@pytest.mark.parametrize("value", [0, True, 91])
def test_hist04_active_day_type_and_bounds_rejected(
    daily_dataset_factory, forecast_config_factory, tmp_path, value
):
    dataset = daily_dataset_factory(tmp_path / f"dataset-{value}", days=14)
    config = forecast_config_factory(tmp_path / f"bad-{value}.yaml", dataset,
                                     history={"minimum_active_days": value})
    with pytest.raises(InputError):
        load_config(config)


def test_hist05_daily_positivity_aggregates_split_agents():
    # Neither agent is positive on every startup day; the scope/model daily total is positive.
    start = date(2026, 7, 1)
    scope = ScopeMeta("org-a", "organization", "org-a", "Organization A",
                      frozenset({("u1", "agent-a"), ("u2", "agent-b")}))
    coverage = {(start + timedelta(days=i), "org-a", user, agent): "complete"
                for i in range(16) for user, agent in scope.keys}
    usage = {}
    for i in range(16):
        agent = "agent-a" if i % 2 == 0 else "agent-b"
        usage[("org-a", "organization", "org-a", "model-a", start + timedelta(days=i),
               agent, "input")] = 10.0
    data = PreparedDataset(start, start + timedelta(days=15), "UTC",
                           {"org-a": {"model-a": frozenset({"agent-a", "agent-b"})}},
                           {("org-a", "organization", "org-a"): scope}, usage, coverage, [], {}, {})
    histories, _ = build_histories(data, [scope], start + timedelta(days=15), lookback=90,
                                   minimum=14, active_days=3, allocation_days=28)
    history = histories[0]
    assert history.startup_start == date(2026, 7, 1)
    assert history.status == "qualified"


def test_hist05_scope_models_are_checked_independently(daily_dataset_factory, tmp_path):
    dataset = daily_dataset_factory(
        tmp_path / "models", days=18,
        models=(("qualified-model", "agent-a"), ("scattered-model", "agent-b")),
        token_fn=lambda day_index, model_index, _user:
            ((10, 0, 0) if model_index == 0 or day_index in (0, 4, 8, 12, 16) else (0, 0, 0)),
    )
    loaded = load_dataset(dataset)
    scope = loaded.scopes[("org-a", "organization", "org-a")]
    histories, _ = build_histories(loaded, [scope], date(2026, 7, 18), lookback=90,
                                   minimum=14, active_days=4, allocation_days=28)
    assert {item.model: item.status for item in histories} == {
        "qualified-model": "qualified", "scattered-model": "insufficient_startup"
    }


def test_hist06_all_zero_floor_is_inactive_but_subminimum_fails(daily_dataset_factory, tmp_path):
    fourteen = daily_dataset_factory(tmp_path / "zero14", days=14, token_fn=lambda *_: None)
    history = _one_history(fourteen, date(2026, 7, 14))
    assert history.status == "inactive"
    assert np.array_equal(history.values, np.zeros(14))
    thirteen = daily_dataset_factory(tmp_path / "zero13", days=13, token_fn=lambda *_: None)
    assert _one_history(thirteen, date(2026, 7, 13)).status == "insufficient_history"


def test_hist07_qualification_persists_through_taper_retirement_and_restart(daily_dataset_factory, tmp_path):
    def amount(index):
        return 10 if index < 4 or index in (10, 17, 24) else 0
    taper = daily_dataset_factory(tmp_path / "taper", days=28,
                                  token_fn=lambda index, *_: (amount(index), 0, 0))
    assert _one_history(taper, date(2026, 7, 28), active=4, allocation_days=28).status == "qualified"
    retired = _one_history(taper, date(2026, 7, 28), active=4, allocation_days=3)
    assert retired.status == "inactive"
    restarted = daily_dataset_factory(tmp_path / "restart", days=28,
        token_fn=lambda index, *_: (10 if index < 4 or index == 27 else 0, 0, 0))
    assert _one_history(restarted, date(2026, 7, 28), active=4, allocation_days=3).status == "qualified"
    weekly = daily_dataset_factory(tmp_path / "never-qualified", days=28,
        token_fn=lambda index, *_: (10 if index % 7 == 0 else 0, 0, 0))
    assert _one_history(weekly, date(2026, 7, 28), active=4).status == "insufficient_startup"


def test_hist08_leading_zeros_then_seven_vs_fourteen_days_from_start(daily_dataset_factory, tmp_path):
    short = daily_dataset_factory(tmp_path / "short", days=12,
        token_fn=lambda index, *_: (10 if 5 <= index <= 7 else 0, 0, 0))
    history = _one_history(short, date(2026, 7, 12), active=3)
    assert history.startup_start == date(2026, 7, 6)
    assert len(history.dates) == 7 and history.status == "insufficient_history"
    long = daily_dataset_factory(tmp_path / "long", days=19,
        token_fn=lambda index, *_: (10 if 5 <= index <= 7 else 0, 0, 0))
    history = _one_history(long, date(2026, 7, 19), active=3)
    assert len(history.dates) == 14 and history.status == "qualified"
    future = daily_dataset_factory(tmp_path / "future", days=20,
        token_fn=lambda index, *_: (10 if index >= 14 else 0, 0, 0))
    at_earlier_origin = _one_history(future, date(2026, 7, 14), active=3)
    assert at_earlier_origin.startup_start is None
    assert at_earlier_origin.status == "inactive"


def test_hist09_recent_zero_window_vs_one_positive(daily_dataset_factory, tmp_path):
    inactive = daily_dataset_factory(tmp_path / "inactive", days=20,
        token_fn=lambda index, *_: (10 if index < 4 else 0, 0, 0))
    history = _one_history(inactive, date(2026, 7, 20), active=4, allocation_days=7)
    shares, tokens, total = allocation(history)
    assert history.status == "inactive"
    assert (shares, tokens, total) == ([], [], 0.0)
    low = daily_dataset_factory(tmp_path / "low", days=20,
        token_fn=lambda index, *_: (10 if index < 4 else (1 if index == 19 else 0), 0, 0))
    history = _one_history(low, date(2026, 7, 20), active=4, allocation_days=7)
    assert history.status == "qualified"
    assert history.categories == {("agent-a", "input"): 1.0,
                                  ("agent-a", "cache_write_5m"): 0.0,
                                  ("agent-a", "cache_write_1h"): 0.0,
                                  ("agent-a", "cache_read"): 0.0,
                                  ("agent-a", "output"): 0.0}


def _allocation_history(categories):
    return SeriesHistory(None, "model", [], np.array([], dtype=np.float64), categories, None,
                         None, None, None, "qualified", [])


def test_alloc01_one_million_hand_oracle():
    shares, tokens, total = allocation(_allocation_history({
        ("agent-a", "input"): 100_000.0, ("agent-a", "cache_read"): 800_000.0,
        ("agent-a", "output"): 100_000.0,
    }))
    assert total == 1_000_000.0
    assert tokens == [{"agent_type": "agent-a", "input": 100_000.0,
                       "cache_write_5m": 0.0, "cache_write_1h": 0.0,
                       "cache_read": 800_000.0, "output": 100_000.0}]
    assert shares == [{"agent_type": "agent-a", "input": 0.1, "cache_write_5m": 0.0,
                       "cache_write_1h": 0.0, "cache_read": 0.8, "output": 0.1}]
    assert [500_000 * shares[0][key] for key in ("input", "cache_read", "output")] == [50_000, 400_000, 50_000]


def test_alloc01b_five_type_shares_worked_example():
    # Cache-write contract design 6: the 2.2 row (T=1,000,000 plus output 30,000).
    shares, tokens, total = allocation(_allocation_history({
        ("agent-a", "input"): 600.0, ("agent-a", "cache_write_5m"): 5_982.0,
        ("agent-a", "cache_write_1h"): 193_418.0, ("agent-a", "cache_read"): 800_000.0,
        ("agent-a", "output"): 30_000.0,
    }))
    assert total == 1_030_000.0
    expected = {"input": 0.000583, "cache_write_5m": 0.005808, "cache_write_1h": 0.187784,
                "cache_read": 0.776699, "output": 0.029126}
    assert {key: round(shares[0][key], 6) for key in expected} == expected
    assert sum(shares[0][key] for key in expected) == pytest.approx(1.0, abs=1e-12)


def test_alloc02_token_weighted_not_daily_percentage_average(daily_dataset_factory, tmp_path):
    dataset = daily_dataset_factory(
        tmp_path / "weighted", days=14,
        token_fn=lambda index, *_: ((900, 0, 100) if index == 0 else
                                    ((0, 90, 10) if index == 1 else (0, 0, 0))),
    )
    history = _one_history(dataset, date(2026, 7, 14), active=1, allocation_days=14)
    assert history.categories == {("agent-a", "input"): 900.0,
                                  ("agent-a", "cache_write_5m"): 0.0,
                                  ("agent-a", "cache_write_1h"): 0.0,
                                  ("agent-a", "cache_read"): 90.0,
                                  ("agent-a", "output"): 110.0}
    shares, _, total = allocation(history)
    assert total == 1100.0
    np.testing.assert_allclose([shares[0]["input"], shares[0]["cache_read"], shares[0]["output"]],
                               [900 / 1100, 90 / 1100, 110 / 1100], rtol=0, atol=1e-15)


def test_alloc04_recent_window_excludes_old_outage_and_future(daily_dataset_factory, tmp_path):
    dataset = daily_dataset_factory(
        tmp_path / "window", days=40,
        token_fn=lambda index, *_: ((999_999, 0, 0) if index < 30 or index > 34 else (10, 20, 30)),
        coverage_fn=lambda index, *_: "partial" if index == 20 else "complete",
    )
    history = _one_history(dataset, date(2026, 8, 4), active=3, allocation_days=5)
    assert history.category_window_start == date(2026, 7, 31)
    assert history.categories == {("agent-a", "input"): 50.0,
                                  ("agent-a", "cache_write_5m"): 0.0,
                                  ("agent-a", "cache_write_1h"): 0.0,
                                  ("agent-a", "cache_read"): 100.0,
                                  ("agent-a", "output"): 150.0}
    shares, _, total = allocation(history)
    assert total == 300.0
    np.testing.assert_allclose([shares[0]["input"], shares[0]["cache_read"], shares[0]["output"]],
                               [1 / 6, 1 / 3, 1 / 2], rtol=0, atol=1e-15)


def test_alloc05_fixed_joint_ratios_conserve_every_day_path_including_zero():
    shares, _, _ = allocation(_allocation_history({
        ("agent-a", "input"): 20.0, ("agent-a", "cache_read"): 30.0,
        ("agent-a", "output"): 0.0, ("agent-b", "input"): 10.0,
        ("agent-b", "cache_read"): 0.0, ("agent-b", "output"): 40.0,
    }))
    paths = np.array([[0.0, 50.0, 125.5], [10.0, 1000.0, 3.0]])
    assert shares == [
        {"agent_type": "agent-a", "input": 0.2, "cache_write_5m": 0.0, "cache_write_1h": 0.0,
         "cache_read": 0.3, "output": 0.0},
        {"agent_type": "agent-b", "input": 0.1, "cache_write_5m": 0.0, "cache_write_1h": 0.0,
         "cache_read": 0.0, "output": 0.4},
    ]
    ratios = np.array([[row[key] for row in shares for key in TOKEN_TYPES]])
    allocated = paths[:, :, None] * ratios
    np.testing.assert_allclose(allocated.sum(axis=2), paths, rtol=0, atol=1e-12)
    for column, ratio in enumerate(ratios[0]):
        np.testing.assert_allclose(allocated[:, :, column], paths * ratio, rtol=0, atol=1e-12)
    assert np.all(allocated[paths == 0] == 0)
