import json
import threading
from pathlib import Path

import pytest

from common.msging.clnt.store import MsgStore
from common.msging.models import Envelope, MsgRecord


def _rec(msg_id: int) -> MsgRecord:
    return MsgRecord(
        msg_id=msg_id,
        envelope=Envelope(producer_id="prodA", msg_id=msg_id, service_name="ingest",
                          created_at="2026-07-25T10:15:30Z", payload={"n": msg_id}),
        created_at="2026-07-25T10:15:30Z",
    )


@pytest.fixture()
def store(tmp_path):
    return MsgStore(tmp_path)


def test_put_pending_is_visible_and_durable(store):
    store.put_pending(_rec(1))
    assert store.list("pending") == [1]
    assert store.get(1).envelope.payload == {"n": 1}


def test_state_is_the_folder(store, tmp_path):
    store.put_pending(_rec(1))
    store.mark_sent(1)
    assert store.list("pending") == [] and store.list("sent") == [1]
    assert (tmp_path / "sent").exists()


def test_whole_envelope_survives_a_round_trip(store):
    rec = _rec(7)
    rec.envelope.event_id = "e7"
    rec.envelope.dedup = True
    store.put_pending(rec)
    got = store.get(7)
    assert got.envelope.event_id == "e7" and got.envelope.dedup is True


def test_purge_removes_it_everywhere(store):
    store.put_pending(_rec(1))
    store.mark_sent(1)
    store.purge(1)
    assert store.get(1) is None and store.total() == 0


def test_nack_persists_the_reason(store):
    store.put_pending(_rec(1))
    store.mark_sent(1)
    assert store.mark_nacked(1, "schema boom") == "sent"
    assert store.get(1).nack_reason == "schema boom"
    assert store.list("nacked") == [1]


def test_nack_also_takes_a_record_that_is_still_pending(store):
    """§2.2 step 3 marks the record NACKED; it does not say from where.

    The server answers one msg twice -- the 202 and the SSE outcome -- and
    nothing sequences them, so a NACK routinely arrives while `send()` is still
    between `put_pending()` and `mark_sent()`. Refusing it there discarded the
    outcome and parked the msg in `sent/` until `sent_timeout_s`.
    """
    store.put_pending(_rec(1))
    assert store.mark_nacked(1, "app said no") == "pending"
    assert store.list("nacked") == [1] and store.list("pending") == []
    assert store.get(1).nack_reason == "app said no"


def test_nack_reports_which_state_it_moved_the_record_out_of(store):
    """The return is not decoration: a window slot belongs to a `SENT` record
    and to nothing else (§5.1), so the caller releases one only for `"sent"`.
    Releasing on a `pending/` move as well would be a second release for one
    acquire and `max_in_flight` would stop bounding anything."""
    store.put_pending(_rec(1))
    store.put_pending(_rec(2))
    store.mark_sent(2)
    assert store.mark_nacked(1, "x") == "pending"
    assert store.mark_nacked(2, "y") == "sent"


def test_nack_raises_when_the_record_is_in_neither_state(store):
    store.put_pending(_rec(1))
    store.mark_sent(1)
    store.mark_nacked(1, "once")
    with pytest.raises(FileNotFoundError):
        store.mark_nacked(1, "again")   # already NACKED: not ours to move


def test_demote_returns_a_sent_msg_to_pending(store):
    store.put_pending(_rec(1))
    store.mark_sent(1)
    store.demote_to_pending(1)
    assert store.list("pending") == [1] and store.list("sent") == []


def test_sweep_moves_all_sent_and_reports_the_count(store):
    for i in (1, 2, 3):
        store.put_pending(_rec(i))
        store.mark_sent(i)
    assert store.sweep_sent_to_pending() == 3
    assert store.list("pending") == [1, 2, 3] and store.list("sent") == []


def test_listing_is_sorted_by_msg_id(store):
    for i in (30, 10, 20):
        store.put_pending(_rec(i))
    assert store.list("pending") == [10, 20, 30]


def test_listing_is_sorted_numerically_not_lexically(store):
    """2 < 10 < 100 numerically, but "10" < "100" < "2" lexically -- a string
    sort would return [10, 100, 2], which breaks the cleaner loop's ordering
    assumption (design §4.4)."""
    for i in (100, 2, 10):
        store.put_pending(_rec(i))
    assert store.list("pending") == [2, 10, 100]


def test_reopening_the_store_sees_prior_state(store, tmp_path):
    store.put_pending(_rec(1))
    store.mark_sent(1)
    assert MsgStore(tmp_path).list("sent") == [1]


def test_no_partial_file_is_ever_visible(store, tmp_path):
    """Records land via rename, so a reader never sees a half-written file."""
    store.put_pending(_rec(1))
    for path in (tmp_path / "pending").iterdir():
        assert not path.name.endswith(".tmp")


