"""Backfill/gap-fill CLI (git-source.md §8, implementation plan A5). Run:
    python -m server.ingest.git.cli_backfill --repo owner/name [options]

Reads history via the GraphQL bulk reader (default) or the REST gap-fill reader (`--reader
rest`), maps it through the identical `map_github`/`apply` path the webhook uses, and advances
the repo's persisted cursor (`git_repos.backfill_cursor`) so a re-run resumes rather than
restarting (§8.2, §8.3). `--since` overrides the stored cursor for a first run or a deliberate
re-backfill; omitted, an unseen repo starts from the Unix epoch (full history).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import structlog

from server.storage.config import load_config as load_storage_config
from server.storage.engine import get_engine, session_scope
from server.storage.queries import QueryCatalog

from .apply import apply_map_result
from .backfill import _EPOCH, GraphQLBackfillReader, RestBackfillReader
from .client import GitHubApiError, GitHubClient
from .config import load_config

log = structlog.get_logger(module="ingest.git.cli_backfill")


def _resolve_installation(client: GitHubClient, override: int | None) -> int:
    if override:
        return override
    installations = client.list_installations()
    if not installations:
        raise SystemExit("No installations found. Install the App on your org first.")
    if len(installations) > 1:
        ids = ", ".join(str(i["id"]) for i in installations)
        raise SystemExit(f"Multiple installations ({ids}); pass --installation ID.")
    return installations[0]["id"]


def _fetch_repo_meta(client: GitHubClient, installation_id: int, owner: str, name: str) -> dict:
    r = client.rest_get(installation_id, f"/repos/{owner}/{name}")
    body = r.json()
    return {
        "repo_id": f"github:{body['id']}", "repo_num": body["id"], "full_name": body["full_name"],
        "owner": owner, "default_branch": body.get("default_branch") or "", "private": bool(body.get("private")),
        "installation_id": installation_id, "org_id": "",  # org_id filled by the caller
        "info": {},
    }


def _read_cursor(catalog: QueryCatalog, repo_id: str, since_override: str | None) -> datetime:
    if since_override:
        return datetime.fromisoformat(since_override.replace("Z", "+00:00"))
    with session_scope() as s:
        row = s.execute(catalog.get("git", "get_repo_cursor"), {"repo_id": repo_id}).first()
    if row and row[0]:
        return row[0] if row[0].tzinfo else row[0].replace(tzinfo=timezone.utc)
    return _EPOCH


def _advance_cursor(catalog: QueryCatalog, repo_id: str, newest: datetime, state: str) -> None:
    with session_scope() as s:
        s.execute(catalog.get("git", "advance_repo_cursor"),
                  {"repo_id": repo_id, "cursor": newest.isoformat(), "state": state})


def _backfill_repo(client: GitHubClient, catalog: QueryCatalog, installation_id: int,
                   owner: str, name: str, org_id: str, since: datetime | None,
                   reader: str, list_only: bool, dump: bool) -> dict:
    meta = _fetch_repo_meta(client, installation_id, owner, name)
    meta["org_id"] = org_id
    repo_id, repo_num = meta["repo_id"], meta["repo_num"]

    if not list_only:
        with session_scope() as s:
            s.execute(catalog.get("git", "upsert_repo"), {**meta, "info": json.dumps(meta["info"])})

    cursor = since or _read_cursor(catalog, repo_id, None)
    log.info("backfill_start", repo=f"{owner}/{name}", since=cursor.isoformat(), reader=reader)

    counts = {"prs": 0, "issues": 0, "artifacts": 0}
    newest_seen = cursor

    def _consume(result, kind_key: str) -> None:
        nonlocal newest_seen
        counts[kind_key] += 1
        counts["artifacts"] += len(result.artifacts)
        if dump:
            print(json.dumps(result.artifacts, indent=2, default=str))
        if not list_only:
            with session_scope() as s:
                apply_map_result(s, catalog, result)
        ts = datetime.fromisoformat(result.artifacts[0]["updated_at"].replace("Z", "+00:00"))
        newest_seen = max(newest_seen, ts)

    if reader == "graphql":
        gql = GraphQLBackfillReader(client)
        # PRs and issues are two `search` connections sharing the one repo cursor and history
        # window (SIG §4 two-speed plan): sweep PRs to exhaustion, then issues.
        for page_reader, kind_key in ((gql.read_page, "prs"), (gql.read_issue_page, "issues")):
            page_cursor = None
            while True:
                page = page_reader(installation_id, owner, name, repo_num, org_id, cursor, page_cursor)
                for result in page.results:
                    _consume(result, kind_key)
                if page.newest_updated_at and page.newest_updated_at > newest_seen:
                    newest_seen = page.newest_updated_at
                log.info("backfill_page", repo=f"{owner}/{name}", kind=kind_key,
                         count=len(page.results), has_next_page=page.has_next_page,
                         budget=client.budget_snapshot(installation_id))
                if not page.has_next_page:
                    break
                page_cursor = page.next_cursor
    else:
        rest = RestBackfillReader(client)
        for number in rest.list_numbers_since(installation_id, owner, name, cursor):
            _consume(rest.read_pr(installation_id, owner, name, repo_num, org_id, number), "prs")
        for number in rest.list_issue_numbers_since(installation_id, owner, name, cursor):
            result = rest.read_issue(installation_id, owner, name, repo_num, org_id, number)
            if result.mapped:
                _consume(result, "issues")
        log.info("backfill_page", repo=f"{owner}/{name}", prs=counts["prs"], issues=counts["issues"],
                 budget=client.budget_snapshot(installation_id))

    if not list_only:
        _advance_cursor(catalog, repo_id, newest_seen, "done")
    log.info("backfill_done", repo=f"{owner}/{name}", **counts)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill git-source history for a repo.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--repo", help="owner/name")
    target.add_argument("--org", help="backfill every repo the installation covers")
    parser.add_argument("--installation", type=int, default=None)
    parser.add_argument("--org-id", default="default", help="org_id to stamp on artifacts (A7 binds this for real)")
    parser.add_argument("--since", default=None, help="ISO-8601; overrides the stored cursor")
    parser.add_argument("--reader", choices=["graphql", "rest"], default="graphql")
    parser.add_argument("--list-only", action="store_true", help="read and print counts, write nothing")
    parser.add_argument("--dump", action="store_true", help="print each PR's mapped artifacts as JSON")
    parser.add_argument("--config", default="config/github.yaml")
    parser.add_argument("--storage-config", default="config/storage.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    client = GitHubClient(config)
    inst_id = _resolve_installation(client, args.installation)

    storage_cfg = load_storage_config(args.storage_config)
    get_engine(storage_cfg)
    catalog = QueryCatalog.load(storage_cfg.queries_dir)

    # Normalize to tz-aware, matching _read_cursor: a date-only --since (e.g. "2026-08-25")
    # parses naive, and the readers emit tz-aware timestamps -- comparing the two raises
    # TypeError in _backfill_repo's newest_seen tracking. Assume UTC when no offset is given.
    since = None
    if args.since:
        since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

    if args.repo:
        owner, _, name = args.repo.partition("/")
        if not name:
            raise SystemExit("--repo must be owner/name")
        repos = [(owner, name)]
    else:
        resp = client.rest_get(inst_id, "/installation/repositories", params={"per_page": 100})
        repos = [(r["full_name"].split("/", 1)[0], r["full_name"].split("/", 1)[1])
                for r in resp.json().get("repositories", [])]
        if not repos:
            raise SystemExit(f"installation {inst_id} sees zero repos for --org {args.org}")

    for owner, name in repos:
        try:
            _backfill_repo(client, catalog, inst_id, owner, name, args.org_id, since,
                           args.reader, args.list_only, args.dump)
        except GitHubApiError as e:
            log.error("backfill_repo_failed", repo=f"{owner}/{name}", status=e.status, body=e.body[:300])


if __name__ == "__main__":
    main()
