"""sync() against a real Postgres (IDENT §3, plan G2/G3). Skips without TALIUM_TEST_DSN."""
from sqlalchemy import text

from server.directory import (
    DirectoryRecord,
    ExternalKey,
    Precedence,
    RoleGrant,
    TeamMembership,
    TeamRecord,
    sync,
)
from server.storage.engine import session_scope

from .conftest import requires_db


def _rec(keys, **kw) -> DirectoryRecord:
    return DirectoryRecord(external_keys=tuple(ExternalKey(*k) for k in keys), **kw)


def _fixture_roster() -> tuple[list[TeamRecord], list[DirectoryRecord]]:
    teams = [
        TeamRecord("eng", "acme", "Engineering", None, "csv"),
        TeamRecord("platform", "acme", "Platform", "eng", "csv"),
    ]
    records = [
        _rec(
            [("csv", "anil@acme.com"), ("github", "anilk")],
            email="anil@acme.com",
            display_name="Anil K",
            teams=(TeamMembership("platform", "lead"),),
            roles=(RoleGrant("lead", "team", "platform"),),
            source_provider="csv",
        ),
        _rec(
            [("csv", "bea@acme.com")],
            email="bea@acme.com",
            display_name="Bea",
            teams=(TeamMembership("platform", None),),
            source_provider="csv",
        ),
    ]
    return teams, records


@requires_db
def test_fixture_roster_syncs(engine, catalog, config, precedence):
    teams, records = _fixture_roster()
    with session_scope() as s:
        result = sync(s, catalog, records, teams=teams, precedence=precedence, config=config)
    assert result.created == 2
    assert result.linked == 3
    assert result.skipped == 0
    with session_scope() as s:
        assert s.execute(text("SELECT count(*) FROM teams")).scalar() == 2
        assert s.execute(text("SELECT count(*) FROM team_members")).scalar() == 2
        assert s.execute(
            text("SELECT parent_team_id FROM teams WHERE team_id='platform'")
        ).scalar() == "eng"
        # bea declared no roles -> the default dev@org grant (IDENT §3.2)
        bea = s.execute(
            text("SELECT user_id FROM identity_links WHERE external_id='bea@acme.com'")
        ).scalar()
        grant = s.execute(
            text("SELECT role, scope_type, scope_id FROM role_assignments WHERE user_id=:u"),
            {"u": bea},
        ).one()
        assert (grant.role, grant.scope_type, grant.scope_id) == ("dev", "org", "default")


@requires_db
def test_reimport_is_idempotent(engine, catalog, config, precedence):
    teams, records = _fixture_roster()
    with session_scope() as s:
        sync(s, catalog, records, teams=teams, precedence=precedence, config=config)
    with session_scope() as s:
        again = sync(s, catalog, records, teams=teams, precedence=precedence, config=config)
    assert again.created == 0
    assert again.linked == 0
    assert again.updated == 0  # IS DISTINCT FROM guard: a no-change re-import touches nothing
    with session_scope() as s:
        assert s.execute(text("SELECT count(*) FROM users")).scalar() == 2
        assert s.execute(text("SELECT count(*) FROM team_members")).scalar() == 2
        assert s.execute(text("SELECT count(*) FROM role_assignments")).scalar() == 2


@requires_db
def test_csv_cannot_clobber_an_idp_owned_field(engine, catalog, config, precedence):
    # an IdP (scim) already owns this person's row.
    with session_scope() as s:
        s.execute(
            text("INSERT INTO users (user_id, email, display_name, status, source_provider, info, created_at) "
                 "VALUES ('github:carol', 'carol@corp.com', 'Carol Ir', 'active', 'scim', '{}'::jsonb, now())")
        )
        s.execute(
            text("INSERT INTO identity_links (provider, external_id, user_id) "
                 "VALUES ('github', 'carol', 'github:carol')")
        )
    csv_rec = _rec(
        [("github", "carol")], email="stale@old.com", display_name="STALE", source_provider="csv"
    )
    with session_scope() as s:
        result = sync(s, catalog, [csv_rec], precedence=precedence, config=config)
    assert result.updated == 0
    with session_scope() as s:
        row = s.execute(
            text("SELECT email, display_name, source_provider FROM users WHERE user_id='github:carol'")
        ).one()
    assert row.display_name == "Carol Ir"      # scim value survives
    assert row.email == "carol@corp.com"
    assert row.source_provider == "scim"


@requires_db
def test_same_provider_reimport_updates_fields(engine, catalog, config, precedence):
    rec_v1 = _rec([("csv", "d@acme.com")], email="d@acme.com", display_name="Dee", source_provider="csv")
    with session_scope() as s:
        sync(s, catalog, [rec_v1], precedence=precedence, config=config)
    rec_v2 = _rec([("csv", "d@acme.com")], email="d@acme.com", display_name="Deepa", source_provider="csv")
    with session_scope() as s:
        result = sync(s, catalog, [rec_v2], precedence=precedence, config=config)
    assert result.updated == 1
    with session_scope() as s:
        assert s.execute(
            text("SELECT display_name FROM users WHERE user_id='csv:d@acme.com'")
        ).scalar() == "Deepa"


@requires_db
def test_unresolvable_record_is_skipped_not_fatal(engine, catalog, config, precedence):
    good = _rec([("csv", "e@acme.com")], email="e@acme.com", display_name="Eff")
    bad = _rec([("csv", "ghost@acme.com")])  # no email/name, resolves to nothing
    with session_scope() as s:
        result = sync(s, catalog, [good, bad], precedence=precedence, config=config)
    assert result.created == 1
    assert result.skipped == 1
    assert result.notes and "ghost@acme.com" in result.notes[0]


@requires_db
def test_membership_replaced_for_synced_user(engine, catalog, config, precedence):
    v1 = _rec(
        [("csv", "f@acme.com")], email="f@acme.com", display_name="Faye",
        teams=(TeamMembership("platform", "member"),),
    )
    with session_scope() as s:
        sync(s, catalog, [v1], teams=[TeamRecord("platform", "acme", "Platform")],
             precedence=precedence, config=config)
    v2 = _rec(
        [("csv", "f@acme.com")], email="f@acme.com", display_name="Faye",
        teams=(TeamMembership("eng", "lead"),),
    )
    with session_scope() as s:
        sync(s, catalog, [v2], teams=[TeamRecord("eng", "acme", "Engineering")],
             precedence=precedence, config=config)
    with session_scope() as s:
        rows = s.execute(
            text("SELECT team_id, role_in_team FROM team_members WHERE user_id='csv:f@acme.com'")
        ).all()
    assert rows == [("eng", "lead")]
