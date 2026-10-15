import json

from common.msging.clnt.msg_id import MsgIdAllocator
from common.msging.clnt.store import MsgStore, highest_record_id


def test_ids_are_monotonic(tmp_path):
    a = MsgIdAllocator(tmp_path / "msg_id.json", block=10)
    ids = [a.next() for _ in range(25)]
    assert ids == sorted(ids) and len(set(ids)) == 25


def test_only_one_disk_write_per_block(tmp_path):
    p = tmp_path / "msg_id.json"
    a = MsgIdAllocator(p, block=10)
    for _ in range(10):
        a.next()
    first = json.loads(p.read_text())["next_msg_id"]
    a.next()                                   # 11th crosses the block boundary
    assert json.loads(p.read_text())["next_msg_id"] > first


def test_crash_never_reuses_an_id(tmp_path):
    """Simulated crash: drop the allocator without close(). The persisted
    high-water mark must already be past everything handed out (§3.1)."""
    p = tmp_path / "msg_id.json"
    a = MsgIdAllocator(p, block=100)
    handed_out = [a.next() for _ in range(5)]
    del a                                      # no close()
    b = MsgIdAllocator(p, block=100)
    assert b.next() > max(handed_out)


def test_clean_close_does_not_burn_the_block(tmp_path):
    p = tmp_path / "msg_id.json"
    a = MsgIdAllocator(p, block=1000)
    last = [a.next() for _ in range(3)][-1]
    a.close()
    b = MsgIdAllocator(p, block=1000)
    assert b.next() == last + 1


def test_fresh_store_starts_from_a_nonzero_base(tmp_path):
    """Random base so a lost store under a surviving identity.json does not
    replay ids the server already saw (§3.1)."""
    a = MsgIdAllocator(tmp_path / "a.json", block=10)
    b = MsgIdAllocator(tmp_path / "b.json", block=10)
    assert a.next() > 0 and b.next() > 0


def test_the_counter_file_is_never_visible_half_written(tmp_path):
    """D30 applied to `msg_id.json`: the real name only ever changes by
    `os.replace`, so a reader always sees a complete counter. The `.tmp` is a
    different name and is never read."""
    p = tmp_path / "msg_id.json"
    a = MsgIdAllocator(p, block=2)
    for _ in range(6):                             # several block boundaries
        a.next()
        assert json.loads(p.read_text())["next_msg_id"] > 0
    a.close()
    assert json.loads(p.read_text())["next_msg_id"] > 0


def test_the_store_root_defaults_to_the_counter_files_own_directory(tmp_path):
    """The wiring `api.py` relies on: it passes only
    `<store_dir>/msg_id.json`, and `<store_dir>` is also the root holding
    `pending/`/`sent/`/`nacked/`, so the reuse floor (§3.1) finds the records
    without the client having to say where they are.
    """
    store = MsgStore(tmp_path)
    store.put_pending(_record(4242))
    assert highest_record_id(tmp_path) == 4242

    a = MsgIdAllocator(tmp_path / "msg_id.json", block=10)   # no store_root
    assert a.next() > 4242


def test_an_explicit_store_root_is_honoured(tmp_path):
    """...and it can be pointed elsewhere, for a caller that does not co-locate
    the counter with the store."""
    store = MsgStore(tmp_path / "records")
    store.put_pending(_record(777))

    a = MsgIdAllocator(tmp_path / "msg_id.json", block=10,
                       store_root=tmp_path / "records")
    assert a.next() > 777


def _record(n: int):
    from common.msging.models import Envelope, MsgRecord
    return MsgRecord(
        msg_id=n, created_at="2026-07-25T10:15:30Z",
        envelope=Envelope(producer_id="p", msg_id=n, service_name="ingest",
                          created_at="2026-07-25T10:15:30Z"))
