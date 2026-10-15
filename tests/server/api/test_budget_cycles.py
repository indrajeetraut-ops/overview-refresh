"""A1: budget cycles (`server/api/manager/budget_cycles.py`) -- period generation for every kind
(the reference prototype's arithmetic, ported with its tests), validation, containing-period
lookup and the stored cycle. Pure arithmetic runs without a DB; storage tests need one."""
from datetime import date, datetime, timedelta, timezone

import pytest
from structlog.testing import capture_logs
from sqlalchemy import text

from server.api.errors import BadRequestError
from server.api.manager import budget_cycles as bc
from server.api.manager.budget_cycles import Cycle
from server.storage import session_scope

from .conftest import requires_db

TODAY = date(2026, 10, 12)      # the reference test's "today" (a Monday)


def _sprint(weeks=2, anchor=date(2026, 9, 14), number=231, recurrence="recurring") -> Cycle:
    return bc.validate("sprint", recurrence, sprint_weeks=weeks, anchor_date=anchor,
                       anchor_number=number)


def _labels(result) -> list[str]:
    return [p["label"] for p in result["periods"]]


# -------------------------------------------------- ported reference tests (budget-cycles.test.ts)

def test_sprints_are_numbered_from_the_anchor_and_expose_the_next_one():
    # anchor 231 started 14 Sep; 12 Oct is exactly 28 days later = the first day of sprint 233.
    result = bc.periods(_sprint(), TODAY, history=3)
    assert _labels(result) == ["Sprint 231", "Sprint 232", "Sprint 233"]
    current = result["periods"][-1]
    assert current["is_current"] is True
    assert current["days_elapsed"] == 1
    assert current["days_total"] == 14
    assert current["start"] == date(2026, 10, 12)
    assert current["end_exclusive"] == date(2026, 10, 26)
    nxt = result["next"]
    assert nxt["label"] == "Sprint 234"
    assert nxt["days_total"] == 14
    assert nxt["days_elapsed"] == 0 and nxt["is_current"] is False
    assert nxt["start"] == date(2026, 10, 26)


def test_calendar_quarters_and_a_one_time_custom_cycle():
    quarters = bc.periods(bc.validate("quarter"), TODAY)
    assert quarters["periods"][-1]["label"] == "Q4 2026"
    assert quarters["next"]["label"] == "Q1 2027"

    custom = bc.validate("custom", "one-time", anchor_date="2026-10-01", custom_end="2027-03-31")
    result = bc.periods(custom, TODAY)
    assert len(result["periods"]) == 1
    assert result["periods"][0]["days_elapsed"] == 12          # 1..12 Oct inclusive
    assert result["periods"][0]["days_total"] == 182
    assert result["periods"][0]["label"] == "01 Oct 2026 – 31 Mar 2027"
    assert result["next"] is None


# ----------------------------------------------------------------------------- sprint edges

def test_sprint_history_extends_backwards_from_the_anchor():
    # unlike the reference, history is not cut at the anchor: sprints 228-230 are generated too
    result = bc.periods(_sprint(), TODAY, history=6)
    assert _labels(result) == [f"Sprint {n}" for n in range(228, 234)]
    assert result["periods"][0]["start"] == date(2026, 8, 3)
    assert all(not p["is_current"] for p in result["periods"][:-1])
    assert result["periods"][0]["days_elapsed"] == 14           # finished: all its days elapsed


def test_sprint_numbers_stop_at_one():
    result = bc.periods(_sprint(number=2), TODAY, history=6)
    assert _labels(result) == ["Sprint 1", "Sprint 2", "Sprint 3", "Sprint 4"]
    assert result["periods"][0]["start"] == date(2026, 8, 31)


def test_sprint_before_its_anchor_has_no_past_periods_and_next_is_the_anchor_sprint():
    result = bc.periods(_sprint(anchor=date(2026, 11, 2), number=7), TODAY)
    assert result["periods"] == []
    assert result["next"]["label"] == "Sprint 7"
    assert result["next"]["start"] == date(2026, 11, 2)
    assert result["next"]["days_elapsed"] == 0


