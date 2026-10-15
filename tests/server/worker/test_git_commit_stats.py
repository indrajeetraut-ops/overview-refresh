"""The `git_commit_stats` worker handler (Track churn/reopen, Landing 2): fills a push's commit
line stats from the compare API. DB-backed; skips without `TALIUM_TEST_DSN`. The GitHubClient is
faked -- no network -- so these assert the merge/skip/failure behavior, not GitHub itself."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from server.ingest.git.client import GitHubApiError
from server.storage.engine import session_scope
from server.worker.git_commit_stats import make_git_commit_stats_handler
from tests.server.worker.conftest import requires_db

REPO_ID = "github:900"
AFTER = "b" * 40
COMMIT_ID = f"{REPO_ID}:commit:{AFTER}"
MID = datetime(2026, 8, 10, tzinfo=timezone.utc)


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


class _FakeClient:
    """Minimal stand-in: records rest_get calls, returns a canned response or raises."""
    def __init__(self, resp):
        self._resp, self.calls = resp, []

    def rest_get(self, installation_id, path, params=None):
        self.calls.append((installation_id, path))
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


def _seed_repo_and_commit(s, *, stats="{}"):
    s.execute(text(
        "INSERT INTO git_repos (repo_id, repo_num, full_name, owner, default_branch, private, "
        "installation_id, org_id, backfill_state, first_seen_at, updated_at, info) VALUES "
        "(:r, 900, 'acme/widgets', 'acme', 'main', false, 55, 'default', 'done', now(), now(), '{}')"),
        {"r": REPO_ID})
    s.execute(text(
        "INSERT INTO git_artifacts (artifact_id, kind, repo_id, org_id, sha, is_bot, state, "
        "created_at, updated_at, stats, files, labels, commit_shas, info) VALUES "
        "(:a, 'commit', :r, 'default', :sha, false, 'pushed', :ts, :ts, CAST(:stats AS jsonb), "
        "'[]', '[]', '[]', '{}')"),
        {"a": COMMIT_ID, "r": REPO_ID, "sha": AFTER, "ts": MID, "stats": stats})


def _commit_stats():
    with session_scope() as s:
        return s.execute(text("SELECT stats FROM git_artifacts WHERE artifact_id = :a"),
                         {"a": COMMIT_ID}).scalar()


def _payload():
    return {"repo_id": REPO_ID, "before": "a" * 40, "after": AFTER}


@requires_db
def test_compare_stats_merged_onto_the_push_head_commit(engine, catalog):
    with session_scope() as s:
        _seed_repo_and_commit(s)
    client = _FakeClient(_Resp({"files": [
        {"additions": 10, "deletions": 4}, {"additions": 2, "deletions": 0}]}))
    with session_scope() as s:
        make_git_commit_stats_handler(client)(s, catalog, _payload())

    assert _commit_stats() == {"additions": 12, "deletions": 4, "changed_files": 2}
    # called the compare endpoint against the seeded repo's installation
    assert client.calls == [(55, f"/repos/acme/widgets/compare/{'a'*40}...{AFTER}")]


@requires_db
def test_merge_is_additive_and_preserves_other_stat_keys(engine, catalog):
    with session_scope() as s:
        _seed_repo_and_commit(s, stats='{"comment_count": 3}')
    client = _FakeClient(_Resp({"files": [{"additions": 5, "deletions": 1}]}))
    with session_scope() as s:
        make_git_commit_stats_handler(client)(s, catalog, _payload())
    assert _commit_stats() == {"comment_count": 3, "additions": 5, "deletions": 1, "changed_files": 1}


@requires_db
def test_304_leaves_stats_untouched(engine, catalog):
    with session_scope() as s:
        _seed_repo_and_commit(s)
    client = _FakeClient(_Resp(None, status=304))
    with session_scope() as s:
        make_git_commit_stats_handler(client)(s, catalog, _payload())
    assert _commit_stats() == {}


@requires_db
def test_api_error_does_not_raise_and_leaves_stats_untouched(engine, catalog):
    with session_scope() as s:
        _seed_repo_and_commit(s)
    client = _FakeClient(GitHubApiError("GET", "u", 404, "gone"))
    with session_scope() as s:
        make_git_commit_stats_handler(client)(s, catalog, _payload())   # must not raise
    assert _commit_stats() == {}


@requires_db
def test_unknown_repo_makes_no_api_call(engine, catalog):
    # No git_repos row for REPO_ID -> nothing to authenticate as; the handler no-ops.
    client = _FakeClient(_Resp({"files": []}))
    with session_scope() as s:
        make_git_commit_stats_handler(client)(s, catalog, _payload())
    assert client.calls == []
