"""The topic-agnostic dispatch loop (ingest.md §5.1): claim one pending job under SKIP LOCKED,
run its handler in a SAVEPOINT, mark done or (on failure) failed + backoff."""
import threading
from datetime import datetime, timezone

from sqlalchemy import select

from server.storage.engine import session_scope
from server.storage.schema import QueueJob
from server.worker.config import WorkerConfig
from server.worker.dispatch import claim_and_run
from tests.server.worker.conftest import requires_db


def _add_job(dedup_key="s1"):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        s.add(QueueJob(topic="rollup", dedup_key=dedup_key, payload={"session_id": dedup_key},
                       status="pending", run_after=now, attempts=0, enqueued_at=now))


@requires_db
def test_empty_queue_returns_false(engine, catalog):
    assert claim_and_run(catalog, "rollup", lambda s, c, p: None, WorkerConfig()) is False


@requires_db
def test_success_runs_handler_and_marks_done(engine, catalog):
    _add_job()
    seen = []
    assert claim_and_run(catalog, "rollup", lambda s, c, p: seen.append(p), WorkerConfig()) is True
    assert seen == [{"session_id": "s1"}]
    with session_scope() as s:
        assert s.scalars(select(QueueJob.status)).all() == ["done"]


@requires_db
def test_failure_marks_pending_with_backoff(engine, catalog):
    _add_job()

    def boom(s, c, p):
        raise RuntimeError("nope")

    assert claim_and_run(catalog, "rollup", boom, WorkerConfig(max_attempts=5)) is True
    with session_scope() as s:
        job = s.scalars(select(QueueJob)).one()
        assert job.status == "pending" and job.attempts == 1  # retryable until max_attempts


@requires_db
def test_two_workers_skip_locked_exactly_one_runs(engine, catalog):
    _add_job()
    config = WorkerConfig()
    calls, lock = [], threading.Lock()

    def handler(s, c, p):
        with lock:
            calls.append(1)

    results: list[bool] = []

    def worker():
        results.append(claim_and_run(catalog, "rollup", handler, config))

    t1, t2 = threading.Thread(target=worker), threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert len(calls) == 1        # exactly one worker ran the handler (SKIP LOCKED)
    assert results.count(True) == 1


# --------------------- timers (otel-intake design §4.4; dispatch._Due)

def test_timers_fire_immediately_then_wait_out_their_interval():
    """Due on boot: a process that restarts every few minutes must still make progress, and
    every timer registered here is idempotent."""
    from server.worker.dispatch import _Due

    calls = []
    due = _Due([("t", 3600.0, lambda: calls.append(1))])
    assert due.run_due() is True
    assert due.run_due() is False          # interval not elapsed
    assert calls == [1]


def test_a_throwing_timer_does_not_take_the_loop_down():
    """A timer shares the process with the queue handlers; it must not be able to stop them."""
    from server.worker.dispatch import _Due

    calls = []

    def boom():
        raise RuntimeError("nope")

    due = _Due([("bad", 0.0, boom), ("good", 0.0, lambda: calls.append(1))])
    assert due.run_due() is True
    assert calls == [1]
