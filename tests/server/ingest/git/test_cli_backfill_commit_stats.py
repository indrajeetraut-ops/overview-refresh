"""Commit-stats backfill (Track churn/reopen, backfill 4b): compare API -> commit stats for
pushes ingested before the git_commit_stats worker existed. Client faked. DB-backed."""
from __future__ import annotations

import json

from sqlalchemy import text

from server.ingest.git.cli_backfill_commit_stats import backfill_commit_stats
from server.storage.engine import session_scope
from tests.server.ingest.conftest import requires_db

REPO_NUM = 556
REPO_ID = f"github:{REPO_NUM}"
BEFORE, AFTER = "a" * 40, "c" * 40
COMMIT_ID = f"{REPO_ID}:commit:{AFTER}"


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


class _FakeClient:
    def __init__(self, resp):
        self._resp, self.calls = resp, []

    def rest_get(self, installation_id, path, params=None):
        self.calls.append(path)
        return self._resp


def _seed_repo(s):
    s.execute(text(
        "INSERT INTO git_repos (repo_id, repo_num, full_name, owner, default_branch, private, "
        "installation_id, org_id, backfill_state, first_seen_at, updated_at, info) VALUES "
        "(:r, :n, 'acme/svc', 'acme', 'main', false, 88, 'default', 'done', now(), now(), '{}')"),
        {"r": REPO_ID, "n": REPO_NUM})


def _seed_head_commit(s, *, stats="{}"):
    # A normal-push head commit: carries the range on info.github.push_meta, stats still empty.
    info = json.dumps({"github.push_meta": {"before_sha": BEFORE, "after_sha": AFTER,
                                            "integration_method": None, "commit_shas": [AFTER]}})
    s.execute(text(
        "INSERT INTO git_artifacts (artifact_id, kind, repo_id, org_id, sha, is_bot, state, "
        "created_at, updated_at, stats, files, labels, commit_shas, info) VALUES "
        "(:a, 'commit', :r, 'default', :sha, false, 'pushed', now(), now(), CAST(:stats AS jsonb), "
        "'[]', '[]', '[]', CAST(:info AS jsonb))"),
        {"a": COMMIT_ID, "r": REPO_ID, "sha": AFTER, "stats": stats, "info": info})


def _commit_stats():
    with session_scope() as s:
        return s.execute(text("SELECT stats FROM git_artifacts WHERE artifact_id = :a"),
                         {"a": COMMIT_ID}).scalar()


@requires_db
def test_apply_fills_stats_from_push_meta_range(engine, catalog):
    with session_scope() as s:
        _seed_repo(s)
        _seed_head_commit(s)
    client = _FakeClient(_Resp({"files": [{"additions": 8, "deletions": 3}]}))
    counts = backfill_commit_stats(client, catalog, REPO_ID, apply=True)

    assert counts == {"candidates": 1, "filled": 1}
    assert _commit_stats() == {"additions": 8, "deletions": 3, "changed_files": 1}
    assert client.calls == [f"/repos/acme/svc/compare/{BEFORE}...{AFTER}"]


@requires_db
def test_dry_run_makes_no_api_call_and_writes_nothing(engine, catalog):
    with session_scope() as s:
        _seed_repo(s)
        _seed_head_commit(s)
    client = _FakeClient(_Resp({"files": [{"additions": 8, "deletions": 3}]}))
    counts = backfill_commit_stats(client, catalog, REPO_ID, apply=False)
    assert counts == {"candidates": 1, "filled": 0}
    assert client.calls == []
    assert _commit_stats() == {}


@requires_db
def test_already_filled_commit_is_not_a_candidate(engine, catalog):
    with session_scope() as s:
        _seed_repo(s)
        _seed_head_commit(s, stats='{"additions": 1, "deletions": 1}')
    client = _FakeClient(_Resp({"files": []}))
    counts = backfill_commit_stats(client, catalog, REPO_ID, apply=True)
    assert counts["candidates"] == 0          # the `stats ? 'additions'` guard excludes it
    assert client.calls == []
