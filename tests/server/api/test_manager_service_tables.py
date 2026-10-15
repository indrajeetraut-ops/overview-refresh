"""A3 GET /v1/teams + /v1/projects -- service-level (`server/api/manager/service.py`).

Plan Verify: "tables sort/paginate; a row's fields match teamMeta/projectMeta."
"""
from datetime import datetime, timedelta, timezone

import pytest

from server.api.config import ApiConfig
from server.api.errors import BadRequestError
from server.api.manager import budget_cycles
from server.api.manager import budgets as budgets_service
from server.api.manager.service import projects_table, teams_table
from server.directory import DirectoryRecord, ExternalKey, TeamMembership, TeamRecord, sync
from server.storage import session_scope
from server.storage.schema import GitArtifact, GitRepo
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

from .conftest import requires_db

FRM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime(2026, 1, 31, tzinfo=timezone.utc)
MID = datetime(2026, 1, 15, tzinfo=timezone.utc)
REPO_NUM = 777  # git-source's own numeric id for "acme/api" -- see the GitRepo bridge row below


def _add_session(s, sid, user_id, cost, repo_id=None, repo_prov=None):
    s.add(SessionRow(session_id=sid, producer_id="p", org_id="default", user_id=user_id,
                     agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                     ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id=sid, created_at=MID, updated_at=MID, models={}, tools={},
                      files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                      long_turn_count=0, info={}, otel={}, datasource="both",
                      cost_effective_usd_micros=cost, repo_id=repo_id,
                      repo_id_provenance=repo_prov))


def _seed(s, catalog):
    sync(
        s, catalog,
        [
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                            teams=(TeamMembership("platform"),)),
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev2"),), email="dev2@x.com",
                            teams=(TeamMembership("growth"),)),
        ],
        teams=[TeamRecord("platform", "default", "Platform"), TeamRecord("growth", "default", "Growth")],
    )
    _add_session(s, "s1", "csv:dev1", 30_000_000, "github:acme/api", "remote")
    _add_session(s, "s2", "csv:dev2", 10_000_000, "github:acme/web", "remote")
    # the bridge (aggregate.resolve_git_repo_id): "acme/api" has a GitHub App installed and
    # backfilled; "acme/web" does not, so its git_artifacts-derived fields stay honestly empty.
    s.add(GitRepo(repo_id=f"github:{REPO_NUM}", repo_num=REPO_NUM, full_name="acme/api",
                 owner="acme", default_branch="main", private=False, installation_id=1,
                 org_id="default", backfill_state="done", first_seen_at=MID, updated_at=MID))
    s.add(GitArtifact(artifact_id=f"github:{REPO_NUM}:pr:1", kind="pr", repo_id=f"github:{REPO_NUM}",
                      org_id="default", user_id="csv:dev1", is_bot=False, state="merged",
                      created_at=MID, updated_at=MID, merged_at=MID))
    s.flush()
    budgets_service.put_budget(s, catalog, "team", "platform", "monthly", __import__("decimal").Decimal("10"))


