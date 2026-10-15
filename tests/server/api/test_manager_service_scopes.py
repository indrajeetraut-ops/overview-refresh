"""U7-econ-picker `GET /v1/scopes` -- service-level (`server/api/manager/service.py`).

Plan Verify: returns all teams/projects the viewer can see, no truncation, no timeframe
dependence.
"""
from server.api.manager.service import list_scopes
from server.directory import DirectoryRecord, ExternalKey, TeamMembership, TeamRecord, sync
from server.storage import session_scope

from .conftest import requires_db


def _seed_teams(s, catalog, n: int) -> list[str]:
    team_ids = [f"team{i}" for i in range(n)]
    sync(
        s, catalog,
        [DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                         teams=(TeamMembership(team_ids[0]),))],
        teams=[TeamRecord(tid, "default", f"Team {i}") for i, tid in enumerate(team_ids)],
    )
    return team_ids


@requires_db
def test_list_scopes_returns_every_visible_team_no_truncation(engine, catalog):
    # 60 teams -- past the old /v1/teams `limit=50` the picker used to inherit.
    with session_scope() as s:
        team_ids = _seed_teams(s, catalog, 60)
    with session_scope() as s:
        result = list_scopes(s, catalog, team_ids, [])
    assert len(result["teams"]) == 60
    assert {t["id"] for t in result["teams"]} == set(team_ids)


@requires_db
def test_list_scopes_sorts_teams_by_name(engine, catalog):
    with session_scope() as s:
        team_ids = _seed_teams(s, catalog, 3)
    with session_scope() as s:
        result = list_scopes(s, catalog, team_ids, [])
    assert [t["name"] for t in result["teams"]] == ["Team 0", "Team 1", "Team 2"]


@requires_db
def test_list_scopes_only_includes_visible_teams(engine, catalog):
    with session_scope() as s:
        team_ids = _seed_teams(s, catalog, 5)
    with session_scope() as s:
        result = list_scopes(s, catalog, team_ids[:2], [])
    assert len(result["teams"]) == 2


def test_list_scopes_derives_project_names_from_repo_id_no_db_join(catalog):
    # No DB session at all (`s=None`) -- git_ident.describe is a pure string transform and
    # an empty `visible_teams` never issues a query, so this must not need a DB round trip
    # or a timeframe.
    result = list_scopes(None, catalog, [], ["github:acme/api", "local:my-repo"])
    assert result["projects"] == [
        {"id": "github:acme/api", "name": "acme/api"},
        {"id": "local:my-repo", "name": "my-repo"},
    ]


def test_list_scopes_empty_visibility_returns_empty_lists(catalog):
    result = list_scopes(None, catalog, [], [])
    assert result == {"teams": [], "projects": []}