def test_mark_sent_records_sent_at_and_bumps_attempts(store):
    """sent_at is what the cleaner loop's sent_timeout_s check is measured
    against (design §4.4), and attempts feeds its backoff curve -- both must
    survive the PENDING -> SENT transition, not just the folder move."""
    store.put_pending(_rec(1))
    before = store.get(1)
    assert before.sent_at is None and before.attempts == 0
    store.mark_sent(1)
    after = store.get(1)
    assert after.sent_at is not None
    assert after.attempts == 1


def test_stray_tmp_file_is_not_listed_or_loaded(store, tmp_path):
    """A crash mid-write leaves a `.tmp` behind in pending/; it must never be
    treated as a completed record."""
    store.put_pending(_rec(1))
    (tmp_path / "pending" / "2.json.tmp").write_text('{"msg_id": 2')  # truncated, half-written
    assert store.list("pending") == [1]
    assert store.get(2) is None


def test_corrupt_record_file_is_skipped_not_fatal(store, tmp_path):
    """A truncated/corrupt .json that DID complete its rename (e.g. disk
    corruption, not a mid-write crash) must not blow up list()/get() or a
    cleaner sweep -- it is skipped with a loud log instead (see store.py)."""
    store.put_pending(_rec(1))
    (tmp_path / "pending" / "2.json").write_text("{not valid json")
    assert store.list("pending") == [1]
    assert store.get(2) is None
    # sweep must not crash when it walks past a corrupt sent/ record either
    store.mark_sent(1)
    (tmp_path / "sent" / "3.json").write_text("{not valid json")
    assert store.sweep_sent_to_pending() == 1


def test_mark_sent_racing_purge_does_not_resurrect_the_record(store, monkeypatch):
    """Race 1 (reviewer-reproduced): mark_sent reads the pending record into
    memory (`_require`), a concurrent purge() deletes it, and mark_sent's own
    rewrite/rename recreates it -- a "purged" record coming back from the
    dead. The interleaving is pinned with real threads and two `Event`s
    rather than sleeps, so the test is deterministic:

      T_sent  : _require() reads the record -> signals `read_done` -> waits
                on `release_require` before doing anything else.
      T_purge : waits for `read_done`, then calls purge(1). Given a bounded
                window (`join(timeout=...)`) to run to completion *before*
                mark_sent is allowed to resume -- on unpatched (unlocked)
                code this window is enough for purge to finish (local FS
                ops are microseconds fast), reproducing the exact
                interleaving from the reviewer's probe. On the fixed
                (locked) code, purge instead blocks acquiring the store
                lock and the join times out with the thread still alive,
                which is fine -- it proceeds once mark_sent releases the
                lock.

    Fails on current code: purge deletes pending/1.json while mark_sent is
    paused; mark_sent then rewrites and transitions it back into being,
    leaving `total() == 1` and `get(1)` non-None after purge(1) already
    returned.

    Passes after the fix: purge() and mark_sent() are serialized by the
    store lock, so whichever runs first completes atomically before the
    other's read-modify-write can observe a half-mutated state.
    """
    store.put_pending(_rec(1))

    read_done = threading.Event()
    release_require = threading.Event()
    orig_require = MsgStore._require
    triggered = {"done": False}

    def hooked_require(self, dir_path, msg_id):
        rec = orig_require(self, dir_path, msg_id)
        if not triggered["done"]:
            triggered["done"] = True
            read_done.set()
            release_require.wait(timeout=5)
        return rec

    monkeypatch.setattr(MsgStore, "_require", hooked_require)

    t_sent = threading.Thread(target=store.mark_sent, args=(1,))
    t_sent.start()
    assert read_done.wait(timeout=5), "mark_sent never reached _require hook"

    t_purge = threading.Thread(target=store.purge, args=(1,))
    t_purge.start()
    # Give purge a bounded window to run to completion while mark_sent is
    # still paused -- enough time on unlocked code, harmless timeout on
    # locked code (it just means purge is correctly blocked on the lock).
    t_purge.join(timeout=1.0)

    release_require.set()
    t_sent.join(timeout=5)
    t_purge.join(timeout=5)
    assert not t_sent.is_alive() and not t_purge.is_alive()

    assert store.total() == 0, "purged record was resurrected by a racing mark_sent()"
    assert store.get(1) is None


