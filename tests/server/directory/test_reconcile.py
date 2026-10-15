"""reconcile() against a real Postgres (IDENT §3, plan G2). Skips without TALIUM_TEST_DSN."""
from sqlalchemy import text

from server.directory import DirectoryRecord, ExternalKey
from server.directory.reconcile import reconcile
from server.storage.engine import session_scope

from .conftest import requires_db


def _rec(keys, **kw) -> DirectoryRecord:
    return DirectoryRecord(external_keys=tuple(ExternalKey(*k) for k in keys), **kw)


@requires_db
def test_creates_user_and_links_when_unresolvable_but_named(engine, catalog):
    rec = _rec([("csv", "anil@acme.com"), ("github", "anilk")], email="anil@acme.com", display_name="Anil K")
    with session_scope() as s:
        out = reconcile(s, catalog, rec)
    assert out.created is True
    assert out.linked == 2
    with session_scope() as s:
        row = s.execute(
            text("SELECT display_name, source_provider FROM users WHERE user_id = :u"),
            {"u": out.user_id},
        ).one()
        assert row.display_name == "Anil K"
        links = s.execute(
            text("SELECT provider FROM identity_links WHERE user_id = :u ORDER BY provider"),
            {"u": out.user_id},
        ).scalars().all()
    assert links == ["csv", "github"]
    # a github key mints the git-source-compatible id
    assert out.user_id == "github:anilk"


@requires_db
def test_second_reconcile_is_a_noop_on_the_spine(engine, catalog):
    rec = _rec([("csv", "a@b.com")], email="a@b.com")
    with session_scope() as s:
        first = reconcile(s, catalog, rec)
    with session_scope() as s:
        second = reconcile(s, catalog, rec)
    assert first.user_id == second.user_id
    assert second.created is False
    assert second.linked == 0


@requires_db
def test_enriches_telemetry_stub_never_duplicates(engine, catalog):
    # a telemetry/git path created this row with only github_user set -- no identity_links.
    with session_scope() as s:
        s.execute(
            text("INSERT INTO users (user_id, github_user, status, info, created_at) "
                 "VALUES ('u_tel_1', 'octo', 'active', '{}'::jsonb, now())")
        )
    rec = _rec([("github", "octo")], email="octo@acme.com", display_name="Octo Cat")
    with session_scope() as s:
        out = reconcile(s, catalog, rec)
    assert out.created is False
    assert out.user_id == "u_tel_1"          # same row, not a new github:octo
    assert out.linked == 1                    # the github link is back-filled onto the spine
    with session_scope() as s:
        count = s.execute(text("SELECT count(*) FROM users")).scalar()
        link = s.execute(
            text("SELECT user_id FROM identity_links WHERE provider='github' AND external_id='octo'")
        ).scalar()
    assert count == 1
    assert link == "u_tel_1"


@requires_db
def test_rejects_record_with_no_resolvable_key_and_no_name(engine, catalog):
    rec = _rec([("csv", "ghost@acme.com")])  # no email, no display_name
    with session_scope() as s:
        try:
            reconcile(s, catalog, rec)
            raised = False
        except Exception as e:  # noqa: BLE001 -- asserting the type below
            raised = type(e).__name__
    assert raised == "UnresolvableRecordError"
