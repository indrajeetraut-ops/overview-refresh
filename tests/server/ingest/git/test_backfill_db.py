"""Backfill DB-integration tests (git-source.md §11's test bar): cursor resume mid-run maps
each PR exactly once, and a backfill row landing over an already-webhook-mapped PR leaves state
identical rather than corrupting or duplicating it."""
from __future__ import annotations

import httpx
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select

from server.ingest.git.apply import apply_map_result
from server.ingest.git.backfill import GraphQLBackfillReader, RestBackfillReader
from server.ingest.git.client import GitHubClient
from server.ingest.git.config import GithubConfig
from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog
from server.storage.schema import Base, GitArtifact
from tests.server.storage.conftest import requires_db

TOKEN_URL = "https://api.github.com/app/installations/42/access_tokens"
GRAPHQL_URL = "https://api.github.com/graphql"
ORG_ID = "org_test"
REPO_NUM = 999001


def _key(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "key.pem"
    path.write_bytes(pem)
    return str(path)


def _pr_node(number: int, updated_at: str) -> dict:
    return {
        "number": number, "title": f"PR {number}", "body": None, "state": "OPEN", "merged": False,
        "isDraft": False, "createdAt": updated_at, "updatedAt": updated_at,
        "closedAt": None, "mergedAt": None, "additions": 1, "deletions": 1, "changedFiles": 1,
        "author": {"login": "u"}, "mergedBy": None, "mergeCommit": None,
        "headRefOid": f"{number:040x}", "headRefName": f"b{number}", "baseRefName": "main",
        "labels": {"nodes": []}, "milestone": None, "comments": {"totalCount": 0},
        "reviews": {"totalCount": 0, "nodes": []}, "commits": {"totalCount": 1, "nodes": []},
        "files": {"nodes": []},
    }


def _rl():
    return {"cost": 1, "remaining": 4999, "nodeCount": 1, "resetAt": "2099-01-01T00:00:00Z"}


@requires_db
def test_cursor_resume_mid_run_maps_each_pr_exactly_once(engine, tmp_path):
    from datetime import datetime, timezone
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load("config/queries")
    client = GitHubClient(GithubConfig(client_id="Iv1.abc", private_key_path=_key(tmp_path)))
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_repo"), {
            "repo_id": f"github:{REPO_NUM}", "repo_num": REPO_NUM, "full_name": "o/n", "owner": "o",
            "default_branch": "main", "private": False, "installation_id": 42, "org_id": ORG_ID,
            "info": "{}",
        })

    page1 = {"data": {"rateLimit": _rl(), "search": {
        "issueCount": 3, "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
        "nodes": [_pr_node(1, "2026-01-01T00:00:00Z"), _pr_node(2, "2026-01-02T00:00:00Z")],
    }}}
    page2 = {"data": {"rateLimit": _rl(), "search": {
        "issueCount": 3, "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [_pr_node(3, "2026-01-03T00:00:00Z")],
    }}}

    with respx.mock(assert_all_called=False) as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(201, json={"token": "tok"}))
        # 3 calls total: run 1's page 1, run 2's re-issued page 1 (resume re-queries from the
        # persisted cursor, which can legitimately overlap what run 1 already saw), run 2's page 2.
        mock.post(GRAPHQL_URL).mock(side_effect=[
            httpx.Response(200, json=page1), httpx.Response(200, json=page1), httpx.Response(200, json=page2),
        ])
        reader = GraphQLBackfillReader(client)

        # Run 1: only page 1 (simulates a run interrupted after the first page).
        p1 = reader.read_page(42, "o", "n", REPO_NUM, ORG_ID, since)
        with session_scope() as s:
            for result in p1.results:
                apply_map_result(s, catalog, result)
        with session_scope() as s:
            s.execute(catalog.get("git", "advance_repo_cursor"),
                     {"repo_id": f"github:{REPO_NUM}", "cursor": p1.newest_updated_at.isoformat(), "state": "listing"})

        # Run 2 "resumes": reads the persisted cursor, re-issues page 1 AGAIN (a real resume
        # would re-query from the persisted cursor, which can overlap what run 1 already saw --
        # exactly what upsert idempotency is for) plus page 2.
        with session_scope() as s:
            row = s.execute(catalog.get("git", "get_repo_cursor"), {"repo_id": f"github:{REPO_NUM}"}).first()
        resumed_since = row[0]
        p1_again = reader.read_page(42, "o", "n", REPO_NUM, ORG_ID, resumed_since)
        with session_scope() as s:
            for result in p1_again.results:
                apply_map_result(s, catalog, result)
        p2 = reader.read_page(42, "o", "n", REPO_NUM, ORG_ID, resumed_since, cursor=p1_again.next_cursor)
        with session_scope() as s:
            for result in p2.results:
                apply_map_result(s, catalog, result)

    with session_scope() as s:
        count = s.scalar(select(func.count()).select_from(GitArtifact).where(GitArtifact.kind == "pr"))
    assert count == 3   # PRs 1, 2, 3 -- each exactly once despite page 1 being re-processed


