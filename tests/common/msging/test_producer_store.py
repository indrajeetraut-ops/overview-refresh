import json
import sys

import pytest

from common.msging.config import ProducerStoreConfig, ProducerStoreFileConfig
from common.msging.models import ProducerRecord
from common.msging.srv.producer_store import FileProducerStore, make_store
from server.storage.producer_store import PgProducerStore


def make_record(producer_id="p1", key_prefix="tk_prod_abcd"):
    return ProducerRecord(
        producer_id=producer_id,
        key_prefix=key_prefix,
        key_hash="hash1",
        registered_at="2026-07-17T00:00:00+00:00",
    )


def test_upsert_get_roundtrip(tmp_path):
    store = FileProducerStore(tmp_path / "producers.json")
    store.upsert(make_record())
    rec = store.get("p1")
    assert rec is not None and rec.key_prefix == "tk_prod_abcd"
    assert store.get("nope") is None


def test_get_by_key_prefix(tmp_path):
    store = FileProducerStore(tmp_path / "producers.json")
    store.upsert(make_record("p1", "tk_prod_aaaa"))
    store.upsert(make_record("p2", "tk_prod_bbbb"))
    matches = store.get_by_key_prefix("tk_prod_bbbb")
    assert [r.producer_id for r in matches] == ["p2"]
    assert store.get_by_key_prefix("tk_prod_zzzz") == []
    # prefix collisions must return every match, not one arbitrary record
    store.upsert(make_record("p3", "tk_prod_bbbb"))
    colliding = store.get_by_key_prefix("tk_prod_bbbb")
    assert {r.producer_id for r in colliding} == {"p2", "p3"}


def test_touch_last_seen_and_revoke(tmp_path):
    store = FileProducerStore(tmp_path / "producers.json")
    store.upsert(make_record())
    store.touch_last_seen("p1", "2026-07-17T01:00:00+00:00")
    store.set_revoked("p1", True)
    rec = store.get("p1")
    assert rec.last_seen_at == "2026-07-17T01:00:00+00:00"
    assert rec.revoked is True


def test_descriptor_blob_stored_as_is(tmp_path):
    store = FileProducerStore(tmp_path / "producers.json")
    rec = make_record()
    rec.descriptor = {"hostname": "mac1", "future": {"nested": True}}
    store.upsert(rec)
    assert store.get("p1").descriptor == {"hostname": "mac1", "future": {"nested": True}}


def test_make_store_config_switch(tmp_path):
    cfg = ProducerStoreConfig(
        backend="file", file=ProducerStoreFileConfig(path=str(tmp_path / "p.json"))
    )
    assert isinstance(make_store(cfg), FileProducerStore)
    # server.storage now provides PgProducerStore (docs/modules/storage.md
    # §5); the postgres backend must resolve to it, not raise.
    assert isinstance(
        make_store(ProducerStoreConfig(backend="postgres")), PgProducerStore
    )


def test_postgres_backend_fails_loudly_not_with_importerror(monkeypatch):
    """Selecting postgres when server.storage is not importable must name the
    cause (NotImplementedError) instead of surfacing a raw ImportError from
    deep inside make_store.

    server.storage.producer_store is actually installed in this repo (Task 9
    context), so make_store(postgres) normally succeeds -- see
    test_make_store_config_switch above. To exercise the "not installed"
    branch we simulate that condition directly rather than asserting a
    behavior (bare failure) that would be false for this repo's current
    state: setting sys.modules[name] = None is the standard way to force the
    next `import` of that name to raise ImportError.
    """
    monkeypatch.setitem(sys.modules, "server.storage.producer_store", None)
    cfg = ProducerStoreConfig(backend="postgres")
    with pytest.raises(NotImplementedError):
        make_store(cfg)


def test_unknown_backend_raises_valueerror():
    with pytest.raises(ValueError):
        make_store(ProducerStoreConfig(backend="redis"))


# ====================================================== the key_prefix index
#
# `get_by_key_prefix` used to be a full `json.loads` of producers.json plus a
# linear scan, and `Auth.verify`'s cache miss called it on the path of *every*
# request -- including the 401s a hostile caller sends, which cost exactly what
# a real key did. It is now a dict lookup against `_by_prefix`, rebuilt whenever
# the parse is. **An index that drifts from the file is worse than no index**:
# it authenticates against records that are no longer there and misses records
# that are, so every mutation and every reload is pinned below.


def _index(store):
    return {prefix: sorted(ids) for prefix, ids in store._by_prefix.items()}


def test_the_index_matches_the_file_after_upsert(tmp_path):
    store = FileProducerStore(tmp_path / "producers.json")
    store.upsert(make_record("p1", "tk_prod_aaaa"))
    store.upsert(make_record("p2", "tk_prod_bbbb"))
    store.upsert(make_record("p3", "tk_prod_aaaa"))
    assert _index(store) == {"tk_prod_aaaa": ["p1", "p3"],
                             "tk_prod_bbbb": ["p2"]}


