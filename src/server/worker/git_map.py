"""The `git_map` job handler (git-source.md §4.1, implementation plan A4): claim (SKIP LOCKED,
already done at the `queue_jobs` level by `dispatch.py`) a landed delivery, map it via
`map_github.py`, apply the result (`apply.py` -- shared with the backfill readers, A5), and mark
the delivery `done` or `unmapped` -- one transaction (`dispatch.py`'s SAVEPOINT), the same shape
as every other queue_jobs handler (`rollup.py`).

`status = unmapped` is deliberately not an error (§5.2): the handler returns normally, and
`dispatch.py` marks the *queue job* done regardless -- correctly recognizing "this event isn't
ours to map" is success, not failure. A genuine bug (an unexpected payload shape) propagates
and `dispatch.py`'s own retry/failure bookkeeping takes over, unchanged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy.orm import Session as DbSession

from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog

from ..ingest.git.apply import apply_map_result
from ..ingest.git.map_github import map_delivery

log = structlog.get_logger(module="worker.git_map")

#: git-source.md §6.1: "until mapped, the install resolves to the default org so no data is
#: lost" -- the same defaulting rule the event header uses for an absent org_id (arch §5.1).
DEFAULT_ORG_ID = "default"


def resolve_org_id(s: DbSession, catalog: QueryCatalog, installation_id: int | None) -> str:
    """The installation is the unit of `org_id` resolution (§6.1); shared with the backfill
    readers (A5), which know their installation but not necessarily its reconciled org.

    Checks a pending tenant binding (A7's setup callback, `git_pending_installs`) before
    falling back to the default: whichever delivery for this installation is mapped FIRST --
    the `installation.created` webhook itself, or any other event that happens to arrive first
    -- consumes (pops) the binding, so every subsequent lookup finds it already applied on
    `git_installations` via `get_installation_org` instead."""
    if installation_id is None:
        return DEFAULT_ORG_ID
    row = s.execute(catalog.get("git", "get_installation_org"),
                    {"installation_id": installation_id}).first()
    if row:
        return row[0]
    pending = s.execute(catalog.get("git", "pop_pending_install"),
                        {"installation_id": installation_id}).first()
    return pending[0] if pending else DEFAULT_ORG_ID


def git_map(s: DbSession, catalog: QueryCatalog, payload: dict) -> None:
    delivery_id = payload["delivery_id"]
    delivery = s.execute(catalog.get("git", "claim_delivery"),
                         {"delivery_id": delivery_id}).mappings().first()
    if delivery is None:
        # Rung for a delivery that no longer exists (pruned, or a stale replay). Nothing to do,
        # and nothing to mark -- the row this job pointed at is simply gone.
        log.warning("git_map_delivery_missing", delivery_id=delivery_id)
        return

    org_id = resolve_org_id(s, catalog, delivery["installation_id"])
    result = map_delivery(delivery["event"], delivery["action"], delivery["payload"], org_id)

    if not result.mapped:
        s.execute(catalog.get("git", "mark_delivery_unmapped"), {"delivery_id": delivery_id})
        log.info("delivery_unmapped", delivery_id=delivery_id, gh_event=delivery["event"])
        return

    apply_map_result(s, catalog, result)

    s.execute(catalog.get("git", "mark_delivery_done"), {"delivery_id": delivery_id})
    log.info("delivery_mapped", delivery_id=delivery_id, gh_event=delivery["event"],
             artifacts=len(result.artifacts), kinds=sorted({a["kind"] for a in result.artifacts}))


def prune_git_deliveries(catalog: QueryCatalog, retention_days: int,
                         now: datetime | None = None) -> int:
    """Delete `done`/`unmapped` deliveries past retention (§10.3) -- `pending`/`failed` stay so
    a mapper fix-and-replay always has the raw payload to re-run against (§10)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    with session_scope() as s:
        result = s.execute(catalog.get("git", "prune_deliveries"), {"cutoff": cutoff.isoformat()})
        return result.rowcount
