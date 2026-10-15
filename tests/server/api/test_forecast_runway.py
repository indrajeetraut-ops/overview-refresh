"""T6 `budget_runway` / `period_runway` (plan §3.8), pure: statuses, the exact threshold
boundaries, the exhausted case, run-out dates for a hand-built set of per-date rows, and the
monotonicity properties. No database."""
from datetime import date, timedelta

import pytest

from server.api import forecast as fc

GRID = list(range(0, 101, 5))
START = date(2026, 10, 1)
D0 = date(2026, 10, 9)                                   # forecast_from
DATES = [D0 + timedelta(days=k) for k in range(5)]       # Oct 9..13
END = DATES[-1]
# row k (day k of the forecast), percentile index i -> (k + 1) * (100 + 10 * i): a distribution
# whose scale grows with the day. Row 4 (the period total) runs 500, 550, ..., 1500.
ROWS = [[(k + 1) * (100 + 10 * i) for i in range(21)] for k in range(5)]
DEFAULTS = dict(lasts_below=0.2, runs_out_above=0.8, min_probability=0.05)


def _runway(budget, actual=0, daily=None, rows=ROWS, within=None, **over):
    remaining = budget - actual
    if within is None:
        within = 0.0 if remaining <= 0 else fc.quantile_cdf(GRID, rows[-1], remaining)
    args = dict(start=START, end=END, dates=DATES, cumulative_quantiles=rows, grid=GRID,
                actual_to_date=actual, budget=budget, within_budget_probability=within,
                daily_actuals=daily or {}, **DEFAULTS)
    args.update(over)
    return fc.budget_runway(**args)


# ------------------------------------------------------------------------------ the dates

def test_run_out_dates_for_a_hand_built_set_of_rows():
    # budget 1000, nothing spent. Row k holds (k+1) * (100 + 10 i): the chance that cumulative
    # cost by day k exceeds 1000 is 1 - cdf:
    #   day 0 (100..300), 1 (200..600), 2 (300..900): max below 1000 -> 0
    #   day 3 (400..1200): 400 + 40 i = 1000 at i = 15 -> cdf 0.75 -> P(out by) 0.25
    #   day 4 (500..1500): 500 + 50 i = 1000 at i = 10 -> cdf 0.50 -> P(out by) 0.50 = P(out)
    r = _runway(1000)
    assert r["status"] == "at_risk"
    assert r["runs_out_probability"] == 0.5
    # targets: q=0.1 -> 0.05, q=0.5 -> 0.25, q=0.9 -> 0.45. Earliest day reaching each:
    # 0.05 and 0.25 -> day 3 (Oct 12); 0.45 -> day 4 (Oct 13)
    assert r["runs_out_on"] == {"p10": date(2026, 10, 12), "p50": date(2026, 10, 12),
                                "p90": date(2026, 10, 13)}
    assert r["days_short"] == 1            # Oct 13 - Oct 12


def test_a_certain_overrun_runs_out_from_the_first_forecast_day():
    # budget 50: every day-0 value (100..300) exceeds it -> P(out by day 0) = 1
    r = _runway(50)
    assert r["status"] == "runs_out" and r["runs_out_probability"] == 1.0
    assert r["runs_out_on"] == {"p10": D0, "p50": D0, "p90": D0}
    assert r["days_short"] == 4


def test_actual_spend_shifts_the_dates_earlier():
    # budget 1000 with 500 already spent leaves R = 500. Total row (500, 550, ...): cdf(500) = 0,
    # so P(out) = 1. Chance of being out by each day: day 0 (100..300) 0; day 1 (200 + 20 i):
    # 500 at i = 15 -> .25; day 2 (300 + 30 i): between i = 6 (480) and 7 (510) ->
    # (30 + 5 * 20/30) / 100 = .3333 -> .6667; day 3 (400 + 40 i): i = 2 (480), 3 (520) ->
    # .125 -> .875; day 4 -> 1. Targets .1 / .5 / .9 -> day 1, day 2, day 4.
    r = _runway(1000, actual=500, daily={START: 500})
    assert r["status"] == "runs_out" and r["runs_out_probability"] == 1.0
    assert r["runs_out_on"] == {"p10": date(2026, 10, 10), "p50": date(2026, 10, 11),
                                "p90": date(2026, 10, 13)}
    assert r["days_short"] == 2


def test_p_out_by_d_is_non_decreasing_in_d_for_every_budget():
    for budget in range(0, 2400, 13):
        remaining = budget
        if remaining <= 0:
            continue
        previous = 0.0
        for row in ROWS:
            p = 1 - fc.quantile_cdf(GRID, row, remaining)
            assert p >= previous
            previous = p


# ------------------------------------------------------------------------------ the statuses