def test_purge_racing_demote_does_not_leak_the_record(store, monkeypatch, tmp_path):
    """Race 2 (reviewer-reproduced): purge() scans pending/, sent/, nacked/
    with exists()-then-unlink(), which is not atomic across the three
    directories. A concurrent demote_to_pending() that moves the file
    between purge's per-directory checks can make purge miss it entirely --
    no exception, the record just leaks on disk.

    Pinned deterministically with real threads and `Event`s:

      T_purge  : its very first `Path.exists()` check (on pending/1.json,
                 which is False -- the record lives in sent/) triggers the
                 hook: signal `checked_pending`, then wait on
                 `release_purge` before the scan continues to sent/ and
                 nacked/.
      T_demote : waits for `checked_pending`, then calls
                 demote_to_pending(1). Given a bounded window
                 (`join(timeout=...)`) to run to completion before purge is
                 allowed to resume its scan -- enough time on unpatched
                 (unlocked) code to move sent/1.json -> pending/1.json
                 behind purge's back; on fixed (locked) code it instead
                 blocks acquiring the store lock (purge is still holding
                 it), so the join just times out with the thread alive.

    Fails on current code: by the time purge's paused scan resumes, the
    record has moved out from under it (sent/ check: False, already moved;
    nacked/ check: False) -- purge returns having deleted nothing, and the
    record is still present in pending/.

    Passes after the fix: purge holds the store lock for its whole scan, so
    demote_to_pending cannot move the file until purge is done. Purge
    correctly deletes it out of sent/ before demote gets a chance to move
    it (demote then loses the transition and raises FileNotFoundError, the
    documented "loser" outcome for a pure _transition race).
    """
    store.put_pending(_rec(1))
    store.mark_sent(1)
    assert store.list("sent") == [1]

    pending_path = tmp_path / "pending" / "1.json"
    checked_pending = threading.Event()
    release_purge = threading.Event()
    orig_exists = Path.exists
    triggered = {"done": False}

    def hooked_exists(self):
        result = orig_exists(self)
        if not triggered["done"] and self == pending_path:
            triggered["done"] = True
            checked_pending.set()
            release_purge.wait(timeout=5)
        return result

    monkeypatch.setattr(Path, "exists", hooked_exists)

    demote_error = {}

    def run_demote():
        try:
            store.demote_to_pending(1)
        except FileNotFoundError as exc:  # expected "loser" outcome once fixed
            demote_error["err"] = exc

    t_purge = threading.Thread(target=store.purge, args=(1,))
    t_purge.start()
    assert checked_pending.wait(timeout=5), "purge never reached the pending/ exists() check"

    t_demote = threading.Thread(target=run_demote)
    t_demote.start()
    # Bounded window for demote to run to completion while purge is paused
    # mid-scan -- enough time on unlocked code, harmless timeout when
    # correctly blocked on the store lock.
    t_demote.join(timeout=1.0)

    release_purge.set()
    t_purge.join(timeout=5)
    t_demote.join(timeout=5)
    assert not t_purge.is_alive() and not t_demote.is_alive()

    assert store.total() == 0, "record leaked on disk: purge skipped it due to a racing demote_to_pending()"
    assert store.get(1) is None
    assert store.list("pending") == []


def test_total_does_not_lose_a_record_being_renamed_between_states(store,
                                                                   monkeypatch):
    """`total()` is one snapshot, not three listings.

    Every transition is an `os.replace` between two state directories, so a
    record moved out of a directory `total()` has not read yet and into one it
    already has is counted in neither -- the store reads one msg lighter than
    it is. `send()`'s `max_pending` check reads this number (`_wait_for_slot`),
    so the undercount handed out a slot §5.1 says it should have refused: an
    over-limit `send()` came back `ok` while the cleaner's epoch sweep was
    moving a record.

    Deterministic, in the same shape as the two purge races above: `total()` is
    paused after it has listed `pending/`, a `demote_to_pending()` is given a
    bounded window to move the record `sent/` -> `pending/`, and then `total()`
    is resumed. Unlocked, the demote completes in that window and `total()`
    answers 0; locked, it blocks on the store lock, the join times out
    harmlessly, and `total()` answers the 1 record that never left the disk.
    """
    store.put_pending(_rec(1))
    store.mark_sent(1)

    listed_pending = threading.Event()
    release = threading.Event()
    orig_valid_ids = MsgStore._valid_ids
    triggered = {"done": False}

    def hooked_valid_ids(self, dir_path):
        out = orig_valid_ids(self, dir_path)
        if dir_path.name == "pending" and not triggered["done"]:
            triggered["done"] = True
            listed_pending.set()
            release.wait(timeout=5)
        return out

    monkeypatch.setattr(MsgStore, "_valid_ids", hooked_valid_ids)

    counted: dict = {}
    t_total = threading.Thread(
        target=lambda: counted.__setitem__("n", store.total()))
    t_total.start()
    assert listed_pending.wait(timeout=5), "total() never listed pending/"

    t_demote = threading.Thread(target=store.demote_to_pending, args=(1,))
    t_demote.start()
    t_demote.join(timeout=1.0)

    release.set()
    t_total.join(timeout=5)
    t_demote.join(timeout=5)
    assert not t_total.is_alive() and not t_demote.is_alive()

    assert counted["n"] == 1, (
        "total() lost a record that was mid-rename between two state "
        "directories, so max_pending would hand out a slot it should refuse")
