"""Backfill and gap-fill readers (git-source.md §8): the GraphQL bulk-history reader and the
REST list/detail gap-fill reader, both producing the identical `MapResult` shape
`map_github.py`'s webhook path does, both applied through the same `apply.py` write path (§8.2:
"one mapper, three readers, so they cannot drift"). Bypasses `git_deliveries`/`queue_jobs`
entirely (§4.2) -- there is no delivery to land (an API response is re-fetchable by
definition) and no dispatch to trigger; `cli_backfill`/the gap-fill timer call these readers
directly, in-process.

**Reader agreement, by construction, not by convention.** The REST reader does not re-implement
PR field extraction: a REST PR-detail response has the same shape as a webhook's `pull_request`
object, so it is handed to `map_github.map_delivery` directly and only *augmented* with what
REST's separate files/commits/reviews endpoints add. The GraphQL reader cannot do that (its
wire shape is structurally different), so it shares the same lower-level helpers
(`_artifact_id`, `_is_bot`, `_parse_reverts_sha`, `_parse_linked_issue`,
`_infer_integration_method`) instead -- the two readers converge on the same artifact fields
from two different starting points, which is what the reader-agreement test actually proves.

**What backfill does NOT get that a webhook does:** `before_sha`/`after_sha` (§5.7) come only
from the base-branch push GitHub fires at merge time; a bulk historic read has no such push to
read. A backfilled merged PR's transition fields stay `None` here -- the live webhook path
fills them in once traffic resumes, and `upsert_artifact`'s COALESCE (git-source.md
implementation plan A4) means a later webhook can supply them without a backfill re-run losing
anything already correlated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import text

from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog

from .apply import apply_map_result
from .client import GitHubApiError, GitHubClient, load_graphql
from .map_github import (
    MapResult,
    _artifact_id,
    _infer_integration_method,
    _is_bot,
    _is_revert_title,
    _parse_linked_issue,
    _parse_reverts_sha,
    _repo_id,
    issue_artifact,
    map_delivery,
    pr_issue_links,
)

log = structlog.get_logger(module="ingest.git.backfill")

_CHANGE_TYPE_TO_OP = {
    "ADDED": "added", "DELETED": "removed", "MODIFIED": "modified",
    "RENAMED": "renamed", "COPIED": "modified", "CHANGED": "modified",
}
_REST_STATUS_TO_OP = {
    "added": "added", "removed": "removed", "modified": "modified",
    "renamed": "renamed", "copied": "modified", "changed": "modified",
}


def _parse_iso(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class BackfillPage:
    results: list[MapResult]
    has_next_page: bool
    next_cursor: str | None
    newest_updated_at: datetime | None   # None only when the page found nothing


# ------------------------------------------------------------------- GraphQL bulk reader (§8.2)

def _graphql_pr_to_result(node: dict, repo_num: int, org_id: str) -> MapResult:
    number = node["number"]
    merged = bool(node.get("merged"))
    state = "merged" if merged else (node.get("state") or "OPEN").lower()
    author = node.get("author") or {}
    merged_by = node.get("mergedBy") or {}
    title, body = node.get("title"), node.get("body")
    is_revert = _is_revert_title(title)
    merge_commit_sha = (node.get("mergeCommit") or {}).get("oid") if merged else None

    commit_nodes = [c["commit"] for c in (node.get("commits") or {}).get("nodes", [])]
    commit_shas = [c["oid"] for c in commit_nodes]
    integration_method = None
    if merge_commit_sha:
        pseudo = [{"id": c["oid"], "message": c.get("message", "")} for c in commit_nodes]
        integration_method = _infer_integration_method(pseudo, merge_commit_sha)

    files = [
        {"path": f["path"], "op": _CHANGE_TYPE_TO_OP.get(f.get("changeType"), "modified"),
         "additions": f.get("additions"), "deletions": f.get("deletions")}
        for f in (node.get("files") or {}).get("nodes", [])
    ]

    info: dict[str, Any] = {"github.draft": bool(node.get("isDraft"))}
    if merged_by.get("login"):
        info["github.merged_by"] = merged_by["login"]
    if is_revert:
        info["github.is_revert"] = True

    artifact_id = _artifact_id(repo_num, "pr", number)
    pr_artifact = {
        "artifact_id": artifact_id, "kind": "pr", "repo_id": _repo_id(repo_num), "org_id": org_id,
        "number": number, "sha": node.get("headRefOid"),
        "author_login": author.get("login"), "author_email": None, "user_id": None,
        "is_bot": _is_bot(author.get("login")), "state": state, "title": title,
        "branch": node.get("headRefName"), "base_branch": node.get("baseRefName"),
        "created_at": node.get("createdAt"), "updated_at": node.get("updatedAt"),
        "closed_at": node.get("closedAt"), "merged_at": node.get("mergedAt"),
        "stats": {
            "additions": node.get("additions"), "deletions": node.get("deletions"),
            "changed_files": node.get("changedFiles"),
            "commits": (node.get("commits") or {}).get("totalCount"),
            "review_count": (node.get("reviews") or {}).get("totalCount"),
            "comment_count": (node.get("comments") or {}).get("totalCount"),
        },
        "files": files, "parent_artifact_id": None,
        "labels": [l["name"] for l in (node.get("labels") or {}).get("nodes", [])],
        "milestone": (node.get("milestone") or {}).get("title"),
        "linked_issue": _parse_linked_issue(repo_num, body),
        "merge_commit_sha": merge_commit_sha,
        "before_sha": None, "after_sha": None,   # no base-branch push to read here (§5.7, see module docstring)
        "integration_method": integration_method,
        "reverts_sha": _parse_reverts_sha(body) if is_revert else None,
        "commit_shas": commit_shas, "info": info,
    }

    artifacts = [pr_artifact]
    for r in (node.get("reviews") or {}).get("nodes", []):
        if r.get("databaseId") is None:
            continue
        r_author = r.get("author") or {}
        artifacts.append({
            "artifact_id": _artifact_id(repo_num, "review", r["databaseId"]),
            "kind": "review", "repo_id": _repo_id(repo_num), "org_id": org_id,
            "number": number, "sha": (r.get("commit") or {}).get("oid"),
            "author_login": r_author.get("login"), "author_email": None, "user_id": None,
            "is_bot": _is_bot(r_author.get("login")), "state": (r.get("state") or "").lower(),
            "title": None, "branch": None, "base_branch": None,
            "created_at": r.get("submittedAt"), "updated_at": r.get("submittedAt"),
            "closed_at": None, "merged_at": None,
            "stats": {}, "files": None, "parent_artifact_id": artifact_id,
            "labels": [], "milestone": None, "linked_issue": None,
            "merge_commit_sha": None, "before_sha": None, "after_sha": None,
            "integration_method": None, "reverts_sha": None, "commit_shas": None,
            "info": {},
        })

    links = pr_issue_links(
        artifact_id, repo_num,
        closing_issue_numbers=[r["number"] for r in (node.get("closingIssuesReferences") or {}).get("nodes", [])],
        commit_messages=[c.get("message", "") for c in commit_nodes],
        branch=node.get("headRefName"), title=title, body=body,
    )
    return MapResult(mapped=True, artifacts=artifacts, merge_commit_sha=merge_commit_sha,
                     issue_links=links)


def _graphql_issue_to_result(node: dict, repo_num: int, org_id: str) -> MapResult:
    """One GraphQL `Issue` node -> the same `issue` artifact the webhook path produces, built
    through the shared `issue_artifact` field-mapper (ISSUE §0.2 reader agreement). GraphQL
    reports `state` as `OPEN`/`CLOSED`; the webhook/REST shape is lower-case, so it is
    normalized here to keep the three byte-identical."""
    author = node.get("author") or {}
    state = (node.get("state") or "OPEN").lower()
    artifact = issue_artifact(
        repo_num, org_id,
        number=node["number"], state=state, title=node.get("title"),
        author_login=author.get("login"),
        labels=[l["name"] for l in (node.get("labels") or {}).get("nodes", [])],
        milestone=(node.get("milestone") or {}).get("title"),
        assignees=[a["login"] for a in (node.get("assignees") or {}).get("nodes", [])],
        parent_number=None,   # no parent/sub-issue field in the query (see backfill_issues.graphql)
        created_at=node.get("createdAt"), updated_at=node.get("updatedAt"),
        closed_at=node.get("closedAt"),
        comment_count=(node.get("comments") or {}).get("totalCount"),
    )
    return MapResult(mapped=True, artifacts=[artifact])


class GraphQLBackfillReader:
    """The bulk historic reader (§8.2): one page = one `search` round trip returning PRs with
    files/reviews/commits already attached, or -- via `read_issue_page` -- a page of issues
    with labels/milestone/assignees/comment-count attached. `errors[]` handling is the client's
    (`GitHubClient.graphql` already raises on any non-empty `errors[]`, §7.4) -- this reader
    never sees, and therefore never maps, a partial page."""

    def __init__(self, client: GitHubClient) -> None:
        self.client = client
        self._query = load_graphql("backfill_search")
        self._issue_query = load_graphql("backfill_issues")

    def read_page(self, installation_id: int, owner: str, name: str, repo_num: int,
                  org_id: str, since: datetime, cursor: str | None = None) -> BackfillPage:
        search_query = (
            f"repo:{owner}/{name} is:pr updated:>={since.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            "sort:updated-asc"
        )
        data = self.client.graphql(installation_id, self._query,
                                   {"query": search_query, "cursor": cursor})
        search = data["search"]
        nodes = search["nodes"]
        results = [_graphql_pr_to_result(n, repo_num, org_id) for n in nodes]
        newest = max((_parse_iso(n["updatedAt"]) for n in nodes), default=None)
        return BackfillPage(
            results=results, has_next_page=search["pageInfo"]["hasNextPage"],
            next_cursor=search["pageInfo"]["endCursor"], newest_updated_at=newest,
        )

    def read_issue_page(self, installation_id: int, owner: str, name: str, repo_num: int,
                        org_id: str, since: datetime, cursor: str | None = None) -> BackfillPage:
        """The issue equivalent of `read_page` (ISSUE H2): `is:issue` search, same date filter,
        same cursor, same page shape. `is:issue` already excludes PRs, so every node is an
        `Issue`."""
        search_query = (
            f"repo:{owner}/{name} is:issue updated:>={since.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            "sort:updated-asc"
        )
        data = self.client.graphql(installation_id, self._issue_query,
                                   {"query": search_query, "cursor": cursor})
        search = data["search"]
        nodes = search["nodes"]
        results = [_graphql_issue_to_result(n, repo_num, org_id) for n in nodes]
        newest = max((_parse_iso(n["updatedAt"]) for n in nodes), default=None)
        return BackfillPage(
            results=results, has_next_page=search["pageInfo"]["hasNextPage"],
            next_cursor=search["pageInfo"]["endCursor"], newest_updated_at=newest,
        )


# ------------------------------------------------------------- REST gap-fill / fallback (§8.2)

class RestBackfillReader:
    """The recurring gap-fill and GraphQL-fallback reader (§8.2). ETag-conditional (via
    `GitHubClient.rest_get`, A2): a repeat sweep of mostly-unchanged PRs answers `304` on every
    page, costing nothing against the primary limit. REST fails per-resource with an honest
    HTTP status, unlike GraphQL's partial-200 failure mode -- the reason it is the fallback when
    a GraphQL page times out or a huge PR's `files` connection truncates."""

    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    def list_numbers_since(self, installation_id: int, owner: str, name: str,
                           since: datetime, per_page: int = 100) -> list[int]:
        """Newest-first list pass, stopping once a page is entirely older than `since`."""
        numbers: list[int] = []
        page = 1
        while True:
            r = self.client.rest_get(
                installation_id, f"/repos/{owner}/{name}/pulls",
                params={"state": "all", "sort": "updated", "direction": "desc",
                        "per_page": per_page, "page": page},
            )
            if r.status_code == 304:
                break
            items = r.json()
            if not items:
                break
            stop = False
            for pr in items:
                if _parse_iso(pr["updated_at"]) < since:
                    stop = True
                    break
                numbers.append(pr["number"])
            if stop or len(items) < per_page:
                break
            page += 1
        return numbers

    def read_pr(self, installation_id: int, owner: str, name: str, repo_num: int,
               org_id: str, number: int) -> MapResult:
        """The detail pass for one PR: REST's PR-detail response has the SAME shape as a
        webhook's `pull_request` object, so the core fields go through `map_delivery` itself --
        the same function the webhook path calls -- and only the extra endpoints REST needs a
        separate round trip for (files, commits, reviews) are merged in after."""
        pr = self.client.rest_get(installation_id, f"/repos/{owner}/{name}/pulls/{number}").json()
        result = map_delivery(
            "pull_request", "synchronize", {"pull_request": pr, "repository": {"id": repo_num}}, org_id,
        )
        artifact = result.artifacts[0]

        files = self.client.rest_get(
            installation_id, f"/repos/{owner}/{name}/pulls/{number}/files", params={"per_page": 100},
        ).json()
        artifact["files"] = [
            {"path": f["filename"], "op": _REST_STATUS_TO_OP.get(f.get("status"), "modified"),
             "additions": f.get("additions"), "deletions": f.get("deletions")}
            for f in files
        ]

        commits = self.client.rest_get(
            installation_id, f"/repos/{owner}/{name}/pulls/{number}/commits", params={"per_page": 100},
        ).json()
        artifact["commit_shas"] = [c["sha"] for c in commits]
        if artifact["merge_commit_sha"]:
            pseudo = [{"id": c["sha"], "message": c["commit"]["message"]} for c in commits]
            artifact["integration_method"] = _infer_integration_method(pseudo, artifact["merge_commit_sha"])

        reviews = self.client.rest_get(
            installation_id, f"/repos/{owner}/{name}/pulls/{number}/reviews", params={"per_page": 100},
        ).json()
        artifact["stats"]["review_count"] = len(reviews)
        artifact["stats"]["comment_count"] = (pr.get("comments") or 0) + (pr.get("review_comments") or 0)
        for r in reviews:
            r_author = r.get("user") or {}
            result.artifacts.append({
                "artifact_id": _artifact_id(repo_num, "review", r["id"]),
                "kind": "review", "repo_id": _repo_id(repo_num), "org_id": org_id,
                "number": number, "sha": r.get("commit_id"),
                "author_login": r_author.get("login"), "author_email": None, "user_id": None,
                "is_bot": _is_bot(r_author.get("login")), "state": (r.get("state") or "").lower(),
                "title": None, "branch": None, "base_branch": None,
                "created_at": r.get("submitted_at"), "updated_at": r.get("submitted_at"),
                "closed_at": None, "merged_at": None,
                "stats": {}, "files": None, "parent_artifact_id": artifact["artifact_id"],
                "labels": [], "milestone": None, "linked_issue": None,
                "merge_commit_sha": None, "before_sha": None, "after_sha": None,
                "integration_method": None, "reverts_sha": None, "commit_shas": None,
                "info": {},
            })
        return result

    def list_issue_numbers_since(self, installation_id: int, owner: str, name: str,
                                 since: datetime, per_page: int = 100) -> list[int]:
        """Newest-first list pass over `/issues`, stopping once a page is entirely older than
        `since`. `/issues` returns PRs too (they carry a `pull_request` key) -- those are
        dropped here so only true issue numbers reach `read_issue` (ISSUE H2)."""
        numbers: list[int] = []
        page = 1
        while True:
            r = self.client.rest_get(
                installation_id, f"/repos/{owner}/{name}/issues",
                params={"state": "all", "sort": "updated", "direction": "desc",
                        "per_page": per_page, "page": page},
            )
            if r.status_code == 304:
                break
            items = r.json()
            if not items:
                break
            stop = False
            for issue in items:
                if _parse_iso(issue["updated_at"]) < since:
                    stop = True
                    break
                if "pull_request" in issue:
                    continue
                numbers.append(issue["number"])
            if stop or len(items) < per_page:
                break
            page += 1
        return numbers

    def read_issue(self, installation_id: int, owner: str, name: str, repo_num: int,
                   org_id: str, number: int) -> MapResult:
        """The detail pass for one issue: REST's issue response has the SAME shape as a
        webhook's `issue` object, so it goes straight through `map_delivery` -- the same
        function the webhook path calls (ISSUE §0.2 reader agreement, mirroring `read_pr`).
        Issues need no separate files/commits/reviews round trips."""
        issue = self.client.rest_get(
            installation_id, f"/repos/{owner}/{name}/issues/{number}"
        ).json()
        return map_delivery(
            "issues", "opened", {"issue": issue, "repository": {"id": repo_num}}, org_id,
        )


