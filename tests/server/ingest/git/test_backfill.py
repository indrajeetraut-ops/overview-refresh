"""Backfill/gap-fill readers (git-source.md §8, §11's test bar): GraphQL paging, the REST list
pass stopping at the cursor, non-empty GraphQL `errors[]` never mapping partial data, and --
the load-bearing one -- a GraphQL-backfilled and a REST-backfilled artifact for the SAME PR
being byte-identical, proven from two independently-built fixtures representing one PR through
each wire shape."""
from __future__ import annotations

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from server.ingest.git.backfill import GraphQLBackfillReader, RestBackfillReader
from server.ingest.git.client import GitHubApiError, GitHubClient
from server.ingest.git.config import GithubConfig

TOKEN_URL = "https://api.github.com/app/installations/42/access_tokens"
GRAPHQL_URL = "https://api.github.com/graphql"
ORG_ID = "org_test"
REPO_NUM = 41213765


@pytest.fixture
def private_key_path(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "key.pem"
    path.write_bytes(pem)
    return str(path)


@pytest.fixture
def client(private_key_path):
    config = GithubConfig(client_id="Iv1.abc123", private_key_path=private_key_path)
    return GitHubClient(config)


def _token_route(mock):
    return mock.post(TOKEN_URL).mock(return_value=httpx.Response(201, json={"token": "tok"}))


def _rl(cost=1, remaining=4999):
    return {"cost": cost, "remaining": remaining, "nodeCount": 1, "resetAt": "2099-01-01T00:00:00Z"}


# ------------------------------------------------------------------------------- GraphQL paging

def test_graphql_reader_pages_across_two_responses(client):
    page1 = {
        "data": {
            "rateLimit": _rl(),
            "search": {
                "issueCount": 2,
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                "nodes": [{
                    "number": 1, "title": "First", "body": None, "state": "OPEN", "merged": False,
                    "isDraft": False, "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
                    "closedAt": None, "mergedAt": None, "additions": 1, "deletions": 1, "changedFiles": 1,
                    "author": {"login": "u1"}, "mergedBy": None, "mergeCommit": None,
                    "headRefOid": "a" * 40, "headRefName": "b1", "baseRefName": "main",
                    "labels": {"nodes": []}, "milestone": None, "comments": {"totalCount": 0},
                    "reviews": {"totalCount": 0, "nodes": []}, "commits": {"totalCount": 1, "nodes": []},
                    "files": {"nodes": []},
                }],
            },
        }
    }
    page2 = {
        "data": {
            "rateLimit": _rl(),
            "search": {
                "issueCount": 2,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{
                    "number": 2, "title": "Second", "body": None, "state": "OPEN", "merged": False,
                    "isDraft": False, "createdAt": "2026-01-02T00:00:00Z", "updatedAt": "2026-01-02T00:00:00Z",
                    "closedAt": None, "mergedAt": None, "additions": 2, "deletions": 2, "changedFiles": 2,
                    "author": {"login": "u2"}, "mergedBy": None, "mergeCommit": None,
                    "headRefOid": "b" * 40, "headRefName": "b2", "baseRefName": "main",
                    "labels": {"nodes": []}, "milestone": None, "comments": {"totalCount": 0},
                    "reviews": {"totalCount": 0, "nodes": []}, "commits": {"totalCount": 1, "nodes": []},
                    "files": {"nodes": []},
                }],
            },
        }
    }
    with respx.mock(assert_all_called=False) as mock:
        _token_route(mock)
        mock.post(GRAPHQL_URL).mock(side_effect=[
            httpx.Response(200, json=page1), httpx.Response(200, json=page2),
        ])
        reader = GraphQLBackfillReader(client)
        from datetime import datetime, timezone
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)

        p1 = reader.read_page(42, "o", "n", REPO_NUM, ORG_ID, since)
        assert p1.has_next_page is True
        assert p1.next_cursor == "cursor-1"
        assert [r.artifacts[0]["number"] for r in p1.results] == [1]

        p2 = reader.read_page(42, "o", "n", REPO_NUM, ORG_ID, since, cursor=p1.next_cursor)
        assert p2.has_next_page is False
        assert [r.artifacts[0]["number"] for r in p2.results] == [2]


