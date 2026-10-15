"""Issue ingestion end-to-end through `apply.py` (ISSUE plan H1): an `issues` webhook lands a
`kind="issue"` `git_artifacts` row reusing the PR pipeline unchanged, an `issue_comment` on a
plain issue bumps that issue's `comment_count`, and replaying either is idempotent.

DB-backed; skips without `TALIUM_TEST_DSN` (mapper-only assertions live in `test_map_github.py`).
"""
from __future__ import annotations

import json

from sqlalchemy import text

from server.ingest.git.apply import apply_map_result
from server.ingest.git.backfill import _graphql_issue_to_result, _graphql_pr_to_result
from server.ingest.git.map_github import map_delivery
from server.storage.engine import session_scope
from server.storage.schema import Base
from tests.server.storage.conftest import requires_db

ORG_ID = "org_test"
REPO_NUM = 41213765


def _issue_payload(number=318, state="open", comments=0, title="Async auth middleware",
                   author="akhuteta", closed_at=None, updated_at="2026-09-01T10:00:00Z"):
    return {
        "issue": {
            "number": number, "title": title, "state": state,
            "user": {"login": author},
            "labels": [{"name": "auth"}, {"name": "perf"}],
            "milestone": {"title": "Sprint 21"},
            "assignees": [{"login": author}],
            "comments": comments,
            "created_at": "2026-09-01T10:00:00Z", "updated_at": updated_at, "closed_at": closed_at,
        },
        "repository": {"id": REPO_NUM},
    }


