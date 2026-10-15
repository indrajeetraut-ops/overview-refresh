"""Directory read service (IDENT §3/§5, plan G6). DB-gated.

Fixture: a 3-level tree  acme-eng -> {platform -> platform-infra, data}
  lead@platform : Lena (github:lena)      dev : Dana (github:dana, platform), Omar (csv:omar@acme.com, data)
  admin@org     : Ada  (csv:ada@acme.com, member of acme-eng)
"""
from datetime import datetime, timedelta, timezone

from server.directory import (
    TRUSTED_VIEWER_HEADER,
    DirectoryConfig,
    DirectoryRecord,
    ExternalKey,
    Precedence,
    RoleGrant,
    TeamMembership,
    TeamRecord,
    descendants,
    members_of,
    resolve_user_id,
    resolve_viewer,
    sync,
    team_of,
    users_in_scope,
    viewer_id_from_headers,
)
from server.storage.engine import session_scope
from server.storage.schema import SessionInfo
from server.storage.schema import Session as SessionRow

from .conftest import requires_db

CFG = DirectoryConfig()
PREC = Precedence(CFG.precedence)
_T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)

TEAMS = [
    TeamRecord("acme-eng", "acme", "Engineering", None, "csv"),
    TeamRecord("platform", "acme", "Platform", "acme-eng", "csv"),
    TeamRecord("platform-infra", "acme", "Infra", "platform", "csv"),
    TeamRecord("data", "acme", "Data", "acme-eng", "csv"),
]

LENA, DANA, OMAR, ADA = "github:lena", "github:dana", "csv:omar@acme.com", "csv:ada@acme.com"


def _rec(email, keys, teams=(), roles=()):
    return DirectoryRecord(
        external_keys=tuple(ExternalKey(*k) for k in keys),
        email=email, display_name=email.split("@")[0].title(),
        teams=tuple(teams), roles=tuple(roles), source_provider="csv",
    )


RECORDS = [
    _rec("lena@acme.com", [("csv", "lena@acme.com"), ("github", "lena")],
         teams=[TeamMembership("platform", "lead")],
         roles=[RoleGrant("lead", "team", "platform")]),
    _rec("dana@acme.com", [("csv", "dana@acme.com"), ("github", "dana")],
         teams=[TeamMembership("platform", None)]),
    _rec("omar@acme.com", [("csv", "omar@acme.com")], teams=[TeamMembership("data", None)]),
    _rec("ada@acme.com", [("csv", "ada@acme.com")],
         teams=[TeamMembership("acme-eng", None)],
         roles=[RoleGrant("admin", "org", "acme")]),
]


def _seed(catalog):
    with session_scope() as s:
        sync(s, catalog, RECORDS, teams=TEAMS, precedence=PREC, config=CFG)


def _sess(session_id, user_id, repo_id, prov="remote"):
    return (
        SessionRow(
            session_id=session_id, producer_id="p", org_id="acme", user_id=user_id,
            agent="claude_code", status="ended", started_at=_T0,
            last_event_at=_T0 + timedelta(hours=1), signal_blob={}, info_blob={},
            parent_session_id=None, subagents=[],
        ),
        SessionInfo(
            session_id=session_id, repo_id=repo_id, repo_id_provenance=prov,
            models={}, tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
            info={}, otel={}, created_at=_T0, updated_at=_T0,
        ),
    )


def test_f2_auth_seam_reads_the_trusted_header_case_insensitively():
    # no DB: the seam SSO later replaces the body of (IDENT §8).
    assert viewer_id_from_headers({TRUSTED_VIEWER_HEADER: "github:lena"}) == "github:lena"
    assert viewer_id_from_headers({"X-Talium-Viewer": "  lena@acme.com "}) == "lena@acme.com"
    assert viewer_id_from_headers({"authorization": "Bearer x"}) is None
    assert viewer_id_from_headers({TRUSTED_VIEWER_HEADER: "   "}) is None