@pytest.mark.parametrize("within, status", [
    (0.81, "lasts"),          # P(out) .19
    (0.8, "at_risk"),         # P(out) = 1 - 0.8 = 0.19999999999999996 in floats, exactly the 0.2 cut-off: not below it
    (0.79, "at_risk"),        # .21
    (0.5, "at_risk"),
    (0.21, "at_risk"),        # .79
    (0.2, "at_risk"),         # 1 - 0.2 = 0.8, exactly the 0.8 cut-off: not above it
    (0.19, "runs_out"),       # .81
    (0.0, "runs_out"),
    (1.0, "lasts"),
])
def test_status_boundaries_are_exact(within, status):
    assert _runway(1000, within=within)["status"] == status


def test_status_follows_the_configured_thresholds():
    assert _runway(1000, within=0.5, lasts_below=0.6)["status"] == "lasts"
    assert _runway(1000, within=0.5, lasts_below=0.4, runs_out_above=0.45)["status"] == "runs_out"
    assert _runway(1000, within=0.5, lasts_below=0.5)["status"] == "at_risk"       # P(out) == lasts_below
    assert _runway(1000, within=0.5, runs_out_above=0.5, lasts_below=0.2)["status"] == "at_risk"


@pytest.mark.parametrize("within, has_dates", [
    (0.95, True),             # P(out) = 0.050000000000000044 == the 0.05 minimum: dates are given
    (0.949, True),            # .051
    (0.96, False),            # .04 -> all None
    (1.0, False),
    (0.0, True),
])
def test_run_out_dates_are_null_below_the_minimum_probability(within, has_dates):
    r = _runway(1000, within=within)
    assert (r["runs_out_on"]["p10"] is not None) == has_dates
    assert (r["runs_out_on"]["p50"] is not None) == has_dates
    assert (r["runs_out_on"]["p90"] is not None) == has_dates
    assert (r["days_short"] is not None) == has_dates


def test_nothing_runs_out_when_the_budget_exceeds_every_outcome():
    r = _runway(5000)                               # row 4 maxes at 1500
    assert r["status"] == "lasts"
    assert r["runs_out_probability"] == 0.0
    assert r["runs_out_on"] == {"p10": None, "p50": None, "p90": None}
    assert r["days_short"] is None


def test_probabilities_sum_to_exactly_one():
    for budget in list(range(1, 2200, 7)) + [1000, 1525, 700]:
        remaining = budget
        within = fc.quantile_cdf(GRID, ROWS[-1], remaining)
        r = _runway(budget, within=within)
        assert within + r["runs_out_probability"] == 1.0


# ------------------------------------------------------------------------------ exhausted

def test_exhausted_reports_the_day_actual_spend_crossed_the_budget():
    daily = {date(2026, 10, 1): 400, date(2026, 10, 2): 0, date(2026, 10, 3): 450,
             date(2026, 10, 4): 300, date(2026, 10, 5): 999}
    # budget 1000: running 400, 400, 850, 1150 -> crossed on Oct 4. actual to date = 400+450+300+999
    r = _runway(1000, actual=2149, daily=daily, within=0.0)
    assert r["status"] == "exhausted"
    assert r["runs_out_probability"] == 1.0
    assert r["runs_out_on"] == {"p10": date(2026, 10, 4), "p50": date(2026, 10, 4),
                                "p90": date(2026, 10, 4)}
    assert r["days_short"] == (END - date(2026, 10, 4)).days == 9


def test_exhausted_exactly_at_the_budget_and_on_the_first_day():
    daily = {date(2026, 10, 2): 600, date(2026, 10, 3): 400}
    r = _runway(1000, actual=1000, daily=daily, within=0.0)       # R == 0
    assert r["status"] == "exhausted" and r["runs_out_on"]["p50"] == date(2026, 10, 3)
    one_day = {START: 5000}
    r = _runway(1000, actual=5000, daily=one_day, within=0.0)
    assert r["runs_out_on"]["p10"] == START and r["days_short"] == (END - START).days


@pytest.mark.parametrize("budget", [0, -50])
def test_a_budget_of_zero_or_less_is_exhausted_on_the_period_start(budget):
    r = _runway(budget, actual=300, daily={date(2026, 10, 5): 300}, within=0.0)
    assert r["status"] == "exhausted"
    assert r["runs_out_on"] == {"p10": START, "p50": START, "p90": START}
    assert r["days_short"] == (END - START).days
    r0 = _runway(budget, actual=0, daily={}, within=0.0)           # nothing spent, budget 0
    assert r0["status"] == "exhausted" and r0["runs_out_on"]["p50"] == START


# ------------------------------------------------------------------------------ none cases

def test_future_period_and_no_budget_have_no_runway():
    kwargs = dict(start=START, end=END, dates=DATES, cumulative_quantiles=ROWS, grid=GRID,
                  actual_to_date=0, within_budget_probability=0.5, daily_actuals={}, **DEFAULTS)
    assert fc.period_runway(current=False, budget=1000, **kwargs) is None
    assert fc.period_runway(current=True, budget=None, **kwargs) is None
    assert fc.period_runway(current=False, budget=None, **kwargs) is None
    got = fc.period_runway(current=True, budget=1000, **kwargs)
    assert got == fc.budget_runway(budget=1000, **kwargs)