def test_sprint_rolls_over_on_its_boundary_day():
    last_day = bc.periods(_sprint(), date(2026, 10, 25), history=1)["periods"][0]
    assert (last_day["label"], last_day["days_elapsed"]) == ("Sprint 233", 14)
    first_day = bc.periods(_sprint(), date(2026, 10, 26), history=1)["periods"][0]
    assert (first_day["label"], first_day["days_elapsed"]) == ("Sprint 234", 1)


def test_one_week_and_three_week_sprints():
    week = bc.periods(_sprint(weeks=1, anchor=date(2026, 10, 5), number=40), TODAY, history=1)
    assert week["periods"][0]["label"] == "Sprint 41"
    assert week["periods"][0]["days_total"] == 7
    three = bc.periods(_sprint(weeks=3, anchor=date(2026, 9, 14), number=10), TODAY, history=1)
    assert three["periods"][0]["label"] == "Sprint 11"           # 28 days in = offset 1 of 21d
    assert three["periods"][0]["days_total"] == 21


def test_one_time_sprint_is_only_the_anchor_sprint():
    past = bc.periods(_sprint(recurrence="one-time"), date(2026, 12, 1))
    assert _labels(past) == ["Sprint 231"]
    assert past["periods"][0]["is_current"] is False
    assert past["periods"][0]["days_elapsed"] == 14
    assert past["next"] is None
    live = bc.periods(_sprint(recurrence="one-time"), date(2026, 9, 20))
    assert live["periods"][0]["is_current"] is True and live["periods"][0]["days_elapsed"] == 7
    future = bc.periods(_sprint(recurrence="one-time"), date(2026, 9, 1))
    assert future["periods"] == [] and future["next"]["label"] == "Sprint 231"


# --------------------------------------------------------------- calendar kinds and rollover

def test_quarter_rolls_over_the_year():
    result = bc.periods(bc.validate("quarter"), date(2026, 1, 5), history=2)
    assert _labels(result) == ["Q4 2025", "Q1 2026"]
    assert result["periods"][0]["start"] == date(2025, 10, 1)
    assert result["periods"][0]["end_exclusive"] == date(2026, 1, 1)
    assert result["next"]["label"] == "Q2 2026"
    assert result["next"]["start"] == date(2026, 4, 1)


def test_month_rolls_over_december_and_keys_are_stable():
    result = bc.periods(bc.validate("month"), date(2026, 12, 31), history=2)
    assert _labels(result) == ["Nov 2026", "Dec 2026"]
    assert [p["key"] for p in result["periods"]] == ["month-2026-11", "month-2026-12"]
    assert result["periods"][-1]["days_elapsed"] == 31
    assert result["next"]["label"] == "Jan 2027"
    assert result["next"]["key"] == "month-2027-01"


def test_half_and_year_periods():
    half = bc.periods(bc.validate("half"), date(2026, 7, 1), history=2)
    assert _labels(half) == ["H1 2026", "H2 2026"]
    assert half["periods"][-1]["days_total"] == 184
    assert half["periods"][-1]["days_elapsed"] == 1
    assert half["next"]["label"] == "H1 2027"
    year = bc.periods(bc.validate("year"), TODAY, history=2)
    assert _labels(year) == ["2025", "2026"]
    assert year["periods"][-1]["key"] == "year-2026"
    assert year["periods"][-1]["days_elapsed"] == 285            # 12 Oct is day 285 of 2026
    assert year["next"]["label"] == "2027"


def test_calendar_history_defaults_to_six_and_is_oldest_first():
    result = bc.periods(bc.validate("month"), TODAY)
    assert len(result["periods"]) == 6
    assert result["periods"][0]["label"] == "May 2026"
    assert result["periods"][-1]["is_current"] is True


