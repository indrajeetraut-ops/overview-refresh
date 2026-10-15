from datetime import datetime, timezone

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError

from server.storage.engine import session_scope
from server.storage.schema import Base, QueueJob
from tests.server.storage.conftest import requires_db


def _now():
    return datetime.now(timezone.utc)


def test_no_event_queue_table():
    # event_queue was dropped with the msging-rewrite (ingest.md §14): the session lands
    # directly in sessions.signal_blob, durability is the client's.
    assert "event_queue" not in Base.metadata.tables


def test_queue_jobs_columns_and_indexes():
    cols = Base.metadata.tables["queue_jobs"].columns
    assert set(cols.keys()) == {
        "id", "topic", "dedup_key", "payload", "status", "run_after",
        "attempts", "enqueued_at", "claimed_at",
    }
    idx_names = {ix.name for ix in Base.metadata.tables["queue_jobs"].indexes}
    assert {"uq_queue_jobs_pending", "ix_queue_jobs_claim"} <= idx_names
    pending = next(ix for ix in Base.metadata.tables["queue_jobs"].indexes if ix.name == "uq_queue_jobs_pending")
    assert pending.unique is True
    assert pending.dialect_options["postgresql"]["where"] is not None


@requires_db
def test_partial_unique_index_rejects_second_pending(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.execute(insert(QueueJob).values(
            topic="rollup", dedup_key="s1", payload={}, status="pending", run_after=_now(),
            attempts=0, enqueued_at=_now(),
        ))
    with session_scope() as s:
        with pytest.raises(IntegrityError):
            s.execute(insert(QueueJob).values(
                topic="rollup", dedup_key="s1", payload={}, status="pending", run_after=_now(),
                attempts=0, enqueued_at=_now(),
            ))


@requires_db
def test_partial_unique_index_allows_done_plus_new_pending(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.execute(insert(QueueJob).values(
            topic="rollup", dedup_key="s1", payload={}, status="done", run_after=_now(),
            attempts=0, enqueued_at=_now(),
        ))
        s.execute(insert(QueueJob).values(
            topic="rollup", dedup_key="s1", payload={}, status="pending", run_after=_now(),
            attempts=0, enqueued_at=_now(),
        ))  # a 'done' row does not block a new 'pending' one — no exception
