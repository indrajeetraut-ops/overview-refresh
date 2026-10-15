from datetime import datetime, timezone

from sqlalchemy import select

from common.msging.models import ProducerRecord
from server.storage import (
    Base,
    PgProducerStore,
    QueryCatalog,
    StorageConfig,
    get_engine,
    session_scope,
)
from server.storage.dbm import operations
from server.storage.schema import Session
from tests.server.storage.conftest import requires_db


def test_public_api_imports():
    assert all(x is not None for x in (Base, StorageConfig, get_engine, session_scope, QueryCatalog, PgProducerStore))


@requires_db
def test_init_then_producer_roundtrip_is_idempotent(cfg):
    operations.init(cfg, force=True)
    store = PgProducerStore()
    rec = ProducerRecord(
        producer_id="int1", key_prefix="tk_producer_zz", key_hash="h", registered_at="2026-07-20T00:00:00Z"
    )
    store.upsert(rec)
    store.upsert(rec)  # idempotent
    assert store.get("int1").producer_id == "int1"
    # init again is a no-op (idempotent, arch rule 3)
    assert operations.init(cfg) == "exists"


@requires_db
def test_subagent_linkage_roundtrips_both_directions(cfg):
    """A subagent runs as its own session; both link directions persist (§3.3)."""
    operations.init(cfg, force=True)
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        s.add(Session(
            session_id="parent1", producer_id="p", agent="claude_code",
            started_at=now, last_event_at=now, subagents=["child1"],  # parent->child
        ))
        s.add(Session(
            session_id="child1", producer_id="p", agent="claude_code",
            started_at=now, last_event_at=now, parent_session_id="parent1",  # child->parent
        ))
    with session_scope() as s:
        # child->parent: indexed lookup "list a session's subagents"
        kids = s.scalars(select(Session.session_id).where(Session.parent_session_id == "parent1")).all()
        assert list(kids) == ["child1"]
        # parent->child: the JSONB list round-trips
        assert s.get(Session, "parent1").subagents == ["child1"]
        assert s.get(Session, "child1").parent_session_id == "parent1"
