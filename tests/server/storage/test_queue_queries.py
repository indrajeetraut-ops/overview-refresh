from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text

from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog
from server.storage.schema import Base, QueueJob
from tests.server.storage.conftest import requires_db

QUERIES_DIR = Path("config/queries")


def test_catalog_loads_ingest_and_fold_queries():
    catalog = QueryCatalog.load(QUERIES_DIR)
    for file_key, name in [
        ("ingest", "ensure_session"), ("ingest", "lock_session"), ("ingest", "ring_rollup"),
        ("fold", "claim_job"), ("fold", "mark_job_done"), ("fold", "mark_job_failed"),
        ("fold", "children_of"), ("fold", "session_info_of"),
    ]:
        assert catalog.get(file_key, name) is not None


@requires_db
def test_ensure_session_creates_once_and_dedups(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    params = {
        "session_id": "s1", "producer_id": "p1", "org_id": "acme", "user_id": "anil",
        "agent": "claude_code", "agent_version": None, "parent_session_id": None,
    }
    with session_scope() as s:
        s.execute(catalog.get("ingest", "ensure_session"), params)
        s.execute(catalog.get("ingest", "ensure_session"), params)  # ON CONFLICT DO NOTHING
    with session_scope() as s:
        rows = s.execute(text("SELECT session_id FROM sessions WHERE session_id = 's1'")).all()
        assert len(rows) == 1


@requires_db
def test_ring_rollup_twice_coalesces_to_one_pending_job(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    with session_scope() as s:
        s.execute(catalog.get("ingest", "ring_rollup"), {"session_id": "parent-1"})
        s.execute(catalog.get("ingest", "ring_rollup"), {"session_id": "parent-1"})
    with session_scope() as s:
        jobs = s.scalars(
            select(QueueJob).where(
                QueueJob.dedup_key == "parent-1", QueueJob.topic == "rollup", QueueJob.status == "pending"
            )
        ).all()
        assert len(jobs) == 1


@requires_db
def test_claim_job_skip_locked_hands_to_exactly_one(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        s.add(QueueJob(
            topic="rollup", dedup_key="s1", payload={"session_id": "s1"},
            status="pending", run_after=now, attempts=0, enqueued_at=now,
        ))

    # Two concurrent transactions: the first claims (and holds) the row; the
    # second's SKIP LOCKED must skip it, proving at-most-one-worker-per-job.
    with engine.connect() as conn1, engine.connect() as conn2:
        with conn1.begin():
            rows1 = conn1.execute(
                catalog.get("fold", "claim_job"), {"topic": "rollup", "k": 1}
            ).fetchall()
            with conn2.begin():
                rows2 = conn2.execute(
                    catalog.get("fold", "claim_job"), {"topic": "rollup", "k": 1}
                ).fetchall()
            assert len(rows1) == 1
            assert len(rows2) == 0
