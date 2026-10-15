"""User <-> repo contribution rollup (docs/design/identity-ingestion.md §3.4, plan G5).

The repos a person contributes to are a **derived** user attribute -- they come from activity,
not from any directory provider. Two sources already in hand, kept distinct so each entry
records which side(s) evidence it (IDENT §3.4):

  * telemetry  -- `session_info.repo_id` (+ `repo_id_provenance`), owner via `sessions.user_id`
  * live GitHub -- `git_artifacts` rows whose `author` resolved to this `user_id`

**Refresh strategy (MVP): compute-on-read.** `compute_user_repos()` derives the list on demand
-- cheap (two grouped indexed scans) and always current. `refresh_user_repos()` materializes
it onto `users.info.repos[]` for the deferred post-fold / post-git-apply hook; wiring that
hook into the workers is a later task (TODO below).

The **reverse** direction ("who contributes to repo X") is deliberately *not* served from this
list and needs no `user_repos` table -- it is `repo_contributors()`, a GROUP BY over the same
base tables (IDENT §3.4 trip-wire).

TODO(directory): call `refresh_user_repos()` from the fold handler and git `apply` once those
paths carry a directory dependency, so `users.info.repos[]` is warm for G6's `visible_repos`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from server.storage.queries import QueryCatalog

# REPO §1.1/§1.4 provenance tiers, strongest first. A `path`-tier repo is a local label, never
# a cross-user key; `remote` is what a resolved git_artifacts repo is worth.
_PROV_RANK = {"pr_link": 3, "remote": 2, "path": 1}
_GIT_PROVENANCE = "remote"


def _rank(prov: str | None) -> int:
    return _PROV_RANK.get(prov or "", 0)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


@dataclass
class _Entry:
    repo_id: str
    provenance: str | None
    sources: set[str]
    session_count: int
    pr_count: int
    first_seen: datetime | None
    last_seen: datetime | None

    def fold_seen(self, first: datetime | None, last: datetime | None) -> None:
        if first is not None:
            self.first_seen = first if self.first_seen is None else min(self.first_seen, first)
        if last is not None:
            self.last_seen = last if self.last_seen is None else max(self.last_seen, last)

    def to_dict(self) -> dict:
        return {
            "repo_id": self.repo_id,
            "provenance": self.provenance,
            "sources": sorted(self.sources),
            "session_count": self.session_count,
            "pr_count": self.pr_count,
            "first_seen": _iso(self.first_seen),
            "last_seen": _iso(self.last_seen),
        }


def compute_user_repos(session: Session, catalog: QueryCatalog, user_id: str) -> list[dict]:
    """The derived `repos[]` for one user: the union of their session repos and their
    authored-artifact repos, each tagged with its evidencing `sources` and the strongest
    provenance seen. Ordered by `repo_id` for a stable rollup."""
    entries: dict[str, _Entry] = {}

    for row in session.execute(
        catalog.get("directory", "user_repos_from_sessions"), {"user_id": user_id}
    ):
        e = entries.get(row.repo_id)
        if e is None:
            entries[row.repo_id] = _Entry(
                repo_id=row.repo_id,
                provenance=row.provenance,
                sources={"telemetry"},
                session_count=row.session_count,
                pr_count=0,
                first_seen=row.first_seen,
                last_seen=row.last_seen,
            )
        else:
            # same repo_id, a second provenance tier (e.g. some sessions path, later remote)
            e.session_count += row.session_count
            if _rank(row.provenance) > _rank(e.provenance):
                e.provenance = row.provenance
            e.fold_seen(row.first_seen, row.last_seen)

    for row in session.execute(
        catalog.get("directory", "user_repos_from_git"), {"user_id": user_id}
    ):
        e = entries.get(row.repo_id)
        if e is None:
            entries[row.repo_id] = _Entry(
                repo_id=row.repo_id,
                provenance=_GIT_PROVENANCE,
                sources={"github"},
                session_count=0,
                pr_count=row.pr_count,
                first_seen=row.first_seen,
                last_seen=row.last_seen,
            )
        else:
            e.sources.add("github")
            e.pr_count = row.pr_count
            if _rank(_GIT_PROVENANCE) > _rank(e.provenance):
                e.provenance = _GIT_PROVENANCE
            e.fold_seen(row.first_seen, row.last_seen)

    return [entries[k].to_dict() for k in sorted(entries)]


def refresh_user_repos(session: Session, catalog: QueryCatalog, user_id: str) -> list[dict]:
    """Compute and write `users.info.repos[]`. Returns the list written."""
    repos = compute_user_repos(session, catalog, user_id)
    session.execute(
        catalog.get("directory", "update_user_repos"),
        {"user_id": user_id, "repos": json.dumps(repos)},
    )
    return repos


def repo_contributors(session: Session, catalog: QueryCatalog, repo_id: str) -> list[dict]:
    """The reverse direction, straight off the base tables -- no denormalized list. A
    `path`-tier session repo is excluded (local label, not cross-user, REPO §1.4)."""
    return [
        {"user_id": row.user_id, "session_count": row.session_count, "pr_count": row.pr_count}
        for row in session.execute(
            catalog.get("directory", "repo_contributors"), {"repo_id": repo_id}
        )
    ]