def test_graphql_reader_never_maps_partial_data_on_errors(client):
    with respx.mock(assert_all_called=False) as mock:
        _token_route(mock)
        mock.post(GRAPHQL_URL).mock(return_value=httpx.Response(200, json={
            "data": None, "errors": [{"message": "something timed out"}],
        }))
        reader = GraphQLBackfillReader(client)
        from datetime import datetime, timezone
        with pytest.raises(GitHubApiError):
            reader.read_page(42, "o", "n", REPO_NUM, ORG_ID, datetime(2026, 1, 1, tzinfo=timezone.utc))


# ---------------------------------------------------------------------------------- REST paging

def test_rest_reader_list_stops_once_page_is_older_than_cursor(client):
    from datetime import datetime, timezone
    cursor = datetime(2026, 6, 1, tzinfo=timezone.utc)
    newer_page = [
        {"number": 10, "updated_at": "2026-07-01T00:00:00Z"},
        {"number": 9, "updated_at": "2026-06-15T00:00:00Z"},
    ]
    older_page = [
        {"number": 8, "updated_at": "2026-05-01T00:00:00Z"},   # older than cursor -> stop, excluded
    ]
    with respx.mock(assert_all_called=False) as mock:
        _token_route(mock)
        route = mock.get("https://api.github.com/repos/o/n/pulls").mock(side_effect=[
            httpx.Response(200, json=newer_page), httpx.Response(200, json=older_page),
        ])
        reader = RestBackfillReader(client)
        numbers = reader.list_numbers_since(42, "o", "n", cursor, per_page=2)
    assert numbers == [10, 9]
    assert route.call_count == 2


# ------------------------------------------------------------------------- reader agreement (§8.2)

_MERGE_SHA = "m" * 40
_HEAD_SHA = "h" * 40


def _graphql_node_for_shared_pr() -> dict:
    return {
        "number": 500, "title": "Fix flaky test",
        "body": "Fixes the flaky retry test. Closes #10.",
        "state": "CLOSED", "merged": True, "isDraft": False,
        "createdAt": "2026-08-01T09:00:00Z", "updatedAt": "2026-08-02T10:00:00Z",
        "closedAt": "2026-08-02T10:00:00Z", "mergedAt": "2026-08-02T10:00:00Z",
        "additions": 10, "deletions": 2, "changedFiles": 1,
        "author": {"login": "akhuteta"}, "mergedBy": {"login": "akhuteta"},
        "mergeCommit": {"oid": _MERGE_SHA},
        "headRefOid": _HEAD_SHA, "headRefName": "fix-flaky", "baseRefName": "main",
        "labels": {"nodes": [{"name": "bug"}]}, "milestone": None,
        "comments": {"totalCount": 0},
        "reviews": {"totalCount": 1, "nodes": [
            {"databaseId": 7001, "state": "APPROVED", "submittedAt": "2026-08-02T09:00:00Z",
             "commit": {"oid": _HEAD_SHA}, "author": {"login": "reviewer1"}},
        ]},
        "commits": {"totalCount": 1, "nodes": [
            {"commit": {"oid": _MERGE_SHA, "message": "Fix flaky test (#500)"}},
        ]},
        "files": {"nodes": [{"path": "src/y.py", "additions": 10, "deletions": 2, "changeType": "MODIFIED"}]},
    }


