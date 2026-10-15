from datetime import datetime, timedelta, timezone

import pytest

from server.api.single_user import service
from server.api.errors import BadRequestError
from server.storage import session_scope
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow
from server.storage.schema import User

from .conftest import requires_db

pytestmark = requires_db

USER = "u1"
NOW = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)   # "today", a Friday


def _day(offset: int, hour: int = 10) -> datetime:
    """`offset` days before `NOW`'s date, e.g. `_day(0)` is today, `_day(1)` is yesterday."""
    return (NOW - timedelta(days=offset)).replace(hour=hour, minute=0, second=0, microsecond=0)


def _seed(s, session_id, datasource, *, started_at, tokens_total=0, otel=None,
         parent_session_id=None, status="live"):
    s.add(SessionRow(session_id=session_id, producer_id="p", org_id="org-acme", user_id=USER,
                     agent="claude_code", status=status, started_at=started_at,
                     last_event_at=started_at, signal_blob={}, info_blob={}, subagents=[],
                     parent_session_id=parent_session_id))
    s.add(SessionInfoRow(
        session_id=session_id, created_at=NOW, updated_at=NOW,
        models={}, tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
        turn_count=0, long_turn_count=0, tokens_total=tokens_total, info={}, otel=otel or {},
        cost_effective_usd_micros=None, datasource=datasource,
    ))


def _otel_only_otel(tokens_input: int) -> dict:
    return {
        "measures": {"tokens_input": str(tokens_input), "active_time_s": "60.0"},
        "tokens": {"tokens_input": str(tokens_input), "tokens_output": "0",
                  "tokens_cache_read": "0", "tokens_cache_creation": "0"},
    }


def test_rhythm_empty_user_does_not_raise(engine, catalog):
    with session_scope() as s:
        result = service.rhythm(s, catalog, USER, weeks=1, now=NOW)

    assert result["modes_present"] == []
    assert result["sessions_by_mode"] == {}
    assert result["active_days"] == 0
    assert result["days_in_window"] == 7
    assert result["longest_streak"] is None
    assert len(result["days"]) == 7
    assert all(d["sessions"] == 0 and d["tokens"] == 0 for d in result["days"])
    assert result["days"][-1]["day"] == NOW.date().isoformat()   # today is the last day


def test_rhythm_single_active_day(engine, catalog):
    with session_scope() as s:
        _seed(s, "s1", "jsonl_only", started_at=_day(0), tokens_total=100)

    with session_scope() as s:
        result = service.rhythm(s, catalog, USER, weeks=1, now=NOW)

    assert result["active_days"] == 1
    assert result["longest_streak"] == {"days": 1, "from": NOW.date().isoformat(),
                                        "to": NOW.date().isoformat()}
    today = next(d for d in result["days"] if d["day"] == NOW.date().isoformat())
    assert today == {"day": NOW.date().isoformat(), "tokens": 100, "sessions": 1}


def test_rhythm_streak_broken_by_one_gap_day(engine, catalog):
    with session_scope() as s:
        # active: day-4, day-3, day-2  [gap: day-1]  active: day-0 (today)
        _seed(s, "s4", "jsonl_only", started_at=_day(4), tokens_total=10)
        _seed(s, "s3", "jsonl_only", started_at=_day(3), tokens_total=10)
        _seed(s, "s2", "jsonl_only", started_at=_day(2), tokens_total=10)
        _seed(s, "s0", "jsonl_only", started_at=_day(0), tokens_total=10)

    with session_scope() as s:
        result = service.rhythm(s, catalog, USER, weeks=1, now=NOW)

    assert result["active_days"] == 4
    # the 3-day run (day-4..day-2) beats the 1-day run (day-0)
    assert result["longest_streak"] == {"days": 3, "from": _day(4).date().isoformat(),
                                        "to": _day(2).date().isoformat()}