# ------------------------------------------------------------------ gap-fill timer (§8.2, §10)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class GapFillRunner:
    """Timer-driven, per-known-repo REST sweep -- the PRIMARY delivery-loss recovery path
    (§10: GitHub's own redelivery reaches only 3 days back; a gap-fill cursor has no such
    limit). Registered as a worker `Timer`, not a queue topic: there is no delivery to
    dispatch on, only a repeated "what changed since the cursor" sweep over every repo this
    installation covers, per `git_repos`."""

    def __init__(self, client: GitHubClient, catalog: QueryCatalog) -> None:
        self.client = client
        self.catalog = catalog
        self.reader = RestBackfillReader(client)

    def tick(self) -> int:
        with session_scope() as s:
            repos = s.execute(text(
                "SELECT repo_id, repo_num, full_name, installation_id, org_id, backfill_cursor "
                "FROM git_repos WHERE installation_id IS NOT NULL"
            )).mappings().all()
        touched = 0
        for repo in repos:
            try:
                touched += self._tick_repo(dict(repo))
            except GitHubApiError:
                log.exception("gapfill_repo_failed", repo=repo["full_name"])
        return touched

    def _tick_repo(self, repo: dict) -> int:
        owner, _, name = repo["full_name"].partition("/")
        cursor = repo["backfill_cursor"]
        cursor = cursor if (cursor and cursor.tzinfo) else (cursor.replace(tzinfo=timezone.utc) if cursor else _EPOCH)
        newest = cursor

        pr_numbers = self.reader.list_numbers_since(repo["installation_id"], owner, name, cursor)
        for number in pr_numbers:
            result = self.reader.read_pr(repo["installation_id"], owner, name, repo["repo_num"],
                                         repo["org_id"], number)
            with session_scope() as s:
                apply_map_result(s, self.catalog, result)
            newest = max(newest, _parse_iso(result.artifacts[0]["updated_at"]))

        issue_numbers = self.reader.list_issue_numbers_since(repo["installation_id"], owner, name, cursor)
        for number in issue_numbers:
            result = self.reader.read_issue(repo["installation_id"], owner, name, repo["repo_num"],
                                            repo["org_id"], number)
            if not result.mapped:
                continue
            with session_scope() as s:
                apply_map_result(s, self.catalog, result)
            newest = max(newest, _parse_iso(result.artifacts[0]["updated_at"]))

        if pr_numbers or issue_numbers:
            with session_scope() as s:
                s.execute(self.catalog.get("git", "advance_repo_cursor"),
                         {"repo_id": repo["repo_id"], "cursor": newest.isoformat(), "state": "done"})
        log.info("gapfill_repo", repo=repo["full_name"], prs=len(pr_numbers), issues=len(issue_numbers))
        return len(pr_numbers) + len(issue_numbers)