def _rest_fixtures_for_shared_pr() -> dict:
    pr = {
        "number": 500, "title": "Fix flaky test",
        "body": "Fixes the flaky retry test. Closes #10.",
        "state": "closed", "merged": True, "draft": False,
        "user": {"login": "akhuteta"}, "merged_by": {"login": "akhuteta"},
        "head": {"ref": "fix-flaky", "sha": _HEAD_SHA}, "base": {"ref": "main"},
        "created_at": "2026-08-01T09:00:00Z", "updated_at": "2026-08-02T10:00:00Z",
        "closed_at": "2026-08-02T10:00:00Z", "merged_at": "2026-08-02T10:00:00Z",
        "merge_commit_sha": _MERGE_SHA,
        "additions": 10, "deletions": 2, "changed_files": 1, "commits": 1,
        "labels": [{"name": "bug"}], "milestone": None,
        "comments": 0, "review_comments": 0,
    }
    files = [{"filename": "src/y.py", "status": "modified", "additions": 10, "deletions": 2}]
    commits = [{"sha": _MERGE_SHA, "commit": {"message": "Fix flaky test (#500)"}}]
    reviews = [{"id": 7001, "user": {"login": "reviewer1"}, "state": "approved",
               "submitted_at": "2026-08-02T09:00:00Z", "commit_id": _HEAD_SHA}]
    return {"pr": pr, "files": files, "commits": commits, "reviews": reviews}


def test_graphql_and_rest_backfilled_artifacts_for_one_pr_are_byte_identical(client):
    from server.ingest.git.backfill import _graphql_pr_to_result
    graphql_result = _graphql_pr_to_result(_graphql_node_for_shared_pr(), REPO_NUM, ORG_ID)

    fx = _rest_fixtures_for_shared_pr()
    with respx.mock(assert_all_called=False) as mock:
        _token_route(mock)
        mock.get("https://api.github.com/repos/o/n/pulls/500").mock(return_value=httpx.Response(200, json=fx["pr"]))
        mock.get("https://api.github.com/repos/o/n/pulls/500/files").mock(return_value=httpx.Response(200, json=fx["files"]))
        mock.get("https://api.github.com/repos/o/n/pulls/500/commits").mock(return_value=httpx.Response(200, json=fx["commits"]))
        mock.get("https://api.github.com/repos/o/n/pulls/500/reviews").mock(return_value=httpx.Response(200, json=fx["reviews"]))
        rest_result = RestBackfillReader(client).read_pr(42, "o", "n", REPO_NUM, ORG_ID, 500)

    assert graphql_result.artifacts[0] == rest_result.artifacts[0]
    assert graphql_result.artifacts[1] == rest_result.artifacts[1]
    assert len(graphql_result.artifacts) == len(rest_result.artifacts) == 2


# ---------------------------------------------------------------- issue backfill (ISSUE H2)

def _graphql_issue_page(nodes, has_next=False, cursor=None):
    return {"data": {"rateLimit": _rl(), "search": {
        "issueCount": len(nodes), "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        "nodes": nodes,
    }}}


def _graphql_issue_node() -> dict:
    return {
        "number": 318, "title": "Async auth middleware", "state": "CLOSED",
        "createdAt": "2026-09-01T10:00:00Z", "updatedAt": "2026-09-04T16:30:00Z",
        "closedAt": "2026-09-04T16:30:00Z",
        "author": {"login": "akhuteta"},
        "labels": {"nodes": [{"name": "auth"}, {"name": "perf"}]},
        "milestone": {"title": "Sprint 21"},
        "assignees": {"nodes": [{"login": "akhuteta"}]},
        "comments": {"totalCount": 4},
    }


def _rest_issue_json() -> dict:
    return {
        "number": 318, "title": "Async auth middleware", "state": "closed",
        "user": {"login": "akhuteta"},
        "labels": [{"name": "auth"}, {"name": "perf"}],
        "milestone": {"title": "Sprint 21"},
        "assignees": [{"login": "akhuteta"}],
        "comments": 4,
        "created_at": "2026-09-01T10:00:00Z", "updated_at": "2026-09-04T16:30:00Z",
        "closed_at": "2026-09-04T16:30:00Z",
    }


