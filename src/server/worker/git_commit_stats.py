"""The `git_commit_stats` job handler (Track churn/reopen, Landing 2): fill a push's commit
line stats via the GitHub compare API, so post-open churn becomes computable.

A `push` webhook's `commits[]` payload carries file *paths* but no line counts, so the commit
artifacts `map_github.py::_map_push` writes land with empty `stats`. `apply.py` rings this
handler once per push (`before..after`); it calls
`GET /repos/{owner}/{name}/compare/{before}...{after}` -- ONE call covering the whole push range
(the per-push-aggregate decision, mvp-gap-closure-plan Track churn/reopen) -- sums the returned
files' additions/deletions, and merge-patches `{additions, deletions, changed_files}` onto the
push's head-commit artifact (the one whose sha = `after`).

The mapper stays pure/no-IO by contract; the API call lives here, mirroring the A5 backfill
readers. `merge_commit_stats`'s additive jsonb merge means this needs no re-map and is safe to
re-run (the backfill CLI, Landing 4b, calls the identical primitive).
"""
from __future__ import annotations

import json

import structlog
from sqlalchemy.orm import Session as DbSession

from server.ingest.git.client import GitHubApiError, GitHubClient
from server.storage.queries import QueryCatalog

log = structlog.get_logger(module="worker.git_commit_stats")


def _summarize_compare(files: list[dict]) -> dict:
    """Aggregate the compare response's per-file diffstat. GitHub caps `files` at 300 and omits
    it entirely for a diff too large to compute -- both surface here as a smaller-or-absent sum,
    honestly partial rather than wrong; `changed_files` counts what was returned."""
    additions = sum(int(f.get("additions") or 0) for f in files)
    deletions = sum(int(f.get("deletions") or 0) for f in files)
    return {"additions": additions, "deletions": deletions, "changed_files": len(files)}


def fetch_and_merge_commit_stats(s: DbSession, catalog: QueryCatalog, client: GitHubClient,
                                 repo_id: str, before: str, after: str) -> dict | None:
    """The one compare-API-then-merge primitive, shared by the worker handler (forward, one push
    at a time off the queue) and the backfill CLI (historical, `cli_backfill_commit_stats`).
    Returns the merged stats, or None when nothing was written (unknown repo, gone sha, or a 304
    replay). The `merge_commit_stats` merge is additive and idempotent, so re-running is safe."""
    repo = s.execute(catalog.get("git", "get_repo_for_commit_stats"),
                     {"repo_id": repo_id}).mappings().first()
    if repo is None:
        # A repo we've never had an installation for -- nothing to authenticate the call as.
        log.info("commit_stats_repo_unknown", repo_id=repo_id)
        return None
    owner, _, name = repo["full_name"].partition("/")
    try:
        r = client.rest_get(repo["installation_id"], f"/repos/{owner}/{name}/compare/{before}...{after}")
    except GitHubApiError:
        # A gone/force-pushed sha 404s permanently; don't propagate (which would retry a queue
        # job forever / abort a backfill sweep). Transient errors were retried in the HTTP layer.
        log.warning("commit_stats_compare_failed", repo_id=repo_id, before=before, after=after)
        return None
    if r.status_code == 304:
        return None   # already fetched this exact range; stats are merged already
    stats = _summarize_compare(r.json().get("files") or [])
    row = s.execute(catalog.get("git", "merge_commit_stats"),
                    {"repo_id": repo_id, "sha": after, "stats": json.dumps(stats)}).first()
    log.info("commit_stats_merged", repo_id=repo_id, after=after, filled=row is not None, **stats)
    return stats if row is not None else None


def make_git_commit_stats_handler(client: GitHubClient):
    """Closes over a `GitHubClient` (the handler contract is `(s, catalog, payload)`, no room for
    a client arg) -- the same factory shape `make_git_correlate_handler` uses for its config."""

    def handler(s: DbSession, catalog: QueryCatalog, payload: dict) -> None:
        fetch_and_merge_commit_stats(s, catalog, client,
                                     payload["repo_id"], payload["before"], payload["after"])

    return handler