def test_leap_years():
    feb_2028 = bc.period_containing(bc.validate("month"), date(2028, 2, 10))
    assert feb_2028["days_total"] == 29 and feb_2028["end_exclusive"] == date(2028, 3, 1)
    assert bc.period_containing(bc.validate("month"), date(2026, 2, 10))["days_total"] == 28
    assert bc.period_containing(bc.validate("year"), date(2028, 6, 1))["days_total"] == 366
    assert bc.period_containing(bc.validate("year"), date(2026, 6, 1))["days_total"] == 365
    assert bc.period_containing(bc.validate("quarter"), date(2028, 2, 29))["days_total"] == 91


# ---------------------------------------------------------------------------------- custom

def test_recurring_custom_repeats_its_length_from_the_start():
    cycle = bc.validate("custom", "recurring", anchor_date="2026-10-01", custom_end="2026-10-14")
    result = bc.periods(cycle, date(2026, 10, 20), history=6)
    assert [p["start"] for p in result["periods"]] == [date(2026, 10, 1), date(2026, 10, 15)]
    current = result["periods"][-1]
    assert current["is_current"] and current["days_elapsed"] == 6 and current["days_total"] == 14
    assert current["label"] == "15 Oct 2026 – 28 Oct 2026"
    assert current["key"] == "custom-2026-10-15"
    assert result["next"]["start"] == date(2026, 10, 29)


def test_recurring_custom_before_its_start_has_only_a_next_period():
    cycle = bc.validate("custom", "recurring", anchor_date="2026-11-01", custom_end="2026-11-30")
    result = bc.periods(cycle, TODAY)
    assert result["periods"] == []
    assert result["next"]["start"] == date(2026, 11, 1)
    assert result["next"]["days_total"] == 30


def test_one_time_custom_before_during_and_after():
    cycle = bc.validate("custom", "one-time", anchor_date="2026-10-20", custom_end="2026-10-31")
    before = bc.periods(cycle, TODAY)
    assert before["periods"] == [] and before["next"]["days_total"] == 12
    assert before["next"]["days_elapsed"] == 0
    during = bc.periods(cycle, date(2026, 10, 25))
    assert during["periods"][0]["is_current"] and during["periods"][0]["days_elapsed"] == 6
    after = bc.periods(cycle, date(2026, 12, 1))
    assert after["periods"][0]["is_current"] is False and after["periods"][0]["days_elapsed"] == 12
    assert after["next"] is None


def test_single_day_custom_period():
    cycle = bc.validate("custom", "one-time", anchor_date="2026-10-12", custom_end="2026-10-12")
    only = bc.periods(cycle, TODAY)["periods"][0]
    assert only["days_total"] == 1 and only["days_elapsed"] == 1 and only["is_current"]


# ----------------------------------------------------------------------- period_containing

def test_period_containing_for_calendar_and_sprint():
    month = bc.period_containing(bc.validate("month"), date(2026, 12, 31))
    assert (month["start"], month["end_exclusive"]) == (date(2026, 12, 1), date(2027, 1, 1))
    sprint = bc.period_containing(_sprint(), date(2026, 10, 25))
    assert (sprint["label"], sprint["start"]) == ("Sprint 233", date(2026, 10, 12))
    assert bc.period_containing(_sprint(), date(2026, 10, 26))["label"] == "Sprint 234"


def test_period_containing_extends_before_the_anchor_and_start():
    before = bc.period_containing(_sprint(), date(2026, 9, 13))
    assert before["label"] == "Sprint 230" and before["days_total"] == 14
    assert before["end_exclusive"] == date(2026, 9, 14)
    early = bc.period_containing(_sprint(number=1), date(2026, 8, 1))      # number below 1: only
    assert early["days_total"] == 14                                        # the length matters
    custom = bc.validate("custom", "recurring", anchor_date="2026-10-01", custom_end="2026-10-14")
    assert bc.period_containing(custom, date(2026, 9, 30))["start"] == date(2026, 9, 17)


def test_period_containing_a_date_outside_a_one_time_cycle_returns_its_single_period():
    cycle = bc.validate("custom", "one-time", anchor_date="2026-10-01", custom_end="2026-10-10")
    found = bc.period_containing(cycle, date(2027, 1, 1))
    assert (found["start"], found["end_exclusive"]) == (date(2026, 10, 1), date(2026, 10, 11))
    assert found["days_total"] == 10