def test_graphql_issue_reader_pages_and_maps_issue_artifacts(client):
    from datetime import datetime, timezone
    page1 = _graphql_issue_page([{**_graphql_issue_node(), "number": 1, "updatedAt": "2026-09-01T00:00:00Z"}],
                                has_next=True, cursor="c1")
    page2 = _graphql_issue_page([{**_graphql_issue_node(), "number": 2, "updatedAt": "2026-09-02T00:00:00Z"}])
    with respx.mock(assert_all_called=False) as mock:
        _token_route(mock)
        mock.post(GRAPHQL_URL).mock(side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)])
        reader = GraphQLBackfillReader(client)
        since = datetime(2026, 9, 1, tzinfo=timezone.utc)
        p1 = reader.read_issue_page(42, "o", "n", REPO_NUM, ORG_ID, since)
        assert p1.has_next_page is True and p1.next_cursor == "c1"
        assert [r.artifacts[0]["kind"] for r in p1.results] == ["issue"]
        assert p1.results[0].artifacts[0]["artifact_id"] == f"github:{REPO_NUM}:issue:1"
        p2 = reader.read_issue_page(42, "o", "n", REPO_NUM, ORG_ID, since, cursor="c1")
        assert p2.has_next_page is False
        assert [r.artifacts[0]["number"] for r in p2.results] == [2]


def test_rest_issue_list_excludes_pull_requests(client):
    from datetime import datetime, timezone
    cursor = datetime(2026, 9, 1, tzinfo=timezone.utc)
    page = [
        {"number": 320, "updated_at": "2026-09-05T00:00:00Z", "pull_request": {"url": "..."}},  # a PR: skip
        {"number": 318, "updated_at": "2026-09-04T00:00:00Z"},                                    # an issue
    ]
    with respx.mock(assert_all_called=False) as mock:
        _token_route(mock)
        mock.get("https://api.github.com/repos/o/n/issues").mock(side_effect=[
            httpx.Response(200, json=page), httpx.Response(200, json=[]),
        ])
        numbers = RestBackfillReader(client).list_issue_numbers_since(42, "o", "n", cursor, per_page=2)
    assert numbers == [318]


def test_graphql_and_rest_backfilled_issue_for_one_number_are_byte_identical(client):
    from server.ingest.git.backfill import _graphql_issue_to_result
    graphql_result = _graphql_issue_to_result(_graphql_issue_node(), REPO_NUM, ORG_ID)
    with respx.mock(assert_all_called=False) as mock:
        _token_route(mock)
        mock.get("https://api.github.com/repos/o/n/issues/318").mock(
            return_value=httpx.Response(200, json=_rest_issue_json()))
        rest_result = RestBackfillReader(client).read_issue(42, "o", "n", REPO_NUM, ORG_ID, 318)
    assert graphql_result.artifacts == rest_result.artifacts
    assert len(graphql_result.artifacts) == 1
    assert graphql_result.artifacts[0]["state"] == "closed"   # GraphQL's CLOSED normalized


def test_webhook_graphql_rest_issue_mappings_all_agree(client):
    """The load-bearing reader-agreement assertion (ISSUE §0.2, H2 "Done when"): one issue
    through all three wire shapes maps byte-identical."""
    from server.ingest.git.backfill import _graphql_issue_to_result
    from server.ingest.git.map_github import map_delivery

    webhook = map_delivery("issues", "edited", {
        "issue": _rest_issue_json(), "repository": {"id": REPO_NUM},
    }, ORG_ID)
    graphql = _graphql_issue_to_result(_graphql_issue_node(), REPO_NUM, ORG_ID)
    with respx.mock(assert_all_called=False) as mock:
        _token_route(mock)
        mock.get("https://api.github.com/repos/o/n/issues/318").mock(
            return_value=httpx.Response(200, json=_rest_issue_json()))
        rest = RestBackfillReader(client).read_issue(42, "o", "n", REPO_NUM, ORG_ID, 318)

    assert webhook.artifacts == graphql.artifacts == rest.artifacts
