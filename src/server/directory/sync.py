"""sync(records) -> SyncResult -- the sole writer of the directory tables
(docs/design/identity-ingestion.md §3, implementation plan G2).

Providers never touch the DB; they hand `DirectoryRecord`s / `TeamRecord`s to this function.
That is what makes a later SCIM/LDAP/OIDC provider a pure adapter (IDENT §8).

One call, one transaction (the caller owns the `session_scope`):
  1. upsert declared teams;
  2. reconcile every record to a `user_id` (create/enrich/link);
  3. write user fields, precedence-gated on `users.source_provider` (G3);
  4. replace `team_members` and `role_assignments` for exactly the synced users.

Idempotent: re-running the same import writes identical rows and reports
`created=0, updated=0` on the second pass (field writes are COALESCE/updates, membership is
delete-then-reinsert of the same set).
"""
from __future__ import annotations

import time
from collections.abc import Iterable

import structlog
from sqlalchemy.orm import Session

from server.storage.queries import QueryCatalog

from .config import DirectoryConfig
from .errors import UnresolvableRecordError
from .models import DirectoryRecord, RoleGrant, SyncResult, TeamRecord
from .precedence import Precedence
from .reconcile import reconcile

log = structlog.get_logger(module="directory.sync")


def _default_roles(config: DirectoryConfig) -> tuple[RoleGrant, ...]:
    return (RoleGrant(config.default_role, "org", config.default_org_id),)


def sync(
    session: Session,
    catalog: QueryCatalog,
    records: Iterable[DirectoryRecord],
    *,
    teams: Iterable[TeamRecord] = (),
    precedence: Precedence | None = None,
    config: DirectoryConfig | None = None,
) -> SyncResult:
    config = config or DirectoryConfig()
    precedence = precedence or Precedence(config.precedence)
    records = list(records)
    teams = list(teams)
    result = SyncResult()
    started = time.monotonic()

    # 1. teams first -- no FKs, so order is not required, but a member row is more useful
    #    when its team already exists.
    upsert_team = catalog.get("directory", "upsert_team")
    for t in teams:
        session.execute(
            upsert_team,
            {
                "team_id": t.team_id,
                "org_id": t.org_id,
                "name": t.name,
                "parent_team_id": t.parent_team_id,
                "source_provider": t.source_provider,
            },
        )

    # 2. reconcile + 3. precedence-gated field write
    get_user = catalog.get("directory", "get_user")
    update_user = catalog.get("directory", "update_user_fields")
    resolved: list[tuple[DirectoryRecord, str]] = []

    for rec in records:
        try:
            outcome = reconcile(session, catalog, rec)
        except UnresolvableRecordError as e:
            result.skipped += 1
            result.notes.append(str(e))
            continue

        result.linked += outcome.linked
        resolved.append((rec, outcome.user_id))

        if outcome.created:
            result.created += 1
            continue

        cur = session.execute(get_user, {"user_id": outcome.user_id}).first()
        stored_provider = cur.source_provider if cur else None
        if precedence.wins(rec.source_provider, stored_provider):
            res = session.execute(
                update_user,
                {
                    "user_id": outcome.user_id,
                    "email": rec.email,
                    "display_name": rec.display_name,
                    "status": rec.status,
                    "primary_team_id": None,
                    "source_provider": rec.source_provider,
                },
            )
            if res.rowcount and res.rowcount > 0:  # 0 when nothing actually changed
                result.updated += 1
        else:
            result.notes.append(
                f"{outcome.user_id}: field write skipped -- {rec.source_provider!r} "
                f"does not outrank {stored_provider!r}"
            )

    # 4. replace membership + grants for exactly the users this run touched.
    synced_ids = sorted({uid for _, uid in resolved})
    if synced_ids:
        session.execute(
            catalog.get("directory", "delete_team_members_for_users"), {"user_ids": synced_ids}
        )
        session.execute(
            catalog.get("directory", "delete_role_assignments_for_users"), {"user_ids": synced_ids}
        )

    insert_member = catalog.get("directory", "insert_team_member")
    insert_role = catalog.get("directory", "insert_role_assignment")
    for rec, user_id in resolved:
        for m in rec.teams:
            session.execute(
                insert_member,
                {"team_id": m.team_id, "user_id": user_id, "role_in_team": m.role_in_team},
            )
        for g in rec.roles or _default_roles(config):
            session.execute(
                insert_role,
                {
                    "user_id": user_id,
                    "role": g.role,
                    "scope_type": g.scope_type,
                    "scope_id": g.scope_id,
                },
            )

    log.info(
        "directory.sync",
        providers=sorted({r.source_provider for r in records}),
        records=len(records),
        teams=len(teams),
        created=result.created,
        updated=result.updated,
        linked=result.linked,
        skipped=result.skipped,
        duration_ms=round((time.monotonic() - started) * 1000, 1),
    )
    return result