# ------------------------------------------------------------------------------ monotonicity

def _none_last(d):
    return date.max if d is None else d


def test_raising_the_budget_never_raises_the_chance_of_running_out():
    previous = None
    for budget in range(1, 2400, 5):
        r = _runway(budget)
        if previous is not None:
            assert r["runs_out_probability"] <= previous + 1e-12
        previous = r["runs_out_probability"]


def test_run_out_dates_are_ordered_p10_p50_p90():
    for budget in range(1, 2400, 3):
        on = _runway(budget)["runs_out_on"]
        if on["p10"] is None:
            assert on == {"p10": None, "p50": None, "p90": None}
        else:
            assert on["p10"] <= on["p50"] <= on["p90"]


def test_p50_never_moves_earlier_when_the_budget_rises_for_a_scaled_distribution():
    """Provable for one distribution scaled by day (this fixture): the survival function of
    row k is S(R / (k + 1)) for a fixed S, and its ratio to the total's is monotone here. It is
    NOT guaranteed in general (see `budget_runway`'s docstring); the example run's tail showed
    1-day exceptions at P(out) 0.05-0.09."""
    previous = date.min
    for budget in range(1, 2400, 2):
        p50 = _none_last(_runway(budget)["runs_out_on"]["p50"])
        assert p50 >= previous
        previous = p50


def test_p50_can_move_earlier_when_the_budget_rises_documented_exception():
    """The date is the earliest d with P(out by d) >= q * P(out): conditional on running out.
    Raising the budget can drop mostly late outcomes, so the conditional median date comes
    EARLIER (a tail effect: on the example run, 44 of 28,000 budget steps, always one day,
    always with P(out) between 0.051 and 0.091). Pinned so it stays documented, not 'fixed'."""
    grid = [0, 25, 50, 75, 100]
    days = [date(2026, 10, 9), date(2026, 10, 10)]
    rows = [[2, 4, 8, 18, 24], [3, 14, 15, 18, 24]]       # day 0, and the period total

    def runway(budget):
        within = fc.quantile_cdf(grid, rows[-1], budget)
        return fc.budget_runway(
            start=START, end=days[-1], dates=days, cumulative_quantiles=rows, grid=grid,
            actual_to_date=0, budget=budget, within_budget_probability=within, daily_actuals={},
            lasts_below=0.2, runs_out_above=0.8, min_probability=0.0)

    # budget 14: total cdf = 25/100 -> P(out) .75; day 0: i = 2 (8), next 18: (50 + 25*6/10)/100 = .65
    #   -> P(out by day 0) .35 < .5 * .75 = .375 -> p50 is day 1 (Oct 10)
    # budget 15: total cdf = 50/100 -> P(out) .5; day 0: (50 + 25*7/10)/100 = .675 -> .325 >= .25
    #   -> p50 is day 0 (Oct 9)
    low, high = runway(14), runway(15)
    assert low["runs_out_on"]["p50"] == date(2026, 10, 10)
    assert high["runs_out_on"]["p50"] == date(2026, 10, 9)
    assert high["runs_out_probability"] < low["runs_out_probability"]        # the probability still falls


# ------------------------------------------------------------------------------ rounding digits

@pytest.mark.parametrize("within, status", [
    (0.8004, "lasts"),        # P(out) .1996: below .2 (rounding to 3 digits would read .2 -> at_risk)
    (0.7996, "at_risk"),      # .2004
    (0.1996, "runs_out"),     # .8004: above .8 (3 digits would read .8 -> at_risk)
    (0.2004, "at_risk"),      # .7996
])
def test_status_cut_offs_are_compared_to_twelve_digits(within, status):
    assert _runway(1000, within=within)["status"] == status


def test_the_minimum_probability_is_compared_to_twelve_digits():
    below = _runway(1000, within=0.9504)       # P(out) .0496 < .05 (3 digits would read .05 -> dates)
    assert below["runs_out_on"] == {"p10": None, "p50": None, "p90": None}
    above = _runway(1000, within=0.9496)       # .0504
    assert above["runs_out_on"]["p10"] is not None


def test_the_date_search_is_compared_to_twelve_digits():
    # rows of the hand-built set: P(out by day 3) = .25 exactly. With P(out) .5004 the p50 target is
    # .2502: day 3 falls short (3 digits would read .25 and stop there), so p50 is day 4 (Oct 13).
    r = _runway(1000, within=0.4996)
    assert r["runs_out_probability"] == pytest.approx(0.5004)
    assert r["runs_out_on"]["p50"] == date(2026, 10, 13)
    # with P(out) exactly .5 the target is .25 and day 3 (P .25) is reached
    assert _runway(1000)["runs_out_on"]["p50"] == date(2026, 10, 12)