def test_re_upserting_a_producer_under_a_new_prefix_does_not_strand_the_old_one(
        tmp_path):
    """The index is rebuilt from the data, never patched. Patched, a producer
    whose key was rotated would still be reachable under its **old** prefix --
    which is to say the old key would still find a candidate record, and the
    hash comparison would be the only thing left standing between it and a
    successful verify."""
    store = FileProducerStore(tmp_path / "producers.json")
    store.upsert(make_record("p1", "tk_prod_aaaa"))
    store.upsert(make_record("p1", "tk_prod_zzzz"))
    assert _index(store) == {"tk_prod_zzzz": ["p1"]}
    assert store.get_by_key_prefix("tk_prod_aaaa") == []
    assert [r.producer_id for r in store.get_by_key_prefix("tk_prod_zzzz")] == ["p1"]


def test_the_index_survives_touch_last_seen_and_set_revoked(tmp_path):
    """Both rewrite the whole file, so both re-adopt -- and `touch_last_seen`
    is on the auth path, called on every cache miss. An index dropped or
    corrupted there would take the next request's lookup with it."""
    store = FileProducerStore(tmp_path / "producers.json")
    store.upsert(make_record("p1", "tk_prod_aaaa"))
    store.upsert(make_record("p2", "tk_prod_aaaa"))

    store.touch_last_seen("p1", "2026-07-17T01:00:00+00:00")
    assert _index(store) == {"tk_prod_aaaa": ["p1", "p2"]}
    assert {r.producer_id for r in store.get_by_key_prefix("tk_prod_aaaa")} == {
        "p1", "p2"}

    store.set_revoked("p2", True)
    assert _index(store) == {"tk_prod_aaaa": ["p1", "p2"]}
    # revoked is a record flag, not a store-level removal: `Auth.verify` is what
    # rejects it, and it needs to *find* the record to do so.
    assert {r.producer_id for r in store.get_by_key_prefix("tk_prod_aaaa")} == {
        "p1", "p2"}


def test_a_touch_for_an_unknown_producer_leaves_the_index_alone(tmp_path):
    store = FileProducerStore(tmp_path / "producers.json")
    store.upsert(make_record("p1", "tk_prod_aaaa"))
    store.touch_last_seen("nobody", "2026-07-17T01:00:00+00:00")
    store.set_revoked("nobody", True)
    assert _index(store) == {"tk_prod_aaaa": ["p1"]}


def test_the_index_is_rebuilt_from_disk_by_a_fresh_store(tmp_path):
    """A restart, and the ordinary case: the process that reads the file is not
    the one that wrote it."""
    path = tmp_path / "producers.json"
    writer = FileProducerStore(path)
    writer.upsert(make_record("p1", "tk_prod_aaaa"))
    writer.upsert(make_record("p2", "tk_prod_aaaa"))
    writer.upsert(make_record("p3", "tk_prod_bbbb"))

    reader = FileProducerStore(path)
    assert reader.count() == 3
    assert _index(reader) == {"tk_prod_aaaa": ["p1", "p2"],
                              "tk_prod_bbbb": ["p3"]}
    assert {r.producer_id for r in reader.get_by_key_prefix("tk_prod_aaaa")} == {
        "p1", "p2"}


def test_an_outside_writer_invalidates_the_index_not_just_the_parse(tmp_path):
    """The cache and the index have one invalidation key -- the file's
    `(mtime_ns, size)` -- because they are one thing. Invalidating the parse and
    keeping the index would leave lookups pointing at producer_ids the fresh
    data no longer has, which is a `KeyError` on the auth path."""
    path = tmp_path / "producers.json"
    store = FileProducerStore(path)
    store.upsert(make_record("p1", "tk_prod_aaaa"))
    assert _index(store) == {"tk_prod_aaaa": ["p1"]}

    other = FileProducerStore(path)          # a second writer: another process
    other.upsert(make_record("p2", "tk_prod_bbbb"))
    # p1 is gone from `other`'s copy? No -- `other` re-read the file first, so
    # both records are there. That is the point: the reload must be complete.
    assert store.count() == 2
    assert _index(store) == {"tk_prod_aaaa": ["p1"], "tk_prod_bbbb": ["p2"]}
    assert [r.producer_id for r in store.get_by_key_prefix("tk_prod_bbbb")] == ["p2"]


def test_a_record_deleted_outside_leaves_no_index_entry_behind(tmp_path):
    """The hard direction. A stale *extra* entry is the dangerous one: the
    lookup finds an id, and the data it indexes into no longer has it."""
    path = tmp_path / "producers.json"
    store = FileProducerStore(path)
    store.upsert(make_record("p1", "tk_prod_aaaa"))
    store.upsert(make_record("p2", "tk_prod_bbbb"))

    kept = json.loads(path.read_text(encoding="utf-8"))
    kept.pop("p1")
    path.write_text(json.dumps(kept), encoding="utf-8")

    assert store.get_by_key_prefix("tk_prod_aaaa") == []
    assert _index(store) == {"tk_prod_bbbb": ["p2"]}
    assert store.count() == 1


def test_a_missing_file_reads_as_an_empty_index(tmp_path):
    """Day one, before anything has registered."""
    store = FileProducerStore(tmp_path / "nothing-here.json")
    assert store.count() == 0
    assert store.get_by_key_prefix("tk_prod_aaaa") == []
    assert _index(store) == {}