def test_rhythm_streak_running_to_window_edge_is_not_truncated(engine, catalog):
    with session_scope() as s:
        _seed(s, "s2", "jsonl_only", started_at=_day(2), tokens_total=10)
        _seed(s, "s1", "jsonl_only", started_at=_day(1), tokens_total=10)
        _seed(s, "s0", "jsonl_only", started_at=_day(0), tokens_total=10)  # today, the edge

    with session_scope() as s:
        result = service.rhythm(s, catalog, USER, weeks=1, now=NOW)

    assert result["longest_streak"] == {"days": 3, "from": _day(2).date().isoformat(),
                                        "to": _day(0).date().isoformat()}


def test_rhythm_tokens_resolve_in_both_modes(engine, catalog):
    """FEAS §7b / R1's own done-when: an otel_only session and a jsonl_only session on
    different days must both contribute non-zero tokens -- the two-rung expression must not
    silently collapse to `tokens_total` alone (which is 0 on every otel_only row)."""
    with session_scope() as s:
        _seed(s, "otel-1", "otel_only", started_at=_day(1), tokens_total=0,
             otel=_otel_only_otel(300))
        _seed(s, "jsonl-1", "jsonl_only", started_at=_day(0), tokens_total=150)

    with session_scope() as s:
        result = service.rhythm(s, catalog, USER, weeks=1, now=NOW)

    by_day = {d["day"]: d["tokens"] for d in result["days"]}
    assert by_day[_day(1).date().isoformat()] == 300
    assert by_day[_day(0).date().isoformat()] == 150
    assert result["modes_present"] == ["jsonl_only", "otel_only"]
    assert result["sessions_by_mode"] == {"jsonl_only": 1, "otel_only": 1}


def test_rhythm_subagent_fanout_does_not_inflate_daily_session_count(engine, catalog):
    with session_scope() as s:
        _seed(s, "parent-1", "jsonl_only", started_at=_day(0), tokens_total=100)
        _seed(s, "child-1", "jsonl_only", started_at=_day(0), tokens_total=20,
             parent_session_id="parent-1")

    with session_scope() as s:
        result = service.rhythm(s, catalog, USER, weeks=1, now=NOW)

    today = next(d for d in result["days"] if d["day"] == NOW.date().isoformat())
    assert today["sessions"] == 1          # roots only
    assert today["tokens"] == 120          # every row's tokens still counted


@pytest.mark.parametrize("weeks", [0, 105])
def test_rhythm_rejects_out_of_bound_weeks(engine, catalog, weeks):
    with session_scope() as s:
        with pytest.raises(BadRequestError):
            service.rhythm(s, catalog, USER, weeks=weeks, now=NOW)


# ------------------------------------------------------------------ TZ track: local day boundaries

def test_rhythm_buckets_by_the_users_local_day_not_utc(engine, catalog):
    """02:30 UTC on Aug 7 is 19:30 on Aug 6 in America/Los_Angeles (UTC-7 in August) -- the
    exact case UTC-only bucketing gets wrong for a user west of Greenwich: a late-evening
    session reads as the *next* UTC day. `NOW` here (10:00 UTC) is Aug 7 in both zones, so only
    the session's own day assignment is under test, not "today"'s."""
    with session_scope() as s:
        s.add(User(user_id=USER, created_at=NOW, timezone="America/Los_Angeles"))
        _seed(s, "s1", "jsonl_only", started_at=datetime(2026, 8, 7, 2, 30, tzinfo=timezone.utc),
             tokens_total=50)

    with session_scope() as s:
        result = service.rhythm(s, catalog, USER, weeks=1, now=NOW)

    by_day = {d["day"]: d["sessions"] for d in result["days"]}
    assert by_day[datetime(2026, 8, 6).date().isoformat()] == 1   # local day, not the UTC day
    assert by_day[datetime(2026, 8, 7).date().isoformat()] == 0
    assert result["active_days"] == 1
