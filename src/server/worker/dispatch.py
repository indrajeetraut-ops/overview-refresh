"""Topic-agnostic claim/complete/retry loop over queue_jobs (ingest.md §5.1, §5.3)."""
from __future__ import annotations

import time
from typing import Callable

import structlog
from sqlalchemy.orm import Session as DbSession

from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog

from .config import WorkerConfig

log = structlog.get_logger(module="worker")

Handler = Callable[[DbSession, QueryCatalog, dict], None]

#: `(name, interval_s, tick)` — work that has no doorbell to ring it. The OTel true-up is the
#: first: its stream is constant, so a timer is the honest trigger rather than a queue nobody
#: would ever fill (otel-intake design §4.4). Timers own their own transactions, unlike
#: queue handlers, which run inside `claim_and_run`'s.
Timer = tuple[str, float, Callable[[], None]]


class _Due:
    """When a timer may next run. Monotonic, so a clock step cannot stall or storm a timer."""

    def __init__(self, timers: list[Timer]) -> None:
        # Due immediately on boot: a process that restarts every few minutes must still make
        # progress, and every timer here is idempotent.
        self._next = {name: 0.0 for name, _interval, _tick in timers}
        self._timers = timers

    def run_due(self) -> bool:
        ran = False
        for name, interval, tick in self._timers:
            now = time.monotonic()
            if now < self._next[name]:
                continue
            try:
                tick()
            except Exception:
                # Loud, and the loop survives: a timer that throws must not take the queue
                # handlers sharing this process down with it.
                log.exception("timer_failed", timer=name)
            # Scheduled from completion, not from the deadline: a tick that overruns its
            # interval must not immediately re-fire and pile up behind itself.
            self._next[name] = time.monotonic() + interval
            ran = True
        return ran


def claim_and_run(catalog: QueryCatalog, topic: str, handler: Handler, config: WorkerConfig) -> bool:
    """Claim at most one pending job for `topic` and run it in one transaction (ingest.md §5.2).
    A handler failure rolls back only its own writes (a SAVEPOINT) so the retry bookkeeping —
    attempts/backoff — always lands under the same claim lock, closing the race a two-phase
    commit would otherwise leave between "rollback the failed fold" and "record the failure".
    Returns True if a job was claimed (whether it succeeded or failed), False if the queue was
    empty."""
    with session_scope() as s:
        claimed = s.execute(
            catalog.get("fold", "claim_job"), {"topic": topic, "k": 1}
        ).mappings().first()
        if claimed is None:
            return False
        job_id = claimed["id"]
        payload = claimed["payload"]
        try:
            with s.begin_nested():
                handler(s, catalog, payload)
        except Exception:
            log.exception("job_failed", topic=topic, job_id=job_id)
            s.execute(
                catalog.get("fold", "mark_job_failed"),
                {"job_id": job_id, "max_attempts": config.max_attempts, "backoff_s": config.retry_backoff_s},
            )
            return True
        s.execute(catalog.get("fold", "mark_job_done"), {"job_id": job_id})
        log.info("job_done", topic=topic, job_id=job_id)
    return True


def run_forever(catalog: QueryCatalog, config: WorkerConfig, handlers: dict[str, Handler],
                timers: list[Timer] | None = None) -> None:
    """The poll loop (ingest.md §5.1). `handlers` maps topic -> handler; one process may run
    several topics, and `timers` adds work nothing rings a doorbell for. Runs until interrupted
    (Ctrl-C / process signal). There is no orphan sweep: the arrival fold is the ingest
    handler's (ingest.md §14), and rollup triggers converge via coalesce + re-trigger (§7). A
    lost-trigger backstop is a named seam (ingest.md §12)."""
    due = _Due(timers or [])
    while True:
        # Timers first: a busy queue must not be able to starve the true-up indefinitely.
        due.run_due()
        claimed_any = False
        for topic, handler in handlers.items():
            if claim_and_run(catalog, topic, handler, config):
                claimed_any = True
        if not claimed_any:
            time.sleep(config.poll_interval_s)
