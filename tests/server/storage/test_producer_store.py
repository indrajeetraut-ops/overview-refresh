import pytest

from common.msging.models import ProducerRecord
from server.storage.producer_store import PgProducerStore
from server.storage.schema import Base
from tests.server.storage.conftest import requires_db


def _rec(pid="p1", prefix="tk_producer_ab", h="hash1"):
    return ProducerRecord(
        producer_id=pid, key_prefix=prefix, key_hash=h, registered_at="2026-07-20T00:00:00Z"
    )


@requires_db
def test_upsert_get_roundtrip(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    store = PgProducerStore()
    store.upsert(_rec())
    got = store.get("p1")
    assert got is not None and got.key_hash == "hash1"


@requires_db
def test_upsert_is_idempotent(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    store = PgProducerStore()
    store.upsert(_rec())
    store.upsert(_rec())  # second time: still one row
    assert len(store.get_by_key_prefix("tk_producer_ab")) == 1


@requires_db
def test_touch_and_revoke(engine):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    store = PgProducerStore()
    store.upsert(_rec())
    store.touch_last_seen("p1", "2026-07-21T00:00:00Z")
    store.set_revoked("p1", True)
    got = store.get("p1")
    assert got.last_seen_at == "2026-07-21T00:00:00Z"
    assert got.revoked is True
