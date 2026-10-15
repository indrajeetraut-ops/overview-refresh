"""Reopen-history backfill (Track churn/reopen, backfill 4a). Run:
    python -m server.ingest.git.cli_backfill_reopens --repo owner/name [--org] [--apply]

`git_artifacts.state` is current-state-only, so a PR ingested before the `git_artifact_events`
transition log existed carries no reopen count. This reads each known PR's authoritative
REOPENED_EVENT timeline from GitHub (GraphQL) and appends one `git_artifact_events` row per
reopen -- recovering the history forward capture (the webhook path) can't reach for old PRs.

Idempotent: rows are keyed on `source_key = "<artifact_id>:reopened:<createdAt>"`, so re-running
inserts nothing new. NOTE the one caveat: forward webhook capture keys a reopen on the PR's
`updated_at` at reopen time, while this keys on the timeline event's `createdAt`. Those are the
same instant for the reopen itself, so a PR reopened both before AND after the log existed could
in the rare case of a same-second unrelated edit record two rows for one reopen -- an acceptable
edge for a metrics backfill, called out rather than papered over.

Dry-run by default (reads + prints counts, writes nothing); pass `--apply` to write.
"""
from __future__ import annotations

import argparse

import structlog

from server.storage.config import load_config as load_storage_config
from server.storage.engine import get_engine, session_scope
from server.storage.queries import QueryCatalog

from .client import GitHubApiError, GitHubClient, load_graphql
from .cli_backfill import _resolve_installation
from .config import load_config

log = structlog.get_logger(module="ingest.git.cli_backfill_reopens")


def reopen_events_from_nodes(nodes: list[dict], artifact_id: str) -> list[dict]:
    """Pure: timeline REOPENED_EVENT nodes -> `git_artifact_events` insert params. A reopen puts
    a PR back to `open`, so `to_state` is 'open'. Nodes without a `createdAt` (a shape GitHub
    shouldn't send for this itemType) are skipped rather than keyed on None."""
    out = []
    for n in nodes:
        created = (n or {}).get("createdAt")
        if not created:
            continue
        out.append({
            "artifact_id": artifact_id, "action": "reopened", "to_state": "open",
            "occurred_at": created, "source_key": f"{artifact_id}:reopened:{created}",
        })
    return out


def _read_pr_reopens(client: GitHubClient, installation_id: int, owner: str, name: str,
                     number: int, artifact_id: str) -> list[dict]:
    """All reopen events for one PR, paging the timeline connection to exhaustion."""
    query = load_graphql("backfill_pr_timeline")
    events: list[dict] = []
    cursor = None
    while True:
        data = client.graphql(installation_id, query,
                              {"owner": owner, "name": name, "number": number, "cursor": cursor})
        tl = (((data.get("repository") or {}).get("pullRequest") or {}).get("timelineItems") or {})
        events.extend(reopen_events_from_nodes(tl.get("nodes") or [], artifact_id))
        page = tl.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return events
        cursor = page.get("endCursor")


def backfill_repo_reopens(client: GitHubClient, catalog: QueryCatalog, repo_full_name: str,
                          apply: bool) -> dict:
    owner, _, name = repo_full_name.partition("/")
    with session_scope() as s:
        repo = s.execute(catalog.get("git", "get_repo_by_full_name"),
                         {"full_name": repo_full_name}).mappings().first()
        if repo is None:
            raise SystemExit(f"{repo_full_name}: not a known installed repo (no git_repos row).")
        prs = [dict(r) for r in s.execute(catalog.get("git", "list_repo_pr_numbers"),
                                          {"repo_id": repo["repo_id"]}).mappings()]
    counts = {"prs_scanned": 0, "reopens_found": 0, "rows_written": 0}
    for pr in prs:
        counts["prs_scanned"] += 1
        events = _read_pr_reopens(client, repo["installation_id"], owner, name,
                                  pr["number"], pr["artifact_id"])
        counts["reopens_found"] += len(events)
        if apply and events:
            with session_scope() as s:
                for ev in events:
                    row = s.execute(catalog.get("git", "insert_artifact_event"), ev).first()
                    if row is not None:
                        counts["rows_written"] += 1
    log.info("reopen_backfill_done", repo=repo_full_name, apply=apply, **counts)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill PR reopen history from the GitHub timeline.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--repo", help="owner/name")
    target.add_argument("--org", action="store_true", help="every installed repo")
    parser.add_argument("--installation", type=int, default=None)
    parser.add_argument("--apply", action="store_true", help="write rows (default: dry run)")
    parser.add_argument("--config", default="config/github.yaml")
    parser.add_argument("--storage-config", default="config/storage.yaml")
    args = parser.parse_args()

    client = GitHubClient(load_config(args.config))
    storage_cfg = load_storage_config(args.storage_config)
    get_engine(storage_cfg)
    catalog = QueryCatalog.load(storage_cfg.queries_dir)

    if args.repo:
        repos = [args.repo]
    else:
        inst_id = _resolve_installation(client, args.installation)
        resp = client.rest_get(inst_id, "/installation/repositories", params={"per_page": 100})
        repos = [r["full_name"] for r in resp.json().get("repositories", [])]

    for full_name in repos:
        try:
            backfill_repo_reopens(client, catalog, full_name, args.apply)
        except GitHubApiError as e:
            log.error("reopen_backfill_repo_failed", repo=full_name, status=e.status, body=e.body[:300])


if __name__ == "__main__":
    main()