@requires_db
def test_hierarchy_walk_resolves_three_levels(engine, catalog):
    _seed(catalog)
    with session_scope() as s:
        assert set(descendants(s, catalog, "acme-eng")) == {
            "acme-eng", "platform", "platform-infra", "data"
        }
        assert set(descendants(s, catalog, "platform")) == {"platform", "platform-infra"}
        assert descendants(s, catalog, "data") == ["data"]


@requires_db
def test_team_of_puts_primary_first(engine, catalog):
    _seed(catalog)
    with session_scope() as s:
        assert team_of(s, catalog, DANA) == ["platform"]


@requires_db
def test_members_of_with_and_without_subteams(engine, catalog):
    _seed(catalog)
    with session_scope() as s:
        assert members_of(s, catalog, "acme-eng") == [ADA]  # only direct member
        allsub = set(members_of(s, catalog, "acme-eng", include_subteams=True))
    assert allsub == {LENA, DANA, OMAR, ADA}


@requires_db
def test_resolve_viewer_lead_sees_only_their_subtree(engine, catalog):
    _seed(catalog)
    with session_scope() as s:
        v = resolve_viewer(s, catalog, LENA, CFG)
    assert v["user_id"] == LENA
    assert set(v["visible_teams"]) == {"platform", "platform-infra"}
    assert "data" not in v["visible_teams"] and "acme-eng" not in v["visible_teams"]


@requires_db
def test_resolve_viewer_dev_scope_excludes_other_teams(engine, catalog):
    _seed(catalog)
    with session_scope() as s:
        v = resolve_viewer(s, catalog, DANA, CFG)
    assert v["visible_teams"] == ["platform"]              # own team only, no elevation
    assert [r["role"] for r in v["roles"]] == ["dev"]      # the default dev@org grant


@requires_db
def test_resolve_viewer_admin_sees_every_team(engine, catalog):
    _seed(catalog)
    with session_scope() as s:
        v = resolve_viewer(s, catalog, ADA, CFG)
    assert set(v["visible_teams"]) == {"acme-eng", "platform", "platform-infra", "data"}


@requires_db
def test_resolve_user_id_via_external_key_and_unknown(engine, catalog):
    _seed(catalog)
    with session_scope() as s:
        assert resolve_user_id(s, catalog, "lena@acme.com") == LENA   # external id on the spine
        assert resolve_user_id(s, catalog, LENA) == LENA              # already a user_id
        assert resolve_user_id(s, catalog, "nobody@nowhere") is None
        assert resolve_viewer(s, catalog, "nobody@nowhere", CFG) is None


@requires_db
def test_users_in_scope(engine, catalog):
    _seed(catalog)
    with session_scope() as s:
        assert set(users_in_scope(s, catalog, "team", "acme-eng")) == {LENA, DANA, OMAR, ADA}
        assert users_in_scope(s, catalog, "team", "data") == [OMAR]
        assert len(users_in_scope(s, catalog, "org", "acme")) == 4


@requires_db
def test_visible_repos_scoping(engine, catalog):
    _seed(catalog)
    with session_scope() as s:
        s.add_all([
            *_sess("d1", DANA, "github:acme/api"),
            *_sess("o1", OMAR, "github:acme/warehouse"),
            *_sess("d2", DANA, "local:scratch", prov="path"),
        ])
    with session_scope() as s:
        lena = resolve_viewer(s, catalog, LENA, CFG)
        dana = resolve_viewer(s, catalog, DANA, CFG)
    # lena leads platform -> sees dana's remote repo; not dana's local label, not omar's repo
    assert "github:acme/api" in lena["visible_repos"]
    assert "local:scratch" not in lena["visible_repos"]
    assert "github:acme/warehouse" not in lena["visible_repos"]
    # dana sees her own repos including the local one; nothing of omar's
    assert {"github:acme/api", "local:scratch"} <= set(dana["visible_repos"])
    assert "github:acme/warehouse" not in dana["visible_repos"]
