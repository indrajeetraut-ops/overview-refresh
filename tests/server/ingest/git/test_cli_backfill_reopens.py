"""Reopen-history backfill (Track churn/reopen, backfill 4a): timeline -> git_artifact_events.
The GraphQL client is faked -- no network. DB-backed; skips without `TALIUM_TEST_DSN`."""
from __future__ import annotations

from sqlalchemy import text

from server.ingest.git.cli_backfill_reopens import backfill_repo_reopens, reopen_events_from_nodes
from server.storage.engine import session_scope
from tests.server.ingest.conftest import requires_db

REPO_NUM = 555
REPO_ID = f"github:{REPO_NUM}"
FULL_NAME = "acme/svc"


def test_reopen_events_from_nodes_skips_nodes_without_a_timestamp():
    events = reopen_events_from_nodes(
        [{"createdAt": "2026-08-03T12:00:00Z"}, {}, {"createdAt": None}], f"{REPO_ID}:pr:9")
    assert events == [{
        "artifact_id": f"{REPO_ID}:pr:9", "action": "reopened", "to_state": "open",
        "occurred_at": "2026-08-03T12:00:00Z",
        "source_key": f"{REPO_ID}:pr:9:reopened:2026-08-03T12:00:00Z",
    }]


class _FakeGraphQLClient:
    """Returns a canned single-page timeline for whatever PR number is asked; records calls."""
    def __init__(self, reopens_by_number: dict[int, list[str]]):
        self._reopens = reopens_by_number
        self.calls = []

    def graphql(self, installation_id, query, variables):
        self.calls.append((installation_id, variables["number"]))
        nodes = [{"createdAt": ts} for ts in self._reopens.get(variables["number"], [])]
        return {"repository": {"pullRequest": {"timelineItems": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}


def _seed(s, numbers):
    s.execute(text(
        "INSERT INTO git_repos (repo_id, repo_num, full_name, owner, default_branch, private, "
        "installation_id, org_id, backfill_state, first_seen_at, updated_at, info) VALUES "
        "(:r, :n, :fn, 'acme', 'main', false, 88, 'default', 'done', now(), now(), '{}')"),
        {"r": REPO_ID, "n": REPO_NUM, "fn": FULL_NAME})
    for num in numbers:
        s.execute(text(
            "INSERT INTO git_artifacts (artifact_id, kind, repo_id, org_id, number, is_bot, "
            "state, created_at, updated_at, stats, files, labels, commit_shas, info) VALUES "
            "(:a, 'pr', :r, 'default', :num, false, 'merged', now(), now(), '{}', '[]', '[]', "
            "'[]', '{}')"),
            {"a": f"{REPO_ID}:pr:{num}", "r": REPO_ID, "num": num})


def _reopen_rows():
    with session_scope() as s:
        return s.execute(text(
            "SELECT artifact_id, occurred_at FROM git_artifact_events WHERE action = 'reopened' "
            "ORDER BY artifact_id, occurred_at")).all()


@requires_db
def test_apply_writes_one_row_per_timeline_reopen(engine, catalog):
    with session_scope() as s:
        _seed(s, [1, 2])
    # PR 1 reopened twice, PR 2 never reopened.
    client = _FakeGraphQLClient({1: ["2026-08-01T00:00:00Z", "2026-08-05T00:00:00Z"], 2: []})
    counts = backfill_repo_reopens(client, catalog, FULL_NAME, apply=True)

    assert counts == {"prs_scanned": 2, "reopens_found": 2, "rows_written": 2}
    rows = _reopen_rows()
    assert [(r.artifact_id) for r in rows] == [f"{REPO_ID}:pr:1", f"{REPO_ID}:pr:1"]
    assert {n for _i, n in client.calls} == {1, 2}   # every known PR was queried


@requires_db
def test_dry_run_writes_nothing(engine, catalog):
    with session_scope() as s:
        _seed(s, [1])
    client = _FakeGraphQLClient({1: ["2026-08-01T00:00:00Z"]})
    counts = backfill_repo_reopens(client, catalog, FULL_NAME, apply=False)
    assert counts["reopens_found"] == 1 and counts["rows_written"] == 0
    assert _reopen_rows() == []


@requires_db
def test_rerun_is_idempotent(engine, catalog):
    with session_scope() as s:
        _seed(s, [1])
    client = _FakeGraphQLClient({1: ["2026-08-01T00:00:00Z"]})
    backfill_repo_reopens(client, catalog, FULL_NAME, apply=True)
    second = backfill_repo_reopens(client, catalog, FULL_NAME, apply=True)
    assert second["rows_written"] == 0          # source_key conflict -> no dup
    assert len(_reopen_rows()) == 1