# ------------------------------------------------------------------------------- validation

@pytest.mark.parametrize("kwargs, fragment", [
    (dict(kind="weekly"), "unknown cycle kind"),
    (dict(kind="month", recurrence="sometimes"), "unknown recurrence"),
    (dict(kind="month", recurrence="one-time"), "always recurring"),
    (dict(kind="year", recurrence="one-time"), "always recurring"),
    (dict(kind="sprint"), "anchor_date"),
    (dict(kind="sprint", anchor_date="2026-09-14", sprint_weeks=0), "sprint_weeks"),
    (dict(kind="sprint", anchor_date="2026-09-14", sprint_weeks=53), "sprint_weeks"),
    (dict(kind="sprint", anchor_date="2026-09-14", sprint_weeks=1.5), "sprint_weeks"),
    (dict(kind="sprint", anchor_date="2026-09-14", sprint_weeks=True), "sprint_weeks"),
    (dict(kind="sprint", anchor_date="2026-09-14", anchor_number=0), "anchor_number"),
    (dict(kind="sprint", anchor_date="14/09/2026"), "calendar date"),
    (dict(kind="sprint", anchor_date="2026-09-14T00:00:00"), "calendar date"),
    (dict(kind="custom", anchor_date="2026-10-01"), "custom_end"),
    (dict(kind="custom", custom_end="2026-10-01"), "anchor_date"),
    (dict(kind="custom", anchor_date="2026-10-02", custom_end="2026-10-01"), "before anchor_date"),
])
def test_validate_rejects(kwargs, fragment):
    with pytest.raises(BadRequestError) as exc:
        bc.validate(**kwargs)
    assert fragment in exc.value.reason


def test_validate_normalises_and_drops_fields_the_kind_does_not_use():
    month = bc.validate("month", sprint_weeks=3, anchor_date="2026-01-01", custom_end="2026-02-01")
    assert month == Cycle(kind="month", recurrence="recurring")
    sprint = bc.validate("sprint", anchor_date=date(2026, 9, 14), custom_end="2026-12-31")
    assert (sprint.sprint_weeks, sprint.anchor_number, sprint.custom_end) == (2, 1, None)
    custom = bc.validate("custom", anchor_date="2026-10-01", custom_end="2026-10-01",
                         sprint_weeks=4, anchor_number=9)
    assert (custom.sprint_weeks, custom.anchor_number) == (None, None)
    assert custom.custom_end == date(2026, 10, 1)            # a one-day custom period is valid


def test_label_map_is_one_to_one():
    assert [bc.label_for(k) for k in bc.KINDS] == [
        "monthly", "quarterly", "half-yearly", "yearly", "sprint", "custom"]
    assert len(set(bc.CYCLE_LABELS.values())) == len(bc.KINDS)
    assert bc.validate("quarter").label == "quarterly"
    with pytest.raises(BadRequestError):
        bc.label_for("weekly")


@pytest.mark.parametrize("history", [0, -1, 25, True, "6", 2.0])
def test_periods_rejects_a_bad_history(history):
    with pytest.raises(BadRequestError):
        bc.periods(bc.validate("month"), TODAY, history=history)


def test_periods_accepts_the_history_bounds():
    assert len(bc.periods(bc.validate("month"), TODAY, history=1)["periods"]) == 1
    assert len(bc.periods(bc.validate("month"), TODAY, history=24)["periods"]) == 24


# --------------------------------------------------------------------------------- storage

@requires_db
def test_no_saved_cycle_is_the_default_monthly_cycle(engine, catalog):
    with session_scope() as s:
        assert bc.load_cycle(s, catalog, "default") is None
        active = bc.active(s, catalog, "default", "monthly")
        view = bc.get_cycle(s, catalog, "default")
    assert active == bc.ActiveCycle("monthly", bc.DEFAULT_CYCLE)
    assert view == {"kind": "month", "recurrence": "recurring", "sprint_weeks": None,
                    "anchor_date": None, "anchor_number": None, "custom_end": None,
                    "period_label": "monthly", "updated_at": None}