@requires_db
def test_teams_table_sorts_by_spend_descending_by_default(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        page = teams_table(s, catalog, ApiConfig(), FRM, TO, sort=None, q=None, limit=50, offset=0)
    names = [t["name"] for t in page["data"]["items"]]
    assert names == ["Platform", "Growth"]  # $30 > $10
    assert page["data"]["total"] == 2


@requires_db
def test_teams_table_paginates(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        page = teams_table(s, catalog, ApiConfig(), FRM, TO, sort="spend", q=None, limit=1, offset=1)
    assert len(page["data"]["items"]) == 1
    assert page["data"]["items"][0]["name"] == "Growth"
    assert page["data"]["total"] == 2  # total reflects the filtered set, not just this page


@requires_db
def test_teams_table_rejects_an_unknown_sort_key(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s, pytest.raises(BadRequestError):
        teams_table(s, catalog, ApiConfig(), FRM, TO, sort="bogus", q=None, limit=50, offset=0)


@requires_db
def test_teams_table_rows_carry_budget_and_member_fields(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        page = teams_table(s, catalog, ApiConfig(), FRM, TO, sort="spend", q=None, limit=50, offset=0)
    platform = next(t for t in page["data"]["items"] if t["team_id"] == "platform")
    assert platform["members"] == 1
    assert platform["merged_prs"] == 1
    assert platform["over_budget"] is True
    assert platform["last_active_at"] is not None
    # no second utilization column: budget_pct IS the team's utilization (cost-based seats)
    assert "utilization_pct" not in platform


@requires_db
def test_teams_table_budget_pct_is_window_scaled(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    # rolling windows (2 Jan onward, so neither starts on a period start): 30 days vs 15 days,
    # both measured against the 31-day month
    start = FRM + timedelta(days=1)
    with session_scope() as s:
        month = teams_table(s, catalog, ApiConfig(), start, start + timedelta(days=30), sort=None,
                            q=None, limit=50, offset=0)
        half = teams_table(s, catalog, ApiConfig(), start, start + timedelta(days=15), sort=None,
                           q=None, limit=50, offset=0)

    def platform(page):
        return next(t for t in page["data"]["items"] if t["team_id"] == "platform")

    assert platform(half)["budget_pct"] == pytest.approx(2 * platform(month)["budget_pct"])


@requires_db
def test_teams_table_no_longer_sorts_by_utilization(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s, pytest.raises(BadRequestError):
        teams_table(s, catalog, ApiConfig(), FRM, TO, sort="utilization_pct", q=None, limit=50,
                    offset=0)


@requires_db
def test_teams_table_search_by_name(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        page = teams_table(s, catalog, ApiConfig(), FRM, TO, sort=None, q="grow", limit=50, offset=0)
    assert [t["name"] for t in page["data"]["items"]] == ["Growth"]


@requires_db
def test_projects_table_one_row_per_canonical_repo(engine, catalog):
    """Also the regression test for the repo-identity bridge (aggregate.resolve_git_repo_id):
    "acme/api" resolves to its git-source GitRepo row and picks up its merged PR; "acme/web"
    has no such row and correctly reads 0, not a false match."""
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        page = projects_table(s, catalog, ApiConfig(), FRM, TO, sort="spend", q=None, limit=50, offset=0)
    repos = {p["repo_id"]: p for p in page["data"]["items"]}
    assert repos["github:acme/api"]["merged_prs"] == 1
    assert repos["github:acme/api"]["contributors"] == 1
    assert repos["github:acme/web"]["merged_prs"] == 0


@requires_db
def test_teams_table_to_date_and_whole_period_windows_use_the_full_budget(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)        # platform: $30 spend, $10 monthly budget
    feb1 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    with session_scope() as s:
        page = lambda frm, to: teams_table(s, catalog, ApiConfig(), frm, to, sort=None, q=None,
                                           limit=50, offset=0)
        early = page(FRM, FRM + timedelta(days=16))
        whole = page(FRM, feb1)
        rolling = page(FRM + timedelta(days=1), FRM + timedelta(days=16))

    def platform(p):
        return next(t for t in p["data"]["items"] if t["team_id"] == "platform")

    assert platform(early)["budget_pct"] == 3.0 and platform(whole)["budget_pct"] == 3.0
    assert platform(early)["over_budget"] is True
    assert platform(rolling)["budget_pct"] == 30_000_000 / round(10_000_000 * 15 / 31)


@requires_db
def test_teams_table_under_a_quarterly_cycle_has_no_budget_until_entered(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        budget_cycles.put_cycle(s, catalog, "default", budget_cycles.validate("quarter"))
    with session_scope() as s:
        page = teams_table(s, catalog, ApiConfig(), FRM, TO, sort=None, q=None, limit=50, offset=0)
        platform = next(t for t in page["data"]["items"] if t["team_id"] == "platform")
        assert platform["budget_pct"] is None and platform["over_budget"] is False
        budgets_service.put_budget(s, catalog, "team", "platform", "quarterly", __import__("decimal").Decimal("60"))
    with session_scope() as s:
        page = teams_table(s, catalog, ApiConfig(), FRM, TO, sort=None, q=None, limit=50, offset=0)
        platform = next(t for t in page["data"]["items"] if t["team_id"] == "platform")
    assert platform["budget_pct"] == 0.5
