"""E1 GET /v1/economics?scope_type=&scope_id= -- service-level
(`server/api/manager/service.py::economics`).

Plan Verify: "switching scope re-scales distributions + forecast; the org scope reconciles
with A1's headline."
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from server.api.config import ApiConfig
from server.api.errors import BadRequestError
from server.api.manager.service import economics, org_overview
from server.directory import DirectoryRecord, ExternalKey, TeamMembership, TeamRecord, sync
from server.storage import session_scope
from server.storage.schema import GitArtifact, PrClassification, SessionGitLink
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

from .conftest import requires_db

FRM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime(2026, 1, 31, tzinfo=timezone.utc)
MID = datetime(2026, 1, 15, tzinfo=timezone.utc)


def _add_session(s, sid, user_id, cost, started_at=MID):
    s.add(SessionRow(session_id=sid, producer_id="p", org_id="default", user_id=user_id,
                     agent="claude_code", status="ended", started_at=started_at,
                     last_event_at=started_at, ended_at=started_at, signal_blob={}, info_blob={},
                     subagents=[]))
    s.add(SessionInfo(session_id=sid, created_at=started_at, updated_at=started_at, models={},
                      tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
                      turn_count=0, long_turn_count=0, info={}, otel={}, datasource="both",
                      cost_effective_usd_micros=cost))


def _add_pr(s, pr_id, user_id, cost, session_id, *, is_revert=False, rework_ratio=None,
           merged_at=MID, created_at=MID):
    s.add(GitArtifact(artifact_id=pr_id, kind="pr", repo_id="github:1", org_id="default",
                      number=1, user_id=user_id, is_bot=False, state="merged",
                      created_at=created_at, updated_at=merged_at, merged_at=merged_at,
                      stats={}))
    s.add(PrClassification(artifact_id=pr_id, change_type="feature", change_confidence=1,
                           change_tier=0, artifact_class="coding", is_revert=is_revert,
                           is_recall=False, rework_ratio=rework_ratio, features_digest={},
                           analyzer="heuristic", classified_at=MID))
    s.add(SessionGitLink(session_id=session_id, artifact_id=pr_id, method="trailer",
                         confidence=1, signals={}, created_at=MID))
    _add_session(s, session_id, user_id, cost)


def _seed_team(s, catalog):
    sync(s, catalog, [
        DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                        teams=(TeamMembership("platform"),)),
    ], teams=[TeamRecord("platform", "default", "Platform")])


@requires_db
def test_unknown_scope_type_is_a_bad_request(engine, catalog):
    with session_scope() as s:
        with pytest.raises(BadRequestError):
            economics(s, catalog, ApiConfig(), "bogus", "x", FRM, TO)


@requires_db
def test_team_scope_computes_dist_envelope_and_rework_ratio(engine, catalog):
    with session_scope() as s:
        _seed_team(s, catalog)
        _add_pr(s, "github:1:pr:1", "csv:dev1", 10_000_000, "s1", rework_ratio=Decimal("0.2"))
        _add_pr(s, "github:1:pr:2", "csv:dev1", 30_000_000, "s2", rework_ratio=Decimal("0.4"),
               is_revert=True)
        s.flush()
    with session_scope() as s:
        result = economics(s, catalog, ApiConfig(), "team", "platform", FRM, TO)

    data = result["data"]
    assert data["scope_type"] == "team"
    assert data["scope_id"] == "platform"
    assert data["merged_prs"] == 2
    assert data["reverted_prs"] == 1
    assert data["reverted_pr_cost"]["usd_micros"] == 30_000_000
    assert data["rework_ratio"] == pytest.approx(0.3)      # avg(0.2, 0.4)
    # cost_dist is in whole dollars (Dist's own unitless convention), unlike every other
    # _usd_micros figure in this response -- the bug this pins down: E1's first cut passed
    # raw usd_micros through unconverted (this assertion itself made the same wrong
    # assumption at first, so it never caught it).
    assert data["cost_dist"]["min"] == 10.0
    assert data["cost_dist"]["max"] == 30.0
    assert data["pr_cost_envelope"]["min_usd_micros"] == 10_000_000
    assert len(data["forecast_cone"]) == 7                  # step 0..6
    # the cone strictly widens
    widths = [c["band_hi_usd_micros"] - c["band_lo_usd_micros"] for c in data["forecast_cone"]]
    assert widths == sorted(widths)
    assert len(set(widths)) == len(widths)


@requires_db
def test_team_scope_with_no_activity_is_all_cold_start_not_zero(engine, catalog):
    with session_scope() as s:
        _seed_team(s, catalog)
    with session_scope() as s:
        result = economics(s, catalog, ApiConfig(), "team", "platform", FRM, TO)

    data = result["data"]
    assert data["merged_prs"] == 0
    assert data["reverted_prs"] == 0
    assert data["cost_dist"] is None
    assert data["time_to_merge_dist"] is None
    assert data["pr_cost_envelope"] is None
    assert data["forecast_cone"] == []
    assert data["rework_ratio"] is None
    assert data["budget_burn"] is None


@requires_db
def test_repo_scope_only_counts_that_repos_own_activity(engine, catalog):
    """A contributor's spend on an unrelated repo must never leak into a project's own
    economics -- the reason repo scope walks repo_sessions_in_range, not a user-based fetch."""
    from server.storage.schema import GitRepo

    with session_scope() as s:
        s.add(GitRepo(repo_id="github:1", repo_num=1, full_name="acme/api", owner="acme",
                     default_branch="main", private=False, installation_id=1, org_id="default",
                     backfill_state="done", first_seen_at=MID, updated_at=MID))
        # in-repo session, tagged with the session-side repo_id
        s.add(SessionRow(session_id="in-repo", producer_id="p", org_id="default", user_id="csv:dev1",
                         agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                         ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
        s.add(SessionInfo(session_id="in-repo", created_at=MID, updated_at=MID, models={},
                          tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
                          turn_count=0, long_turn_count=0, info={}, otel={}, datasource="both",
                          cost_effective_usd_micros=5_000_000, repo_id="github:acme/api",
                          repo_id_provenance="remote"))
        # unrelated-repo session by the same user -- must not leak into this repo's economics
        s.add(SessionRow(session_id="other-repo", producer_id="p", org_id="default", user_id="csv:dev1",
                         agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                         ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
        s.add(SessionInfo(session_id="other-repo", created_at=MID, updated_at=MID, models={},
                          tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
                          turn_count=0, long_turn_count=0, info={}, otel={}, datasource="both",
                          cost_effective_usd_micros=999_000_000, repo_id="github:acme/other",
                          repo_id_provenance="remote"))
        s.flush()
    with session_scope() as s:
        result = economics(s, catalog, ApiConfig(), "repo", "github:acme/api", FRM, TO)
    # the huge unrelated-repo session must not appear at all
    assert result["mode"]["sessions_by_mode"].get("both", 0) == 1


@requires_db
def test_budget_burn_forecasts_to_the_month_end_not_the_window_end(engine, catalog):
    """E1-budget-period: a short display window must still project the burn-up to the
    configured monthly budget period's own last day, not to the window's end. This pins the
    old degenerate behaviour, where `days_in_period == window length == days reported` left the
    projection leg empty (the forecast never extended past the handoff)."""
    jan5 = datetime(2026, 1, 5, tzinfo=timezone.utc)
    short_to = datetime(2026, 1, 8, tzinfo=timezone.utc)  # a 7-day window, well short of the month
    with session_scope() as s:
        _seed_team(s, catalog)
        _add_session(s, "s1", "csv:dev1", 20_000_000, started_at=jan5)
        s.flush()
    with session_scope() as s:
        result = economics(s, catalog, ApiConfig(), "team", "platform", FRM, short_to)

    burn = result["data"]["budget_burn"]
    assert burn is not None
    # January is 31 days -> the burn-up projects to day 31, not to the window's day 7.
    assert burn[-1]["day"] == 31
    assert burn[-1]["projected_usd_micros"] is not None
    # The handoff sits at the reported-days boundary (Jan 1..7 elapsed before the in-progress
    # `to` day), carrying both an actual and a projected figure, equal.
    handoff = next(p for p in burn if p.get("actual_usd_micros") is not None
                   and p.get("projected_usd_micros") is not None)
    assert handoff["day"] == 7
    assert handoff["actual_usd_micros"] == 20_000_000  # the lone Jan-5 session, cumulative


@requires_db
def test_org_scope_reconciles_with_a1_headline(engine, catalog):
    with session_scope() as s:
        _seed_team(s, catalog)
        _add_session(s, "s1", "csv:dev1", 42_000_000)
        s.flush()
    with session_scope() as s:
        org_result = org_overview(s, catalog, ApiConfig(), FRM, TO)
        econ_result = economics(s, catalog, ApiConfig(), "org", "default", FRM, TO)
    assert org_result["data"]["headline"]["spend"]["usd_micros"] == 42_000_000
    # A1 has no merged PRs in this fixture either -- both cold-start the same way
    assert econ_result["data"]["merged_prs"] == 0


@requires_db
def test_budget_burn_stays_on_the_calendar_month_under_every_budget_cycle(engine, catalog):
    """The burn-up carries spend only and the forecast is by calendar month, so a quarterly or
    sprint cycle must not change its horizon (TODO(A4): per-cycle burn-up)."""
    from server.api.manager import budget_cycles

    jan5 = datetime(2026, 1, 5, tzinfo=timezone.utc)
    short_to = datetime(2026, 1, 8, tzinfo=timezone.utc)
    with session_scope() as s:
        _seed_team(s, catalog)
        _add_session(s, "s1", "csv:dev1", 20_000_000, started_at=jan5)
        s.flush()
    burns = []
    for cycle in (None, budget_cycles.validate("quarter"),
                  budget_cycles.validate("sprint", sprint_weeks=2, anchor_date="2026-01-05")):
        with session_scope() as s:
            if cycle is not None:
                budget_cycles.put_cycle(s, catalog, "default", cycle)
        with session_scope() as s:
            burns.append(economics(s, catalog, ApiConfig(), "team", "platform", FRM,
                                   short_to)["data"]["budget_burn"])
    assert burns[0][-1]["day"] == 31                    # January's own length under the default
    assert burns[1] == burns[0] and burns[2] == burns[0]
