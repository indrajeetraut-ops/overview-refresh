"""A4 GET /v1/teams/{id} -- service-level (`server/api/manager/service.py::team_detail`).

Cost-based seats (docs/plan/2026-09-26-cost-based-seat-utilization.md): a seat's allowance is the
team's monthly budget / members, scaled to the window and prorated from the seat's start;
"room to save" = recoverable share >= 10% AND recoverable >= $10 per 30 days; low use = usage
below 40% of the seat allowance, outside the 14-day new-seat grace period.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from server.api.config import ApiConfig
from server.api.manager import budget_cycles
from server.api.manager import budgets as budgets_service
from server.api.manager.service import team_detail
from server.directory import DirectoryRecord, ExternalKey, TeamMembership, TeamRecord, sync
from server.storage import session_scalars, session_scope
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

from .conftest import requires_db

FRM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime(2026, 1, 31, tzinfo=timezone.utc)      # a 30-day window
MID = datetime(2026, 1, 15, tzinfo=timezone.utc)
LONG_AGO = datetime(2025, 11, 1, tzinfo=timezone.utc)  # a seat that predates the window


def _add_session(s, sid, user_id, cost, tokens, info=None):
    info = info or {}
    s.add(SessionRow(session_id=sid, producer_id="p", org_id="default", user_id=user_id,
                     agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                     ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id=sid, created_at=MID, updated_at=MID, models={}, tools={},
                      files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                      long_turn_count=0, info=info, otel={}, datasource="both",
                      cost_effective_usd_micros=cost, tokens_total=tokens,
                      **session_scalars.fold_scalars(info, {})))


def _member(key, name):
    return DirectoryRecord(external_keys=(ExternalKey("csv", key),), email=f"{key}@x.com",
                           display_name=name, teams=(TeamMembership("platform"),))


def _seat_since(s, user_id, when):
    """`users.created_at` is the sync's own wall-clock time; pin it so each seat's start (and
    so its proration / grace) is what the test says it is."""
    s.execute(text("UPDATE users SET created_at = :t WHERE user_id = :u"), {"t": when, "u": user_id})


def _seed(s, catalog, *, budget_usd: str | None = "450.00", extra_members=()):
    """Three regular members by default -> a $150/month seat (450 / 3) -> $150 over this 30-day
    window for a seat that predates it."""
    members = [_member("big-spender", "Big Spender"), _member("light-user", "Light User"),
               _member("idle", "Idle Member"), *extra_members]
    sync(s, catalog, members, teams=[TeamRecord("platform", "default", "Platform")])
    for key in ("big-spender", "light-user", "idle"):
        _seat_since(s, f"csv:{key}", LONG_AGO)
    # big-spender: $90 spend (60% of a $150 seat); $12 recoverable resume = 13% share -> room to save
    _add_session(s, "s1", "csv:big-spender", 90_000_000, 300_000, {
        "resume_cost": {"total_recoverable_usd_micros": 12_000_000, "events": []},
    })
    # light-user: $1 spend -> <1% of the seat, nothing recoverable -> on track, low use
    _add_session(s, "s2", "csv:light-user", 1_000_000, 10, {})
    # idle: rostered, no sessions at all
    s.flush()
    if budget_usd is not None:
        budgets_service.put_budget(s, catalog, "team", "platform", "monthly", Decimal(budget_usd))


def _people(result):
    return {p["user_id"]: p for p in result["data"]["people"]}


@requires_db
def test_room_to_save_uses_recoverable_share_and_scaled_floor(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = team_detail(s, catalog, ApiConfig(), "platform", FRM, TO)

    people = _people(result)
    assert people["csv:big-spender"]["status"] == "room-to-save"
    assert people["csv:big-spender"]["recoverable_pct"] == 12 / 90
    assert people["csv:light-user"]["status"] == "under"   # nothing recoverable, but <1% of the seat
    assert result["data"]["highlights"]["room_to_save_count"] == 1


@requires_db
def test_seat_usage_is_spend_over_the_window_scaled_seat_allowance(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = team_detail(s, catalog, ApiConfig(), "platform", FRM, TO)

    big = _people(result)["csv:big-spender"]
    assert big["seat_allowance"]["usd_micros"] == 150_000_000
    assert big["seat_allowance"]["cost_basis"] == "budget"
    assert big["usage_pct"] == 0.6
    assert big["low_use"] is False


@requires_db
def test_a_seat_over_its_allowance_is_never_on_track(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog, extra_members=[_member("heavy", "Heavy User")])
        _seat_since(s, "csv:heavy", LONG_AGO)
        # 4 members -> a $112.50 seat; $132 spend, nothing recoverable -> 117%
        _add_session(s, "s3", "csv:heavy", 132_000_000, 500_000, {})
    with session_scope() as s:
        result = team_detail(s, catalog, ApiConfig(), "platform", FRM, TO)

    people = _people(result)
    heavy = people["csv:heavy"]
    assert round(heavy["usage_pct"], 4) == round(132 / 112.5, 4)
    assert heavy["status"] == "over"
    assert heavy["usage_status"] == "over"
    assert heavy["low_use"] is False
    # room to save outranks the seat band in `status`; `usage_status` still carries it
    big = people["csv:big-spender"]
    assert big["status"] == "room-to-save"
    assert big["usage_status"] == "healthy"


@requires_db
def test_idle_member_is_listed_and_counts_as_low_use(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = team_detail(s, catalog, ApiConfig(), "platform", FRM, TO)

    people = _people(result)
    assert people["csv:idle"]["usage_pct"] == 0.0
    assert people["csv:idle"]["low_use"] is True
    assert people["csv:light-user"]["low_use"] is True
    assert result["data"]["highlights"]["low_use_count"] == 2


@requires_db
def test_new_member_in_grace_is_not_low_use_and_is_prorated(engine, catalog):
    # a ROLLING window (2 Jan - 1 Feb, so not a period-to-date) and a seat 5 days old at its end
    frm, to = FRM + timedelta(days=1), TO + timedelta(days=1)
    with session_scope() as s:
        _seed(s, catalog, extra_members=[_member("newbie", "New Person")])
        _seat_since(s, "csv:newbie", to - timedelta(days=5))
    with session_scope() as s:
        result = team_detail(s, catalog, ApiConfig(), "platform", frm, to)

    newbie = _people(result)["csv:newbie"]
    assert newbie["in_grace"] is True
    assert newbie["low_use"] is False
    # 4 members -> $112.50/month seat; 5 days of the 31-day month
    assert newbie["seat_allowance"]["usd_micros"] == round(112_500_000 * 5 / 31)


@requires_db
def test_no_team_budget_means_no_seat_usage(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog, budget_usd=None)
    with session_scope() as s:
        result = team_detail(s, catalog, ApiConfig(), "platform", FRM, TO)

    for person in result["data"]["people"]:
        assert person["usage_pct"] is None
        assert person["low_use"] is False
        assert person["seat_allowance"]["usd_micros"] is None
    assert result["data"]["highlights"]["low_use_count"] == 0
    assert result["data"]["team"]["budget_pct"] is None


@requires_db
def test_team_budget_pct_is_window_scaled(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = team_detail(s, catalog, ApiConfig(), "platform", FRM + timedelta(days=1),
                             FRM + timedelta(days=16))

    # $91 spend over a rolling 15-day window (2-17 Jan) of a $450/month budget: 450 x 15/31, not
    # $91 / $450 (a window starting on the 1st would be a period-to-date and get all $450)
    assert result["data"]["team"]["budget_pct"] == 91_000_000 / round(450_000_000 * 15 / 31)


@requires_db
def test_worth_a_look_names_the_room_to_save_person(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = team_detail(s, catalog, ApiConfig(), "platform", FRM, TO)

    findings = result["data"]["worth_a_look"]
    assert any(f["entity"] == {"kind": "person", "id": "csv:big-spender"} for f in findings)


@requires_db
def test_team_budget_pct_for_a_period_to_date_window_uses_the_full_budget(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        to_date = team_detail(s, catalog, ApiConfig(), "platform", FRM, FRM + timedelta(days=16))
        whole = team_detail(s, catalog, ApiConfig(), "platform", FRM,
                            datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert to_date["data"]["team"]["budget_pct"] == 91 / 450
    assert whole["data"]["team"]["budget_pct"] == 91 / 450
    assert to_date["data"]["team"]["over_budget"] is False


@requires_db
def test_seat_allowance_in_a_to_date_window_is_prorated_to_the_period_end(engine, catalog):
    # 4 members -> $112.50/month seat. The newbie joined on the 11th: 21 of January's 31 days.
    with session_scope() as s:
        _seed(s, catalog, extra_members=[_member("newbie", "New Person")])
        _seat_since(s, "csv:newbie", datetime(2026, 1, 11, tzinfo=timezone.utc))
    with session_scope() as s:
        result = team_detail(s, catalog, ApiConfig(), "platform", FRM, FRM + timedelta(days=19))
    people = _people(result)
    assert people["csv:newbie"]["seat_allowance"]["usd_micros"] == round(112_500_000 * 21 / 31)
    assert people["csv:big-spender"]["seat_allowance"]["usd_micros"] == 112_500_000   # the full seat


@requires_db
def test_sprint_cycle_team_detail_seat_and_team_budgets(engine, catalog):
    sprint = (datetime(2026, 1, 5, tzinfo=timezone.utc), datetime(2026, 1, 16, tzinfo=timezone.utc))
    with session_scope() as s:
        _seed(s, catalog, budget_usd=None, extra_members=[_member("newbie", "New Person")])
        _seat_since(s, "csv:newbie", datetime(2026, 1, 12, tzinfo=timezone.utc))
        budget_cycles.put_cycle(s, catalog, "default", budget_cycles.validate(
            "sprint", sprint_weeks=2, anchor_date="2026-01-05", anchor_number=10))   # 5-19 Jan
        budgets_service.put_budget(s, catalog, "team", "platform", "sprint", Decimal("400"))
        budgets_service.put_budget(s, catalog, "team", "platform", "monthly", Decimal("9999"))
    with session_scope() as s:
        result = team_detail(s, catalog, ApiConfig(), "platform", *sprint)
    assert result["data"]["team"]["budget_pct"] == 91 / 400      # the sprint amount, in full
    people = _people(result)
    assert people["csv:big-spender"]["seat_allowance"]["usd_micros"] == 100_000_000   # 400 / 4
    assert people["csv:newbie"]["seat_allowance"]["usd_micros"] == 50_000_000         # 7 of 14 days
