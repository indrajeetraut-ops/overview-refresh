"""API round-trip probe. Run:
    python -m server.ingest.git.cli_probe --repo owner/name [--installation ID] [--config PATH]

Proves the historic-analysis path: after the org has installed the App, mint an installation
token and read the same repo's recent PRs two ways -- once over REST, once over GraphQL -- and
print what each returns plus the GraphQL rate-limit cost. This is the acquisition core the real
backfill (git-source.md §8) grows from; the POC just shows both readers answer with live data.
"""
from __future__ import annotations

import argparse

import structlog

from .client import GitHubApiError, GitHubClient, load_graphql
from .config import load_config

log = structlog.get_logger(module="ingest.git.cli_probe")


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe a repo via REST and GraphQL.")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--installation", type=int, default=None)
    parser.add_argument("--config", default="config/github.yaml")
    args = parser.parse_args()

    owner, _, name = args.repo.partition("/")
    if not name:
        raise SystemExit("--repo must be owner/name")

    config = load_config(args.config)
    client = GitHubClient(config)
    inst_id = _resolve_installation(client, args.installation)
    print(f"installation={inst_id}  repo={args.repo}")

    # --- REST: the 'list' pass. Note the absence of additions/deletions/changed_files here;
    #     that asymmetry is the whole reason GraphQL wins the detail pass (design note §2). ---
    print("\n=== REST  GET /repos/%s/pulls?state=all (list, 'Pull Request Simple') ===" % args.repo)
    try:
        resp = client.rest_get(inst_id, f"/repos/{args.repo}/pulls",
                               params={"state": "all", "sort": "updated", "per_page": 5})
    except GitHubApiError as e:
        if e.status == 404:
            raise SystemExit(
                f"    404 -- installation {inst_id} cannot see '{args.repo}'.\n"
                f"    The repo is not in this installation's scope (or the owner/name is wrong).\n"
                f"    Run utils/github_diagnose.sh to see exactly which repos the token can read."
            )
        raise SystemExit(f"    REST failed: {e}")
    for pr in resp.json():
        print(f"    #{pr['number']:<5} {pr['state']:<7} {pr['title'][:60]}")

    # --- GraphQL: the same PRs WITH stats, reviews and files in one round trip. ---
    print("\n=== GraphQL  pullRequests(first:5) with stats+reviews+files ===")
    try:
        data = client.graphql(inst_id, load_graphql("probe"), {"owner": owner, "name": name})
    except GitHubApiError as e:
        raise SystemExit(f"    GraphQL failed ({e.status}): {e.body[:300]}")
    rl = data["rateLimit"]
    print(f"    rateLimit: cost={rl['cost']} remaining={rl['remaining']} "
          f"nodeCount={rl['nodeCount']} resetAt={rl['resetAt']}")
    repo = data["repository"]
    print(f"    repo.databaseId={repo['databaseId']}  (this is repo_id 'github:<num>')")
    for pr in repo["pullRequests"]["nodes"]:
        author = (pr.get("author") or {}).get("login", "?")
        print(f"    #{pr['number']:<5} {pr['state']:<7} +{pr['additions']}/-{pr['deletions']} "
              f"files={pr['changedFiles']} reviews={pr['reviews']['totalCount']} "
              f"by={author}  {pr['title'][:40]}")

    # One summary line for the whole run (git-source.md §7.4 "measure, not estimate") rather
    # than one per request -- `budget_snapshot` reads back what the client itself measured.
    log.info("rate_limit_budget", **client.budget_snapshot(inst_id))


if __name__ == "__main__":
    main()
