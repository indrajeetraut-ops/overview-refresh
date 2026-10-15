"""User<->repo contribution rollup (IDENT §3.4, plan G5). DB-gated."""
from datetime import datetime, timedelta, timezone

from server.directory.repo_rollup import compute_user_repos, refresh_user_repos, repo_contributors
from server.storage.engine import session_scope
from server.storage.schema import GitArtifact, SessionInfo
from server.storage.schema import Session as SessionRow

from .conftest import requires_db

_T0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _session(session_id, user_id, *, parent=None, at=_T0):
    return SessionRow(
        session_id=session_id, producer_id="p1", org_id="acme", user_id=user_id,
        agent="claude_code", status="ended", started_at=at, last_event_at=at + timedelta(hours=1),
        signal_blob={}, info_blob={}, parent_session_id=parent, subagents=[],
    )


def _info(session_id, repo_id, provenance, *, at=_T0):
    return SessionInfo(
        session_id=session_id, repo_id=repo_id, repo_id_provenance=provenance,
        models={}, tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
        info={}, otel={}, created_at=at, updated_at=at,
    )


def _artifact(artifact_id, repo_id, user_id, *, kind="pr", at=_T0):
    return GitArtifact(
        artifact_id=artifact_id, kind=kind, repo_id=repo_id, org_id="acme", user_id=user_id,
        is_bot=False, state="merged", created_at=at, updated_at=at + timedelta(days=2),
        stats={}, files=[], labels=[], commit_shas=[], info={},
    )


@requires_db
def test_repos_are_the_union_of_sessions_and_authored_artifacts(engine, catalog):
    with session_scope() as s:
        s.add_all([
            _session("s1", "u1"), _info("s1", "github:acme/api", "remote"),
            _session("s2", "u1"), _info("s2", "github:acme/api", "remote"),
            _session("s3", "u1"), _info("s3", "local:myproj", "path"),
            _artifact("a1", "github:12345", "u1"),
        ])
    with session_scope() as s:
        repos = compute_user_repos(s, catalog, "u1")

    by_id = {r["repo_id"]: r for r in repos}
    assert set(by_id) == {"github:acme/api", "github:12345", "local:myproj"}
    assert by_id["github:acme/api"]["sources"] == ["telemetry"]
    assert by_id["github:acme/api"]["session_count"] == 2
    assert by_id["github:acme/api"]["pr_count"] == 0
    assert by_id["github:12345"]["sources"] == ["github"]
    assert by_id["github:12345"]["pr_count"] == 1
    assert by_id["github:12345"]["session_count"] == 0
    # a path-tier repo is carried but flagged local (never a cross-user key)
    assert by_id["local:myproj"]["provenance"] == "path"
    # stable order by repo_id
    assert [r["repo_id"] for r in repos] == sorted(by_id)


@requires_db
def test_subagent_sessions_do_not_double_count(engine, catalog):
    with session_scope() as s:
        s.add_all([
            _session("root", "u2"), _info("root", "github:acme/api", "remote"),
            _session("sub", "u2", parent="root"), _info("sub", "github:acme/api", "remote"),
        ])
    with session_scope() as s:
        repos = compute_user_repos(s, catalog, "u2")
    assert len(repos) == 1
    assert repos[0]["session_count"] == 1  # root only


@requires_db
def test_entry_merges_when_same_repo_id_evidenced_by_both(engine, catalog):
    with session_scope() as s:
        s.add_all([
            _session("s1", "u3"), _info("s1", "github:999", "remote"),
            _artifact("a1", "github:999", "u3", kind="pr"),
            _artifact("a2", "github:999", "u3", kind="review"),
        ])
    with session_scope() as s:
        repos = compute_user_repos(s, catalog, "u3")
    assert len(repos) == 1
    e = repos[0]
    assert e["sources"] == ["github", "telemetry"]
    assert e["session_count"] == 1
    assert e["pr_count"] == 1  # the review artifact is evidence but not a PR


@requires_db
def test_refresh_writes_users_info_repos(engine, catalog):
    from sqlalchemy import text

    with session_scope() as s:
        s.execute(text("INSERT INTO users (user_id, status, info, created_at) "
                       "VALUES ('u4', 'active', '{}'::jsonb, now())"))
        s.add_all([_session("s1", "u4"), _info("s1", "github:acme/api", "remote")])
    with session_scope() as s:
        written = refresh_user_repos(s, catalog, "u4")
    with session_scope() as s:
        stored = s.execute(
            text("SELECT info -> 'repos' FROM users WHERE user_id = 'u4'")
        ).scalar()
    assert stored == written
    assert stored[0]["repo_id"] == "github:acme/api"


@requires_db
def test_reverse_direction_resolves_without_a_user_repos_table(engine, catalog):
    with session_scope() as s:
        s.add_all([
            _session("s1", "u1"), _info("s1", "github:shared", "remote"),
            _session("s2", "u2"), _info("s2", "github:shared", "remote"),
            _session("s3", "u3"), _info("s3", "github:shared", "path"),  # local -> excluded
            _artifact("a1", "github:shared", "u2", kind="pr"),
        ])
    with session_scope() as s:
        contributors = repo_contributors(s, catalog, "github:shared")
    by_user = {c["user_id"]: c for c in contributors}
    assert set(by_user) == {"u1", "u2"}            # u3's evidence is path-tier -> not counted
    assert by_user["u2"]["pr_count"] == 1
    assert by_user["u1"]["session_count"] == 1