@requires_db
def test_issues_webhook_lands_a_titled_statused_issue_artifact(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    result = map_delivery("issues", "opened", _issue_payload(), ORG_ID)
    with session_scope() as s:
        apply_map_result(s, catalog, result)

    with session_scope() as s:
        row = s.execute(text(
            "SELECT kind, state, title, labels, milestone, stats, info, user_id, "
            "sha, merge_commit_sha, files, commit_shas "
            "FROM git_artifacts WHERE artifact_id = :id"
        ), {"id": f"github:{REPO_NUM}:issue:318"}).mappings().one()
    assert row["kind"] == "issue"
    assert row["state"] == "open"
    assert row["title"] == "Async auth middleware"
    assert row["labels"] == ["auth", "perf"]
    assert row["milestone"] == "Sprint 21"
    assert row["stats"]["comment_count"] == 0
    assert row["info"]["github.assignees"] == ["akhuteta"]
    # The author resolves through the same identity path a PR uses.
    assert row["user_id"] == "github:akhuteta"
    # PR-only columns are null / empty on an issue row.
    assert row["sha"] is None and row["merge_commit_sha"] is None
    assert row["files"] == [] and row["commit_shas"] == []


@requires_db
def test_issue_comment_on_a_plain_issue_bumps_that_issues_comment_count(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        apply_map_result(s, catalog, map_delivery("issues", "opened", _issue_payload(), ORG_ID))

    comment = {
        "action": "created", "issue": {"number": 318},
        "repository": {"id": REPO_NUM}, "comment": {"id": 990101},
    }
    with session_scope() as s:
        apply_map_result(s, catalog, map_delivery("issue_comment", "created", comment, ORG_ID))

    with session_scope() as s:
        stats = s.execute(text("SELECT stats FROM git_artifacts WHERE artifact_id = :id"),
                          {"id": f"github:{REPO_NUM}:issue:318"}).scalar_one()
    assert stats["comment_count"] == 1

    # Replaying the same comment delivery is a no-op (dedup on comment_id:action).
    with session_scope() as s:
        apply_map_result(s, catalog, map_delivery("issue_comment", "created", comment, ORG_ID))
    with session_scope() as s:
        stats = s.execute(text("SELECT stats FROM git_artifacts WHERE artifact_id = :id"),
                          {"id": f"github:{REPO_NUM}:issue:318"}).scalar_one()
    assert stats["comment_count"] == 1


@requires_db
def test_replaying_an_issues_delivery_lands_identical_state(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    def snapshot() -> list[dict]:
        with session_scope() as s:
            return [dict(r) for r in s.execute(text(
                "SELECT artifact_id, kind, state, title, closed_at, stats, labels, info "
                "FROM git_artifacts WHERE repo_id = :r ORDER BY artifact_id"
            ), {"r": f"github:{REPO_NUM}"}).mappings().all()]

    for _ in range(2):
        with session_scope() as s:
            apply_map_result(s, catalog, map_delivery("issues", "opened", _issue_payload(), ORG_ID))
    opened_state = snapshot()
    assert len(opened_state) == 1

    # A later `closed` event (newer updated_at) wins; replaying it changes nothing.
    closed_payload = _issue_payload(state="closed", comments=4,
                                    closed_at="2026-09-04T16:30:00Z",
                                    updated_at="2026-09-04T16:30:00Z")
    for _ in range(2):
        with session_scope() as s:
            apply_map_result(s, catalog, map_delivery("issues", "closed", closed_payload, ORG_ID))
    closed_state = snapshot()
    assert len(closed_state) == 1
    assert closed_state[0]["state"] == "closed"
    assert json.loads(json.dumps(closed_state, default=str)) == json.loads(json.dumps(snapshot(), default=str))


@requires_db
def test_backfilled_issue_then_newer_webhook_wins_and_comment_bump_survives(engine, catalog):
    """A GraphQL-backfilled issue lands, then a newer `issues` webhook (last-writer-wins on
    `updated_at`) and an `issue_comment` bump both compose onto the same row (ISSUE H2)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    node = {
        "number": 318, "title": "Async auth middleware", "state": "OPEN",
        "createdAt": "2026-09-01T10:00:00Z", "updatedAt": "2026-09-01T10:00:00Z", "closedAt": None,
        "author": {"login": "akhuteta"},
        "labels": {"nodes": [{"name": "auth"}]}, "milestone": {"title": "Sprint 21"},
        "assignees": {"nodes": [{"login": "akhuteta"}]}, "comments": {"totalCount": 0},
    }
    with session_scope() as s:
        apply_map_result(s, catalog, _graphql_issue_to_result(node, REPO_NUM, ORG_ID))

    # A comment lands against the backfilled row.
    with session_scope() as s:
        apply_map_result(s, catalog, map_delivery("issue_comment", "created", {
            "action": "created", "issue": {"number": 318},
            "repository": {"id": REPO_NUM}, "comment": {"id": 7},
        }, ORG_ID))

    # A newer webhook `edited` closes it; `updated_at` is newer so it wins on title/state.
    with session_scope() as s:
        apply_map_result(s, catalog, map_delivery("issues", "closed",
            _issue_payload(state="closed", comments=1, closed_at="2026-09-05T00:00:00Z",
                           updated_at="2026-09-05T00:00:00Z"), ORG_ID))

    with session_scope() as s:
        row = s.execute(text(
            "SELECT state, closed_at, stats FROM git_artifacts WHERE artifact_id = :id"
        ), {"id": f"github:{REPO_NUM}:issue:318"}).mappings().one()
    assert row["state"] == "closed"
    assert row["closed_at"] is not None
    # `stats` merges (||): the webhook re-supplies comment_count=1, consistent with the bump.
    assert row["stats"]["comment_count"] == 1


# ---------------------------------------------------- PR <-> issue linkage (ISSUE H3)

def _graphql_pr_node(number=502, *, closing=(318,), body="", branch="feature/x", title="Add X",
                     commit_msgs=("work",)):
    return {
        "number": number, "title": title, "body": body,
        "state": "MERGED", "merged": True, "isDraft": False,
        "createdAt": "2026-09-02T09:00:00Z", "updatedAt": "2026-09-03T10:00:00Z",
        "closedAt": "2026-09-03T10:00:00Z", "mergedAt": "2026-09-03T10:00:00Z",
        "additions": 5, "deletions": 1, "changedFiles": 1,
        "author": {"login": "akhuteta"}, "mergedBy": {"login": "akhuteta"},
        "mergeCommit": {"oid": "m" * 40},
        "headRefOid": "h" * 40, "headRefName": branch, "baseRefName": "main",
        "labels": {"nodes": []}, "milestone": None,
        "closingIssuesReferences": {"nodes": [{"number": n} for n in closing]},
        "comments": {"totalCount": 0},
        "reviews": {"totalCount": 0, "nodes": []},
        "commits": {"totalCount": len(commit_msgs),
                    "nodes": [{"commit": {"oid": "c" * 40, "message": m}} for m in commit_msgs]},
        "files": {"nodes": []},
    }


@requires_db
def test_graphql_closing_reference_and_body_closes_resolve_to_one_link_at_1(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    node = _graphql_pr_node(closing=(318,), body="Closes #318")
    with session_scope() as s:
        apply_map_result(s, catalog, _graphql_pr_to_result(node, REPO_NUM, ORG_ID))

    with session_scope() as s:
        rows = [dict(r) for r in s.execute(text(
            "SELECT tracker, project_key, number, method, confidence, issue_artifact_id "
            "FROM pr_issue_links WHERE pr_artifact_id = :id"
        ), {"id": f"github:{REPO_NUM}:pr:502"}).mappings().all()]
    assert len(rows) == 1
    assert rows[0]["method"] == "graphql" and float(rows[0]["confidence"]) == 1.0
    assert rows[0]["number"] == 318
    assert rows[0]["issue_artifact_id"] == f"github:{REPO_NUM}:issue:318"


@requires_db
def test_a_weaker_webhook_resolution_does_not_downgrade_a_graphql_link(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # Backfill establishes a graphql(1.0) link to #318.
    with session_scope() as s:
        apply_map_result(s, catalog, _graphql_pr_to_result(
            _graphql_pr_node(closing=(318,), branch="feature/x"), REPO_NUM, ORG_ID))

    # A later webhook `synchronize` for the same PR -- no closingIssuesReferences visible, only
    # a branch that happens to name #999.
    webhook = {
        "pull_request": {
            "number": 502, "title": "Add X", "body": None, "state": "open", "merged": False,
            "user": {"login": "akhuteta"},
            "head": {"ref": "999-something", "sha": "h" * 40}, "base": {"ref": "main"},
            "created_at": "2026-09-02T09:00:00Z", "updated_at": "2026-09-04T10:00:00Z",
            "labels": [],
        },
        "repository": {"id": REPO_NUM},
    }
    with session_scope() as s:
        apply_map_result(s, catalog, map_delivery("pull_request", "synchronize", webhook, ORG_ID))

    with session_scope() as s:
        rows = [dict(r) for r in s.execute(text(
            "SELECT number, method, confidence FROM pr_issue_links WHERE pr_artifact_id = :id "
            "ORDER BY number"
        ), {"id": f"github:{REPO_NUM}:pr:502"}).mappings().all()]
    # The strong graphql link to #318 survives; the weak branch link to #999 is also recorded.
    assert {r["number"]: r["method"] for r in rows} == {318: "graphql", 999: "branch"}


@requires_db
def test_link_recorded_before_its_issue_lands_gets_the_issue_id_attached_on_arrival(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # A PR body links #318 (body rung) before issue #318 has been ingested.
    pr = {
        "pull_request": {
            "number": 502, "title": "Add X", "body": "Closes #318", "state": "open",
            "merged": False, "user": {"login": "akhuteta"},
            "head": {"ref": "x", "sha": "h" * 40}, "base": {"ref": "main"},
            "created_at": "2026-09-02T09:00:00Z", "updated_at": "2026-09-02T09:00:00Z", "labels": [],
        },
        "repository": {"id": REPO_NUM},
    }
    with session_scope() as s:
        apply_map_result(s, catalog, map_delivery("pull_request", "opened", pr, ORG_ID))
    with session_scope() as s:
        aid = s.execute(text(
            "SELECT issue_artifact_id FROM pr_issue_links WHERE pr_artifact_id = :id"
        ), {"id": f"github:{REPO_NUM}:pr:502"}).scalar_one()
    assert aid == f"github:{REPO_NUM}:issue:318"   # github refs resolve their id at parse time

    # Now the issue itself lands -- the NULL-guarded attach is a no-op here but must not error.
    with session_scope() as s:
        apply_map_result(s, catalog, map_delivery("issues", "opened", _issue_payload(number=318), ORG_ID))
    with session_scope() as s:
        aid = s.execute(text(
            "SELECT issue_artifact_id FROM pr_issue_links WHERE pr_artifact_id = :id"
        ), {"id": f"github:{REPO_NUM}:pr:502"}).scalar_one()
    assert aid == f"github:{REPO_NUM}:issue:318"
