"""Commit-stats backfill (Track churn/reopen, backfill 4b). Run:
    python -m server.ingest.git.cli_backfill_commit_stats [--repo owner/name] [--apply]

The git_commit_stats worker (Landing 2) fills a push's commit line stats from the compare API
going forward. This recovers the same stats for pushes ingested BEFORE that worker existed: it
finds commit artifacts still missing line stats that carry their push range (a normal push
stashes it on the head commit's `info.github.push_meta`; a truncated push sets the
`before_sha`/`after_sha` columns), and re-runs the identical compare-then-merge primitive
(`fetch_and_merge_commit_stats`) the worker uses.

Idempotent: `merge_commit_stats` is an additive jsonb merge, and the candidate query already
excludes rows that have stats -- re-running only touches what's still empty. Dry-run by default;
pass `--apply` to write. `--repo owner/name` scopes to one repo (else every repo).
"""
from __future__ import annotations

import argparse

import structlog

from server.storage.config import load_config as load_storage_config
from server.storage.engine import get_engine, session_scope
from server.storage.queries import QueryCatalog
from server.worker.git_commit_stats import fetch_and_merge_commit_stats

from .client import GitHubApiError, GitHubClient
from .config import load_config

log = structlog.get_logger(module="ingest.git.cli_backfill_commit_stats")


def backfill_commit_stats(client: GitHubClient, catalog: QueryCatalog, repo_id: str | None,
                          apply: bool) -> dict:
    """One sweep over commit artifacts missing stats. `repo_id=None` means every repo."""
    with session_scope() as s:
        rows = [dict(r) for r in s.execute(catalog.get("git", "commits_needing_stats"),
                                           {"repo_id": repo_id}).mappings()]
    counts = {"candidates": len(rows), "filled": 0}
    for row in rows:
        if not row["before"] or not row["after"]:
            continue
        if not apply:
            continue
        with session_scope() as s:
            stats = fetch_and_merge_commit_stats(s, catalog, client,
                                                 row["repo_id"], row["before"], row["after"])
        if stats is not None:
            counts["filled"] += 1
    log.info("commit_stats_backfill_done", repo_id=repo_id, apply=apply, **counts)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill commit line stats via the compare API.")
    parser.add_argument("--repo", default=None, help="owner/name; omit for every repo")
    parser.add_argument("--apply", action="store_true", help="write stats (default: dry run)")
    parser.add_argument("--config", default="config/github.yaml")
    parser.add_argument("--storage-config", default="config/storage.yaml")
    args = parser.parse_args()

    client = GitHubClient(load_config(args.config))
    storage_cfg = load_storage_config(args.storage_config)
    get_engine(storage_cfg)
    catalog = QueryCatalog.load(storage_cfg.queries_dir)

    repo_id = None
    if args.repo:
        with session_scope() as s:
            repo = s.execute(catalog.get("git", "get_repo_by_full_name"),
                             {"full_name": args.repo}).mappings().first()
        if repo is None:
            raise SystemExit(f"{args.repo}: not a known installed repo (no git_repos row).")
        repo_id = repo["repo_id"]

    try:
        backfill_commit_stats(client, catalog, repo_id, args.apply)
    except GitHubApiError as e:
        log.error("commit_stats_backfill_failed", status=e.status, body=e.body[:300])


if __name__ == "__main__":
    main()
