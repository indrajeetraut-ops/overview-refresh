"""Identity resolution (git-source.md §6.2/§6.3, implementation plan A6): repo_id bridging
between the collector's `github:<owner>/<name>` scheme and this module's numeric `git_repos`
scheme, and `author_login -> users.github_user -> user_id` resolution with auto-create.

The URL-normalization table itself (SSH/HTTPS/`.git`/trailing-slash/case all collapsing to one
id) is already covered by `tests/common/test_git_ident.py` -- not duplicated here."""
from __future__ import annotations

import json

from sqlalchemy import text

from common import git_ident
from server.ingest.git.apply import apply_map_result, resolve_user_id
from server.ingest.git.map_github import map_delivery
from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog
from server.storage.schema import Base
from tests.server.storage.conftest import requires_db

ORG_ID = "org_test"
REPO_NUM = 41213765


def _pr_payload(number=1184, author_login="akhuteta", author_is_bot=False):
    login = author_login
    return {
        "pull_request": {
            "number": number, "title": "t", "body": None, "state": "open", "merged": False,
            "user": {"login": login}, "head": {"ref": "b", "sha": "a" * 40}, "base": {"ref": "main"},
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "labels": [],
        },
        "repository": {"id": REPO_NUM},
    }


@requires_db
def test_resolve_repo_id_bridges_collector_scheme_to_numeric_scheme(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_repo"), {
            "repo_id": f"github:{REPO_NUM}", "repo_num": REPO_NUM, "full_name": "Talium-AI/Code-Econ",
            "owner": "talium-ai", "default_branch": "main", "private": False,
            "installation_id": 42, "org_id": ORG_ID, "info": "{}",
        })
    # The collector/repositories-table scheme, as git_ident.normalize_remote would produce it
    # from any remote URL variant -- lower-cased, "owner/name" only.
    collector_id = git_ident.normalize_remote("git@github.com:talium-ai/code-econ.git")
    assert collector_id == "github:talium-ai/code-econ"
    full_name = collector_id.removeprefix("github:")

    with session_scope() as s:
        row = s.execute(catalog.get("git", "resolve_repo_id_by_full_name"), {"full_name": full_name}).first()
    assert row[0] == f"github:{REPO_NUM}"


