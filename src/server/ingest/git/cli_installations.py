"""Installation discovery and tenant binding (git-source.md §6.1, implementation plan A7).
Run: python -m server.ingest.git.cli_installations [list|reconcile|map] [--config PATH]

- `list` (default): the original credential smoke test -- sign a JWT, list installations, and
  for each mint an installation token and list the repos it can see. No DB. If this prints your
  org and its repos, the secrets are right and cli_probe will work.
- `reconcile`: `GET /app/installations` is the authoritative discovery path independent of
  webhooks (§6.1) -- it enumerates every installation of the App regardless of whether any
  webhook was ever received, recovering installs that arrived while the receiver was down (past
  the 3-day redelivery window) or that only ever used the API. Seeds/updates `git_installations`
  for each; a pending tenant binding (the setup callback, A7) is applied and consumed if one
  exists, an already-resolved org_id is preserved, and only a genuinely new installation falls
  back to the default org.
- `map --installation ID --org-id ORG`: the operator-mapping fallback (§6.1 mechanism 3) for an
  out-of-band install (installed directly from GitHub, never through our connect flow). Updates
  `git_installations` directly if the row already exists; otherwise records a pending binding so
  the first reconcile or webhook for this id applies it instead of the default org.
"""
from __future__ import annotations

import argparse
import json

from sqlalchemy import text

from server.storage.config import load_config as load_storage_config
from server.storage.engine import get_engine, session_scope
from server.storage.queries import QueryCatalog
from server.worker.git_map import DEFAULT_ORG_ID

from .client import GitHubClient
from .config import load_config


def _list(client: GitHubClient) -> None:
    installations = client.list_installations()
    if not installations:
        print("No installations. Install the App on your org first.")
        return
    for inst in installations:
        account = inst.get("account", {}).get("login", "?")
        inst_id = inst["id"]
        print(f"\ninstallation {inst_id}  account={account}  "
              f"repos={inst.get('repository_selection')}")
        resp = client.rest_get(inst_id, "/installation/repositories", params={"per_page": 100})
        for repo in resp.json().get("repositories", []):
            print(f"    {repo['full_name']}  (id={repo['id']}, private={repo['private']})")


def _reconcile(client: GitHubClient, catalog: QueryCatalog) -> None:
    installations = client.list_installations()
    if not installations:
        print("No installations to reconcile.")
        return
    for inst in installations:
        account = inst.get("account") or {}
        inst_id = inst["id"]
        with session_scope() as s:
            existing = s.execute(catalog.get("git", "get_installation_org"),
                                 {"installation_id": inst_id}).first()
            if existing:
                org_id = existing[0]
            else:
                pending = s.execute(catalog.get("git", "pop_pending_install"),
                                    {"installation_id": inst_id}).first()
                org_id = pending[0] if pending else DEFAULT_ORG_ID
            state = "suspended" if inst.get("suspended_at") else "active"
            s.execute(catalog.get("git", "upsert_installation"), {
                "installation_id": inst_id, "org_id": org_id,
                "account_login": account.get("login"), "account_type": account.get("type"),
                "repo_selection": inst.get("repository_selection"), "state": state,
                "info": json.dumps({}),
            })
        print(f"reconciled installation {inst_id}  account={account.get('login')}  org_id={org_id}")


def _map(catalog: QueryCatalog, installation_id: int, org_id: str) -> None:
    with session_scope() as s:
        existing = s.execute(catalog.get("git", "get_installation_org"),
                             {"installation_id": installation_id}).first()
        if existing:
            # The row already exists (a prior reconcile or webhook created it): update its
            # org_id directly rather than a full upsert, so account/state fields a reconcile
            # already populated are left untouched.
            s.execute(text("UPDATE git_installations SET org_id = :org WHERE installation_id = :id"),
                     {"org": org_id, "id": installation_id})
        else:
            # Not seen yet: record as pending so the FIRST delivery or reconcile pass for this
            # installation applies it instead of falling back to the default org.
            s.execute(catalog.get("git", "bind_pending_install"),
                     {"installation_id": installation_id, "org_id": org_id})
    print(f"mapped installation {installation_id} -> org {org_id}"
         + ("" if existing else " (pending: applied when the installation is seen)"))


def main() -> None:
    parser = argparse.ArgumentParser(description="GitHub App installation discovery and tenant binding.")
    parser.add_argument("verb", nargs="?", default="list", choices=["list", "reconcile", "map"])
    parser.add_argument("--config", default="config/github.yaml")
    parser.add_argument("--storage-config", default="config/storage.yaml")
    parser.add_argument("--installation", type=int, help="required for 'map'")
    parser.add_argument("--org-id", help="required for 'map'")
    args = parser.parse_args()

    config = load_config(args.config)
    client = GitHubClient(config)

    if args.verb == "list":
        _list(client)
        return

    storage_cfg = load_storage_config(args.storage_config)
    get_engine(storage_cfg)
    catalog = QueryCatalog.load(storage_cfg.queries_dir)

    if args.verb == "reconcile":
        _reconcile(client, catalog)
    elif args.verb == "map":
        if not args.installation or not args.org_id:
            raise SystemExit("'map' requires --installation ID --org-id ORG")
        _map(catalog, args.installation, args.org_id)


if __name__ == "__main__":
    main()
