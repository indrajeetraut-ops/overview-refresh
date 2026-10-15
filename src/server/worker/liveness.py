"""Session liveness: idle close-out (system-architecture.md §8.5). A session goes idle by the
*absence* of events, so nothing ever rings a doorbell for it -- a timer is the only honest
trigger, same reasoning as otel_trueup.py's tick (the OTel stream is constant, not
event-triggered either). Reopen (IdleClosed -> Live) needs no timer: it happens the moment a
late event actually arrives, which is fold/driver.py's fold().
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog

log = structlog.get_logger(module="worker.liveness")


def close_idle_sessions(catalog: QueryCatalog, idle_after_s: float,
                        now: datetime | None = None) -> int:
    """Flips every `live` session with no event in `idle_after_s` to `idle_closed`.

    Plain `status = 'live'` filter, so this only ever touches JSONL-folded sessions -- an
    `otel_only` stub (otel_trueup.py's `_ensure_session_row`) is a different status and is
    never in scope here.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=idle_after_s)
    with session_scope() as s:
        result = s.execute(catalog.get("ingest", "idle_close_sessions"), {"cutoff": cutoff})
        closed = result.rowcount or 0
    if closed:
        log.info("sessions_idle_closed", count=closed, cutoff=cutoff.isoformat())
    return closed