@requires_db
def test_resolve_repo_id_absent_when_never_seen(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        row = s.execute(catalog.get("git", "resolve_repo_id_by_full_name"),
                        {"full_name": "nobody/nothing"}).first()
    assert row is None


@requires_db
def test_author_login_auto_creates_a_user_on_first_sighting(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        user_id = resolve_user_id(s, catalog, "akhuteta", is_bot=False)
    assert user_id == "github:akhuteta"
    with session_scope() as s:
        row = s.execute(text("SELECT user_id, github_user FROM users WHERE user_id = :id"),
                        {"id": "github:akhuteta"}).first()
    assert row == ("github:akhuteta", "akhuteta")


@requires_db
def test_author_login_reuses_a_declared_mapping_never_overwrites(engine, catalog):
    """A session and a PR from the same developer resolve to the SAME user_id: a `users` row
    declared by some other path (collector-supplied login at registration, or an operator
    import -- neither built by this module) is found and reused, not shadowed by a fresh
    auto-created identity."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.execute(text(
            "INSERT INTO users (user_id, github_user, info, created_at) "
            "VALUES ('anil', 'akhuteta', '{}'::jsonb, now())"
        ))
    with session_scope() as s:
        user_id = resolve_user_id(s, catalog, "akhuteta", is_bot=False)
    assert user_id == "anil"   # the declared session-side user_id, not a new "github:akhuteta"
    with session_scope() as s:
        count = s.execute(text("SELECT count(*) FROM users WHERE github_user = 'akhuteta'")).scalar()
    assert count == 1   # no duplicate identity created


@requires_db
def test_author_login_case_insensitive_against_a_declared_mapping(engine, catalog):
    """A login declared lower-case at session-upload time must still be found when GitHub
    reports the same account in a different case (webhook/REST/GraphQL payloads aren't
    guaranteed to agree on case, and a person typing their own handle at upload time might
    not match either)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.execute(text(
            "INSERT INTO users (user_id, github_user, info, created_at) "
            "VALUES ('anil', 'akhuteta', '{}'::jsonb, now())"
        ))
    with session_scope() as s:
        user_id = resolve_user_id(s, catalog, "AKhuteta", is_bot=False)
    assert user_id == "anil"
    with session_scope() as s:
        count = s.execute(text("SELECT count(*) FROM users")).scalar()
    assert count == 1   # no duplicate identity created over a case mismatch


@requires_db
def test_bot_author_is_never_resolved_to_a_user(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        user_id = resolve_user_id(s, catalog, "dependabot[bot]", is_bot=True)
    assert user_id is None
    with session_scope() as s:
        count = s.execute(text("SELECT count(*) FROM users")).scalar()
    assert count == 0


@requires_db
def test_noreply_email_with_no_login_resolves_unmapped_and_is_counted(engine, catalog):
    """§6.3: a noreply email defeats matching outright -- this module never parses the login
    back out of the `id+login@users.noreply.github.com` shape (that would be exactly the
    "guess" it refuses to make). With no `author_login` at all, the artifact lands `user_id`
    null, and `coverage_counts` (A1) reports it as unattributed rather than silently dropping it."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    payload = {
        "ref": "refs/heads/main", "before": "b" * 40, "after": "c" * 40,
        "repository": {"id": REPO_NUM},
        "commits": [{
            "id": "c" * 40, "message": "fix", "timestamp": "2026-01-01T00:00:00Z",
            "author": {"name": "Anon", "email": "12345+anon@users.noreply.github.com"},   # no "username"
            "added": [], "removed": [], "modified": [],
        }],
    }
    result = map_delivery("push", None, payload, ORG_ID)
    with session_scope() as s:
        apply_map_result(s, QueryCatalog.load("config/queries"), result)

    with session_scope() as s:
        row = s.execute(text("SELECT user_id, author_email FROM git_artifacts WHERE kind = 'commit'")).first()
    assert row.user_id is None
    assert row.author_email == "12345+anon@users.noreply.github.com"   # kept as metadata, never parsed for identity
    with session_scope() as s:
        count = s.execute(text("SELECT count(*) FROM users")).scalar()
    assert count == 0   # nothing invented from the noreply address


@requires_db
def test_pr_author_resolves_via_apply_map_result_end_to_end(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    result = map_delivery("pull_request", "opened", _pr_payload(), ORG_ID)
    with session_scope() as s:
        apply_map_result(s, catalog, result)
    with session_scope() as s:
        row = s.execute(text("SELECT user_id FROM git_artifacts WHERE artifact_id = :id"),
                        {"id": f"github:{REPO_NUM}:pr:1184"}).first()
    assert row.user_id == "github:akhuteta"


@requires_db
def test_sha_stays_indexed_and_populated_for_future_correlation(engine, catalog):
    """Correlation (pr-work-classification.md, not built here) joins on `sha` -- this module's
    job is only to make sure that key is always populated and indexed regardless of whether the
    collector git-hook plugin (Part C, deferred) exists. Confirms both halves independent of any
    correlator: the artifact carries a real sha, and the column is indexed (A1)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    result = map_delivery("pull_request", "opened", _pr_payload(), ORG_ID)
    with session_scope() as s:
        apply_map_result(s, catalog, result)
    with session_scope() as s:
        row = s.execute(text("SELECT sha FROM git_artifacts WHERE artifact_id = :id"),
                        {"id": f"github:{REPO_NUM}:pr:1184"}).first()
    assert row.sha == "a" * 40
    assert "ix_git_artifacts_sha" in {ix.name for ix in Base.metadata.tables["git_artifacts"].indexes}
