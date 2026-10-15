"""A2 GET /v1/org/usage -- service-level (`server/api/manager/service.py::org_usage`).

Plan Verify: "over/under counts + low-seat flags match fixture." Cost-based since
docs/plan/2026-09-26-cost-based-seat-utilization.md: a team's utilization is its spend over its
window-scaled monthly budget; seats are counted over MEMBERS against budget / members.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from server.api.config import ApiConfig
from server.api.manager import budgets as budgets_service
from server.api.manager.service import org_usage
from server.directory import DirectoryRecord, ExternalKey, TeamMembership, TeamRecord, sync
from server.storage import session_scalars, session_scope
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

from .conftest import requires_db

FRM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime(2026, 1, 31, tzinfo=timezone.utc)
MID = datetime(2026, 1, 15, tzinfo=timezone.utc)
LONG_AGO = datetime(2025, 11, 1, tzinfo=timezone.utc)


def _add_session(s, session_id, user_id, tokens, info=None, cost=0):
    info = info or {}
    s.add(SessionRow(session_id=session_id, producer_id="p", org_id="default", user_id=user_id,
                     agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                     ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id=session_id, created_at=MID, updated_at=MID, models={},
                      tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
                      turn_count=0, long_turn_count=0, info=info, otel={},
                      datasource="both", tokens_total=tokens, cost_effective_usd_micros=cost,
                      **session_scalars.fold_scalars(info, {})))


def _seats_predate_window(s):
    """Pin every seat's start before the window (sync stamps users.created_at with wall-clock
    time), so allowances are the full window's and nobody is in the new-seat grace period."""
    s.execute(text("UPDATE users SET created_at = :t"), {"t": LONG_AGO})


def _seed(s, catalog, extra_records=(), extra_teams=()):
    sync(
        s, catalog,
        [
            DirectoryRecord(external_keys=(ExternalKey("csv", "heavy"),), email="heavy@x.com",
                            teams=(TeamMembership("platform"),)),
            DirectoryRecord(external_keys=(ExternalKey("csv", "light"),), email="light@x.com",
                            teams=(TeamMembership("platform"),)),
            *extra_records,
        ],
        teams=[TeamRecord("platform", "default", "Platform"), *extra_teams],
    )
    # The fold writes per-segment labels only (info.segments); the session type is the
    # most-turns segment's label, so s1 reads as IMPLEMENT despite a shorter PLAN segment.
    _add_session(s, "s1", "csv:heavy", 950_000,
                 {"segments": [{"label": "PLAN", "turns": 2}, {"label": "IMPLEMENT", "turns": 9}]},
                 cost=96_000_000)
    _add_session(s, "s2", "csv:light", 10_000,
                 {"segments": [{"label": "BUG_FIX", "turns": 4}]}, cost=1_000_000)
    s.flush()
    _seats_predate_window(s)
    # $100/month over a 30-day window: team allowance $100, $50 per seat (2 members)
    budgets_service.put_budget(s, catalog, "team", "platform", "monthly", Decimal("100.00"))


@requires_db
def test_low_seat_flag_and_utilization_status(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = org_usage(s, catalog, ApiConfig(), FRM, TO)

    team = result["data"]["teams"][0]
    assert team["team_id"] == "platform"
    assert team["spend"]["usd_micros"] == 97_000_000
    assert team["tokens_used"] == 960_000                  # informational only
    assert team["allowance"]["usd_micros"] == 100_000_000  # the window-scaled team budget
    assert team["utilization_pct"] == 0.97
    assert team["status"] == "over"                        # $97 / $100
    assert team["seats_under_threshold"] == 1              # "light": $1 of a $50 seat


@requires_db
def test_reasoning_share_rolls_up_from_the_fold(engine, catalog):
    """F5: `org_usage()` wires `aggregate.reasoning_share` straight over the same `rows` its
    other figures already use -- this just proves the wiring, not the aggregate's own math
    (covered directly in test_manager_aggregate.py)."""
    with session_scope() as s:
        _seed(s, catalog)
        _add_session(s, "s3", "csv:heavy", 100_000, {"reasoning_tokens": 5_000})
    with session_scope() as s:
        result = org_usage(s, catalog, ApiConfig(), FRM, TO)

    assert result["data"]["reasoning_share"] == {
        "reasoning_tokens": 5_000, "tokens_total": 1_060_000, "pct": 0.5,
    }


@requires_db
def test_session_types_and_no_output_pct(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = org_usage(s, catalog, ApiConfig(), FRM, TO)

    types = {t["type"]: t["count"] for t in result["data"]["session_types"]}
    assert types == {"IMPLEMENT": 1, "BUG_FIX": 1}
    assert result["data"]["sessions_no_output_pct"] == 1.0  # neither session touched a file


@requires_db
def test_org_usage_empty_scope_safe(engine, catalog):
    with session_scope() as s:
        result = org_usage(s, catalog, ApiConfig(), FRM, TO)
    assert result["data"]["teams"] == []
    assert result["data"]["header"]["sessions"] == 0
    assert result["data"]["spotlight_team"] is None


@requires_db
def test_spotlight_team_is_the_over_budget_one_expanded_to_people(engine, catalog):
    """G3 (mvp-gap-closure-plan): the mockup's "who's using their seat in <team>" -- Platform
    is the only (and over-budget) team here, so it's the spotlight, with "heavy" over their own
    $50 seat and "light" under it."""
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = org_usage(s, catalog, ApiConfig(), FRM, TO)

    spotlight = result["data"]["spotlight_team"]
    assert spotlight["team_id"] == "platform"
    by_id = {p["user_id"]: p for p in spotlight["people"]}
    assert by_id["csv:heavy"]["allowance"]["usd_micros"] == 50_000_000
    assert by_id["csv:heavy"]["utilization_pct"] == 96 / 50
    assert by_id["csv:heavy"]["status"] == "over"
    assert by_id["csv:light"]["status"] == "under"      # $1 of $50
    assert by_id["csv:light"]["tokens_used"] == 10_000  # informational only


@requires_db
def test_idle_team_appears_as_under(engine, catalog):
    """A team whose only member has no sessions used to be absent (it had no rows to group);
    with a budget it is exactly the "under" team this screen exists to show."""
    with session_scope() as s:
        _seed(s, catalog,
              extra_records=[DirectoryRecord(external_keys=(ExternalKey("csv", "quiet"),),
                                             email="quiet@x.com", teams=(TeamMembership("data"),))],
              extra_teams=[TeamRecord("data", "default", "Data")])
        budgets_service.put_budget(s, catalog, "team", "data", "monthly", Decimal("300.00"))
    with session_scope() as s:
        result = org_usage(s, catalog, ApiConfig(), FRM, TO)

    data_team = next(t for t in result["data"]["teams"] if t["team_id"] == "data")
    assert data_team["utilization_pct"] == 0.0
    assert data_team["status"] == "under"
    assert data_team["seats_under_threshold"] == 1