@requires_db
def test_a_default_label_other_than_monthly_with_no_saved_cycle_is_rejected(engine, catalog):
    with session_scope() as s:
        with pytest.raises(BadRequestError):
            bc.active(s, catalog, "default", "quarterly")


@requires_db
def test_put_cycle_round_trips_and_a_second_put_replaces(engine, catalog):
    sprint = _sprint(weeks=3, number=12)
    with session_scope() as s:
        saved = bc.put_cycle(s, catalog, "default", sprint)
    assert saved["kind"] == "sprint" and saved["period_label"] == "sprint"
    assert saved["sprint_weeks"] == 3 and saved["anchor_number"] == 12
    assert saved["anchor_date"] == date(2026, 9, 14) and saved["updated_at"] is not None
    with session_scope() as s:
        assert bc.load_cycle(s, catalog, "default") == sprint
        active = bc.active(s, catalog, "default", "monthly")
    assert active.label == "sprint" and active.cycle == sprint   # the saved cycle beats the default

    quarter = bc.validate("quarter")
    with session_scope() as s:
        bc.put_cycle(s, catalog, "default", quarter)
    with session_scope() as s:
        assert bc.load_cycle(s, catalog, "default") == quarter
        view = bc.get_cycle(s, catalog, "default")
    assert view["sprint_weeks"] is None and view["anchor_date"] is None   # full replace
    assert view["period_label"] == "quarterly"


@requires_db
def test_cycles_are_per_org(engine, catalog):
    with session_scope() as s:
        bc.put_cycle(s, catalog, "acme", bc.validate("year"))
    with session_scope() as s:
        assert bc.load_cycle(s, catalog, "default") is None
        assert bc.load_cycle(s, catalog, "acme") == bc.validate("year")


# ------------------------------------------------- A1 review minors: datetimes, bounds, history

def test_a_datetime_today_is_reduced_to_its_utc_date():
    cycle = bc.validate("month")
    expected = bc.periods(cycle, date(2026, 10, 12))
    assert bc.periods(cycle, datetime(2026, 10, 12, 23, 59, 59, tzinfo=timezone.utc)) == expected
    assert bc.periods(cycle, datetime(2026, 10, 12, 9, 0)) == expected                    # naive = UTC
    # 22:00 on the 11th in UTC-5 is 03:00 UTC on the 12th
    est = timezone(timedelta(hours=-5))
    assert bc.periods(_sprint(), datetime(2026, 10, 11, 22, 0, tzinfo=est)) == bc.periods(_sprint(), TODAY)
    # 23:30 on 31 Oct UTC-5 is already 1 Nov in UTC: a new month
    rolled = bc.periods(cycle, datetime(2026, 10, 31, 23, 30, tzinfo=est), history=1)
    assert rolled["periods"][0]["label"] == "Nov 2026"
    assert bc.period_containing(cycle, datetime(2026, 10, 31, 23, 30, tzinfo=est))["label"] == "Nov 2026"


def test_anchor_number_and_dates_are_bounded():
    assert bc.validate("sprint", anchor_date="2026-09-14", anchor_number=100_000).anchor_number == 100_000
    with pytest.raises(BadRequestError):
        bc.validate("sprint", anchor_date="2026-09-14", anchor_number=100_001)
    assert bc.validate("sprint", anchor_date="2000-01-01").anchor_date == date(2000, 1, 1)
    assert bc.validate("custom", anchor_date="2100-12-30", custom_end="2100-12-31").custom_end == date(2100, 12, 31)
    for bad in ("1999-12-31", "2101-01-01", date(1, 1, 1), date(9999, 12, 31)):
        with pytest.raises(BadRequestError) as exc:
            bc.validate("sprint", anchor_date=bad)
        assert "between" in exc.value.reason
    with pytest.raises(BadRequestError):
        bc.validate("custom", anchor_date="2026-01-01", custom_end="2101-01-01")