@requires_db
def test_backfill_over_webhook_row_leaves_core_state_identical(engine, tmp_path):
    """"Leaves state identical" (git-source.md §11) means backfill enriching a PR a webhook
    already created must not corrupt what the webhook established -- the identity/state fields
    both readers agree on stay byte-identical -- even though backfill legitimately ADDS fields
    (files, commit count, review artifacts) no webhook payload alone can supply. That addition
    is the point of backfill, not a violation of idempotency."""
    from datetime import datetime, timezone
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load("config/queries")
    client = GitHubClient(GithubConfig(client_id="Iv1.abc", private_key_path=_key(tmp_path)))

    # A webhook already mapped this PR (map_delivery directly, as git_map.py would).
    from server.ingest.git.map_github import map_delivery
    webhook_payload = {
        "pull_request": {
            "number": 42, "title": "Existing PR", "body": None, "state": "open", "merged": False,
            "user": {"login": "u"}, "head": {"ref": "b", "sha": "a" * 40}, "base": {"ref": "main"},
            "created_at": "2026-03-01T00:00:00Z", "updated_at": "2026-03-01T00:00:00Z",
            "additions": 5, "deletions": 1, "changed_files": 2, "labels": [],
        },
        "repository": {"id": REPO_NUM},
    }
    webhook_result = map_delivery("pull_request", "opened", webhook_payload, ORG_ID)
    with session_scope() as s:
        apply_map_result(s, catalog, webhook_result)
    with session_scope() as s:
        before = dict(vars(s.get(GitArtifact, f"github:{REPO_NUM}:pr:42")))
        before.pop("_sa_instance_state", None)

    # Now a backfill sweep re-reads the SAME PR (equal updated_at -- a plain re-sweep, not a
    # newer change) via REST.
    with respx.mock(assert_all_called=False) as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(201, json={"token": "tok"}))
        mock.get(f"https://api.github.com/repos/o/n/pulls/42").mock(return_value=httpx.Response(200, json={
            "number": 42, "title": "Existing PR", "body": None, "state": "open", "merged": False,
            "draft": False, "user": {"login": "u"}, "head": {"ref": "b", "sha": "a" * 40}, "base": {"ref": "main"},
            "created_at": "2026-03-01T00:00:00Z", "updated_at": "2026-03-01T00:00:00Z",
            "additions": 5, "deletions": 1, "changed_files": 2, "commits": 1, "labels": [], "milestone": None,
        }))
        mock.get(f"https://api.github.com/repos/o/n/pulls/42/files").mock(return_value=httpx.Response(200, json=[]))
        mock.get(f"https://api.github.com/repos/o/n/pulls/42/commits").mock(return_value=httpx.Response(200, json=[]))
        mock.get(f"https://api.github.com/repos/o/n/pulls/42/reviews").mock(return_value=httpx.Response(200, json=[]))
        backfill_result = RestBackfillReader(client).read_pr(42, "o", "n", REPO_NUM, ORG_ID, 42)
    with session_scope() as s:
        apply_map_result(s, catalog, backfill_result)

    with session_scope() as s:
        after = dict(vars(s.get(GitArtifact, f"github:{REPO_NUM}:pr:42")))
        after.pop("_sa_instance_state", None)

    # Core identity/state fields: untouched by backfill's enrichment.
    identity_fields = [
        "artifact_id", "kind", "repo_id", "org_id", "number", "sha", "author_login",
        "is_bot", "state", "title", "branch", "base_branch", "created_at", "updated_at",
    ]
    for field in identity_fields:
        assert before[field] == after[field], field
    # The core stats webhook already knew: unchanged by backfill's enrichment.
    for field in ("additions", "deletions", "changed_files"):
        assert before["stats"][field] == after["stats"][field], field
    # What ONLY backfill can supply: correctly added, not left at the webhook's None/absent.
    assert after["stats"]["commits"] == 1
    assert after["stats"]["review_count"] == 0