def test_history_larger_than_the_available_past_returns_what_exists():
    custom = bc.validate("custom", "recurring", anchor_date="2026-10-01", custom_end="2026-10-14")
    result = bc.periods(custom, date(2026, 10, 20), history=24)
    assert len(result["periods"]) == 2 and result["periods"][0]["start"] == date(2026, 10, 1)
    young = bc.periods(_sprint(number=3), TODAY, history=24)           # numbers stop at 1
    assert _labels(young) == ["Sprint 1", "Sprint 2", "Sprint 3", "Sprint 4", "Sprint 5"]
    one_time_custom = bc.validate("custom", "one-time", anchor_date="2026-10-01", custom_end="2026-10-14")
    assert len(bc.periods(one_time_custom, date(2026, 10, 20), history=24)["periods"]) == 1


def test_periods_view_reports_exact_instants_and_the_current_to_is_now():
    active = bc.ActiveCycle("monthly", bc.DEFAULT_CYCLE)
    now = datetime(2026, 10, 12, 14, 30, tzinfo=timezone.utc)
    view = bc.periods_view(active, now, history=2)
    assert view["period_label"] == "monthly"
    sep, octo = view["periods"]
    assert sep["from"] == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert sep["to"] == sep["end_exclusive"] == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert octo["to"] == now and octo["end_exclusive"] == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert view["next"]["from"] == octo["end_exclusive"] and view["next"]["to"] == view["next"]["end_exclusive"]
    # a naive `now` is UTC
    assert bc.periods_view(active, datetime(2026, 10, 12, 14, 30), 1)["periods"][0]["to"] == now


# --------------------------------------------------------------- a corrupt saved row degrades

_CORRUPT = [
    ("sprint", "recurring", None),            # sprint without its anchor_date
    ("weekly", "recurring", None),            # a kind this version does not know
    ("month", "one-time", None),              # a calendar kind that is not recurring
]


@requires_db
@pytest.mark.parametrize("kind, recurrence, anchor", _CORRUPT)
def test_a_corrupt_saved_row_falls_back_to_the_monthly_default_and_logs(
        engine, catalog, kind, recurrence, anchor):
    with session_scope() as s:
        s.execute(text("INSERT INTO budget_cycle (org_id, kind, recurrence, anchor_date, updated_at) "
                       "VALUES ('default', :k, :r, :a, now())"), {"k": kind, "r": recurrence, "a": anchor})
    with session_scope() as s, capture_logs() as logs:
        assert bc.load_cycle(s, catalog, "default") is None
        assert bc.active(s, catalog, "default", "monthly") == bc.ActiveCycle("monthly", bc.DEFAULT_CYCLE)
        view = bc.get_cycle(s, catalog, "default")
    assert view["kind"] == "month" and view["period_label"] == "monthly" and view["updated_at"] is None
    warnings = [e for e in logs if e["event"] == "budget_cycle.stored_row_invalid"]
    assert warnings and warnings[0]["log_level"] == "warning" and warnings[0]["org_id"] == "default"
    # the next PUT replaces the bad row
    with session_scope() as s:
        saved = bc.put_cycle(s, catalog, "default", bc.validate("quarter"))
    assert saved["kind"] == "quarter" and saved["label_changed"] is True and saved["previous_period_label"] == "monthly"


@requires_db
def test_put_cycle_reports_what_a_save_did_to_budget_amounts(engine, catalog):
    with session_scope() as s:
        first = bc.put_cycle(s, catalog, "default", bc.validate("month"))     # the default, saved
        assert first["label_changed"] is False and first["note"] is None
        kind_change = bc.put_cycle(s, catalog, "default", _sprint())
        assert kind_change["label_changed"] and "'monthly'" in kind_change["note"] and "'sprint'" in kind_change["note"]
        edit = bc.put_cycle(s, catalog, "default", _sprint(weeks=3))
        assert edit["label_changed"] is False and "changed cycle" in edit["note"]
