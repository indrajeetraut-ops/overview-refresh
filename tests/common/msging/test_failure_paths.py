"""Failure paths -- the tests that justify the design (design §2.3, §3.1, §4.3,
§4.4, §5.1, §8.1; decisions D16, D17, D22, D29, D30, D38, D42).

Every test here names the decision it pins. That is the point of the file: a
decision nobody can break silently is a decision, and one nobody pinned is a
coincidence.

**Deterministic and component-level, by construction.** Clocks are injected,
outcomes are forced onto disk, `os.replace` is made to fail on demand, and the
only server is `conftest.MockServer` over an `httpx.MockTransport`. Nothing
here waits out a real timeout, opens a socket, or depends on timing luck.

**What this file deliberately does not duplicate.** `test_integration.py`
already drives restart reconciliation, the epoch sweep, `sent_timeout_s`,
`slot_full`, `server_busy`, `too_large` and NACK PURGE/RETAIN end-to-end
against a live uvicorn server, and `test_cleaner.py`/`test_store.py`/
`test_api.py` pin each of those mechanisms in isolation. Re-running them here
over a mock transport would buy nothing but runtime. What is here instead is
the half neither reaches:

  - the *composite* failure path -- a 503 that keeps a record and a later pass
    that ships it; a demotion that produces a genuine second POST; a NACK
    decision that survives a process restart;
  - the states a healthy run never produces -- a corrupt record in each of the
    three directories, a `.tmp` a crash left behind, a torn `msg_id.json`, an
    `os.replace` that fails mid-transition;
  - the gaps, pinned so they are documented rather than accidental -- a
    `nacked/` backlog is invisible to the `max_pending` alarm but is *not*
    invisible to `slot_full`.
"""
from __future__ import annotations

import io
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import structlog

from common.msging import clnt, metrics as metrics_mod
from common.msging.clnt import store as store_mod
from common.msging.clnt.cleaner import Cleaner
from common.msging.clnt.msg_id import MsgIdAllocator
from common.msging.clnt.store import MsgStore
from common.msging.clnt.window import Window
from common.msging.codes import Action, Code
from common.msging.log import configure_logging
from common.msging.models import Envelope, MsgRecord

from .conftest import MockServer, wait_until, write_cfg


# -- helpers --------------------------------------------------------------


def _rec(n: int) -> MsgRecord:
    return MsgRecord(
        msg_id=n, created_at="2026-07-25T10:15:30Z",
        envelope=Envelope(producer_id="p", msg_id=n, service_name="ingest",
                          created_at="2026-07-25T10:15:30Z"))


def _capture() -> io.StringIO:
    """Structured log output as a string, so an assertion can name the event
    (§10.4). Process-global structlog config, exactly as `test_cleaner.py`
    does it."""
    stream = io.StringIO()
    configure_logging(json=True,
                      logger_factory=structlog.PrintLoggerFactory(file=stream))
    return stream


def _cleaner(tmp_path, send_fn, **kw) -> tuple[MsgStore, Cleaner, Window]:
    """A store + cleaner + window triple with nothing running in the
    background: every pass in this file is driven synchronously."""
    store = MsgStore(tmp_path)
    window = Window(kw.pop("max_in_flight", 64))
    opts = dict(sweep_interval_s=0.01, sent_timeout_s=300.0, retry_num=3)
    opts.update(kw)
    return store, Cleaner(store, window, send_fn, **opts), window


def _registered(tmp_path, server: MockServer, **cfg) -> str:
    clnt.clnt_init(write_cfg(tmp_path, **cfg))
    return clnt.register({"host": "x", "producer_desc": "collector-7"},
                         _transport=server.transport())["producer_id"]


def _corrupt(dir_path, msg_id: int) -> None:
    """A record file that completed its rename and then went bad -- bit rot,
    an out-of-band edit, a half-flushed page. Distinct from a `.tmp` (design
    §3, corrupt-record policy)."""
    (dir_path / f"{msg_id}.json").write_text("{not json", encoding="utf-8")


def _age_sent_record(store: MsgStore, msg_id: int, *, seconds: int) -> None:
    """Backdate a `sent/` record's `sent_at` by `seconds`, in place.

    Forced state on disk, not a slept-out timeout: `sent_timeout_s` is
    measured from the timestamp, so ageing the record is the honest way to
    reach the branch and it costs nothing.
    """
    rec = store.get(msg_id)
    rec.sent_at = (datetime.now(timezone.utc)
                   - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (store._dirs["sent"] / f"{msg_id}.json").write_text(rec.model_dump_json(),
                                                        encoding="utf-8")


def _msging_threads() -> list[str]:
    return sorted(t.name for t in threading.enumerate()
                  if t.name.startswith("msging-"))


def _replace_failing_into(state: str):
    """A drop-in for `os.replace` that raises `OSError` for renames landing in
    `<store>/<state>/` and behaves normally everywhere else.

    Targeted rather than blanket: `MsgStore` uses `os.replace` for both the
    tmp -> real-name write and the state transition, and a failure that hits
    both cannot tell which half of a two-hop move broke.
    """
    real = os.replace

    def fake(src, dst, *a, **kw):
        if os.path.basename(os.path.dirname(str(dst))) == state:
            raise OSError(5, "injected I/O error")
        return real(src, dst, *a, **kw)

    return fake


@pytest.fixture(autouse=True)
def _no_leaked_threads():
    """Tear the process-wide client state down *here*, then assert no cleaner
    or SSE thread outlived it.

    The teardown is repeated rather than left to `conftest`: an autouse fixture
    declared in `conftest.py` is set up first and therefore finalises last, so
    a check written into this fixture would otherwise run while every thread is
    still alive. `_reset_for_tests()` is idempotent, so conftest's later call
    is a no-op.
    """
    yield
    clnt._reset_for_tests()
    assert wait_until(lambda: not _msging_threads(), 5.0), (
        f"threads outlived the producer: {_msging_threads()}")


# -- 1. restart reconciliation (§2.3) -------------------------------------


def test_a_restart_resends_pending_and_sent_with_the_envelope_verbatim(tmp_path):
    """§2.3 (D30, §3): `PENDING` -> resend, `SENT` -> resend, and the record
    stores the **whole envelope** so the resend rebuilds the request verbatim.

    The verbatim half is what nothing else pins. `event_id`, `dedup`, `info`,
    `producer_desc`, `service_name` and `created_at` cannot be recomputed from
    the payload after a restart -- if the record kept only the payload, the
    resend would be a *different* msg wearing the same `msg_id`, and the
    server's dedup key would still call it a duplicate.
    """
    server = MockServer()
    cfg = write_cfg(tmp_path)
    clnt.clnt_init(cfg)
    pid = clnt.register({"host": "x", "producer_desc": "collector-7"},
                        _transport=server.transport())["producer_id"]
    in_flight = clnt.send(pid, {"a": 1}, info={"k": "v"},
                          event_id="ev-1", dedup=True).msg_id
    never_sent = clnt.send(pid, {"b": 2}).msg_id
    store = clnt._store_for_tests(pid)
    # One record in each of the two states §2.3 resends.
    store.demote_to_pending(never_sent)
    assert store.list("sent") == [in_flight]
    assert store.list("pending") == [never_sent]
    before = {e.msg_id: e for e in server.envelopes}
    assert set(before) == {in_flight, never_sent}

    clnt._reset_for_tests()                       # a process restart
    clnt.clnt_init(cfg)
    clnt.register({"host": "x", "producer_desc": "collector-7"},
                  _transport=server.transport())

    assert wait_until(lambda: {e.msg_id for e in server.envelopes[2:]}
                      == {in_flight, never_sent}), (
        f"both states must resend, saw {[e.msg_id for e in server.envelopes[2:]]}")
    after = {e.msg_id: e for e in server.envelopes[2:]}
    for msg_id in (in_flight, never_sent):
        assert after[msg_id] == before[msg_id], (
            f"msg {msg_id} was not rebuilt verbatim after the restart")
    # Named explicitly: an equality on the model would still pass if the field
    # were dropped from both sides by a schema change.
    assert after[in_flight].event_id == "ev-1"
    assert after[in_flight].dedup is True
    assert after[in_flight].info == {"k": "v"}
    assert after[in_flight].producer_desc == "collector-7"


# -- 2. epoch sweep (D16, §4.3) -------------------------------------------


def test_an_epoch_sweep_moves_every_readable_record_and_survives_a_corrupt_one(
        tmp_path):
    """D16 (§4.3): an epoch change returns all of `sent/` to `pending/`.

    The sweep is the one bulk operation in the client, so it is the one a
    single bad file can take down: an exception here aborts the pass before
    the demotions, the gauges and the `max_pending` alarm ever run, and every
    healthy record stays parked in `sent/` forever. The corrupt record is
    skipped, left on disk for an operator, and excluded from the count -- the
    metric must not claim to have moved something it did not.
    """
    metrics_mod.reset_metrics()
    store, cleaner, window = _cleaner(tmp_path, lambda rec: Code.OK)
    for n in (1, 2):
        store.put_pending(_rec(n))
        store.mark_sent(n)
        window.acquire()
    _corrupt(store._dirs["sent"], 3)

    cleaner.on_epoch("epoch-b")
    counts = cleaner.sweep_once()

    assert counts["demoted"] == 2
    assert store.list("pending") == [1, 2]
    assert store.list("sent") == []
    assert (store._dirs["sent"] / "3.json").exists(), (
        "a corrupt record must be left for inspection, never deleted")
    snap = metrics_mod.metrics()
    assert snap["epoch_swept_msgs_total"]["{}"] == 2
    assert window.in_flight() == 0, "each swept record hands its slot back"


# -- 3. sent_timeout_s (D17, §4.4) ----------------------------------------


def test_the_sent_timeout_backstop_produces_a_genuine_second_post(tmp_path):
    """D17 (§4.4): `sent_timeout_s` backstops every other lost-outcome path.

    Demoting the record is only half of the promise -- the msg has to actually
    go out again. Three passes: pass 1 sends it, pass 2 demotes it once its
    `sent_at` is older than the timeout, pass 3 resends it. `attempts` on the
    record is the durable proof that two POSTs happened, since `mark_sent` is
    the only thing that bumps it.

    The age is forced by writing an old `sent_at` onto the record rather than
    by moving a clock forward: `mark_sent` stamps `sent_at` from the real
    clock, so a skewed `_now_fn` would also make the *resent* record look
    stale and pass 3 would demote what it had just shipped. Ageing the record
    instead keeps every pass reading one clock -- and lets pass 3 assert the
    thing that matters, that a freshly resent msg is not immediately demoted
    again.
    """
    seen: list[int] = []
    store, cleaner, window = _cleaner(
        tmp_path, lambda rec: (seen.append(rec.attempts), Code.OK)[1],
        sent_timeout_s=30.0)
    store.put_pending(_rec(1))

    assert cleaner.sweep_once() == {"retried": 1, "demoted": 0, "purged": 0}
    assert store.list("sent") == [1] and window.in_flight() == 1

    _age_sent_record(store, 1, seconds=60)
    assert cleaner.sweep_once() == {"retried": 0, "demoted": 1, "purged": 0}
    assert store.list("pending") == [1]
    assert window.in_flight() == 0, "§5.1: a demotion frees the slot"

    assert cleaner.sweep_once() == {"retried": 1, "demoted": 0, "purged": 0}
    assert store.list("sent") == [1]
    assert seen == [0, 1], "the second POST saw the record the first one wrote"
    assert store.get(1).attempts == 2


# -- 4. slot_full (D22, §5.1) ---------------------------------------------


def test_slot_full_counts_the_whole_store_not_just_pending(tmp_path):
    """D22 (§5.1): `max_pending` bounds the **durable store**, and `send()`'s
    check reads `total()` -- all three directories -- not `pending/` alone.

    Every record costs the same disk whichever folder it sits in, so a backlog
    of NACKed msgs the app never resolved is exactly as much of a reason to
    refuse a `send()` as an outage's `pending/` backlog. Reading `pending/`
    here instead would let an unresolved-NACK backlog grow without limit.
    """
    server = MockServer()
    pid = _registered(tmp_path, server, max_pending=2, slot_wait_s=0.05)
    nacked = [clnt.send(pid, {"n": n}).msg_id for n in (1, 2)]
    for msg_id in nacked:
        clnt._deliver_for_tests(pid, code="nack", msg_id=msg_id, reason="bad")
    store = clnt._store_for_tests(pid)
    assert store.list("nacked") == sorted(nacked)
    assert store.count("pending") == 0 and store.count("sent") == 0

    result = clnt.send(pid, {"n": 3})

    assert result.err_code == Code.SLOT_FULL
    assert result.msg_id is None, "§5.1: slot_full means NOT stored"
    assert store.total() == 2, "nothing was written for the rejected send"
    assert server.calls["event"] == 2, "the rejected msg never went on the wire"


def test_a_nacked_backlog_never_reaches_the_max_pending_alarm(tmp_path):
    """D22/D26 (§4.4, §5.1): the cleaner's alarm watches `pending/` **only**.

    Pinned to make the asymmetry a decision rather than an accident. §4.4's
    alarm is about an outage's backlog -- msgs the client still owes the
    server -- and a NACKed record is not that: it is waiting on the app's
    `resolve()`, and no amount of sweeping changes it. The consequence is a
    real operational gap and is stated here so it is visible: a store filling
    with unresolved NACKs is silent right up to the moment `send()` starts
    returning `slot_full` (the test above). Nothing alarms on `nacked/`.
    """
    out = _capture()
    metrics_mod.reset_metrics()
    store, cleaner, _ = _cleaner(tmp_path, lambda rec: Code.OK, max_pending=1)
    for n in (1, 2, 3):
        store.put_pending(_rec(n))
        store.mark_nacked(n, "rejected")
    assert store.count("nacked") == 3

    cleaner.sweep_once()

    assert "cleaner_max_pending" not in out.getvalue()
    # §10.5's gauge reads `pending/` too, so the backlog is invisible there as
    # well -- there is no metric an operator could alarm on instead.
    assert metrics_mod.metrics()["pending_count"] == {'{"server_alias":null}': 0}
    assert store.count("nacked") == 3, "and the sweep leaves every one of them"


# -- 5. server_busy (D42, §8.1) -------------------------------------------


def test_server_busy_keeps_the_record_pending_and_a_later_pass_ships_it(tmp_path):
    """D42 (§8.1, §6.2): a 503 is a *deferral*, not a verdict.

    The composite neither the unit tests nor the integration suite pins: the
    record survives the busy answer in `pending/`, the window slot goes back
    (nothing is in flight), and the very next cleaner pass -- once the server
    is taking traffic again -- ships it without the app doing anything. A 503
    that quietly NACKed or dropped the msg would look identical at the moment
    it happened and only differ here.
    """
    server = MockServer(event_status=503)
    pid = _registered(tmp_path, server)
    producer = clnt._producer_for_tests(pid)
    store = clnt._store_for_tests(pid)

    result = clnt.send(pid, {"x": 1})

    assert result.err_code == Code.SERVER_BUSY
    assert result.msg_id is not None, "§8.1: server_busy IS stored"
    assert store.list("pending") == [result.msg_id]
    assert producer.window.in_flight() == 0, "nothing landed, nothing in flight"

    server.event_status = 202                     # the server recovers
    clnt.flush(pid)

    assert store.list("pending") == []
    assert store.list("sent") == [result.msg_id]
    assert server.calls["event"] >= 2
    assert [e.msg_id for e in server.envelopes][-1] == result.msg_id


# -- 6. NACK PURGE and RETAIN (D38, §2.2) ---------------------------------


def test_purge_is_terminal_across_a_restart(tmp_path):
    """D38 (§2.2 step 4, §2.3): `resolve(PURGE)` is the app saying "never
    again", and the only thing that makes that true is that the record is gone
    from disk before the process ends. §2.3 folds every `NACKED` record it
    finds back into `PENDING`, so a PURGE that only marked the record would be
    silently undone by the next start."""
    server = MockServer()
    cfg = write_cfg(tmp_path)
    clnt.clnt_init(cfg)
    pid = clnt.register({"host": "x"},
                        _transport=server.transport())["producer_id"]
    msg_id = clnt.send(pid, {"x": 1}).msg_id
    clnt._deliver_for_tests(pid, code="nack", msg_id=msg_id, reason="bad")
    clnt.resolve(pid, msg_id, Action.PURGE)
    assert clnt._store_for_tests(pid).total() == 0

    clnt._reset_for_tests()
    clnt.clnt_init(cfg)
    clnt.register({"host": "x"}, _transport=server.transport())

    store = clnt._store_for_tests(pid)
    assert store.total() == 0
    # `flush()` is a full cleaner pass on this thread: if anything survived the
    # purge in any state, this is what would put it back on the wire.
    clnt.flush(pid)
    assert server.calls["event"] == 1, "a purged msg must never be resent"


def test_an_unresolved_nack_is_a_retain_across_a_restart(tmp_path):
    """D38 (§2.2 step 5, §2.3): **RETAIN is what "no decision" means.**

    An app that crashes before calling `resolve()` has not declined to
    decide -- it has not decided yet -- so the record is folded into
    `PENDING` and resent. This is the half that makes the default safe: the
    other reading (drop it) would silently lose a msg every time an app died
    with a NACK outstanding.
    """
    server = MockServer()
    cfg = write_cfg(tmp_path)
    clnt.clnt_init(cfg)
    pid = clnt.register({"host": "x"},
                        _transport=server.transport())["producer_id"]
    msg_id = clnt.send(pid, {"x": 1}).msg_id
    clnt._deliver_for_tests(pid, code="nack", msg_id=msg_id, reason="bad")
    assert clnt._store_for_tests(pid).list("nacked") == [msg_id]

    clnt._reset_for_tests()                       # no resolve() ever ran
    clnt.clnt_init(cfg)
    clnt.register({"host": "x"}, _transport=server.transport())

    store = clnt._store_for_tests(pid)
    assert store.list("nacked") == []
    assert wait_until(lambda: [e.msg_id for e in server.envelopes[1:]] == [msg_id]), (
        "an unresolved NACK is a RETAIN and must go out again")
    assert store.get(msg_id) is not None, "and it is still durable"


# -- 7. crash mid-write: the stray .tmp (D30, §3.1) -----------------------


def test_a_crash_mid_write_leaves_a_tmp_that_no_count_can_see(tmp_path):
    """D30 (§3.1, §3): `write tmp -> fsync -> rename -> fsync dir`, so a
    partially written record is never visible under its real name.

    `list()`/`get()` are the obvious half and `test_store.py` pins them. The
    half that matters operationally is the counts: `total()` gates `send()`
    against `max_pending`, and `file_count()` is what `close()` calls
    `unresolved`. A `.tmp` counted by either would eat a producer's send
    budget forever and make `drained` unreachable -- for a file that never
    landed and never will.
    """
    server = MockServer()
    pid = _registered(tmp_path, server)
    store = clnt._store_for_tests(pid)
    (store._dirs["pending"] / "424242.json.tmp").write_text(
        '{"msg_id": 424242', encoding="utf-8")

    assert store.list("pending") == []
    assert store.get(424242) is None
    assert store.count("pending") == 0
    assert store.file_count("pending") == 0
    assert store.total() == 0

    result = clnt.close(pid, timeout_s=0.5)
    assert result.status == "drained" and result.unresolved == 0
    assert (store._dirs["pending"] / "424242.json.tmp").exists(), (
        "the leftover is ignored, not cleaned up behind the operator's back")


# -- corrupt records, in every state (§3 corrupt-record policy) -----------


@pytest.mark.parametrize("state", ["pending", "sent", "nacked"])
def test_a_corrupt_record_is_skipped_logged_and_never_deleted(tmp_path, state):
    """§3's corrupt-record policy, in all three directories.

    Three properties, and all three matter:
      - `list()`/`count()`/`get()` skip it -- the cleaner cannot act on a
        record no one can read, so surfacing it would only produce a crash per
        sweep;
      - the error is **logged loudly**, because a skipped record that is also
        silent is a msg that vanished;
      - the file stays on disk, so an operator has something to look at.

    `nacked/` is the one nothing else covers, and it is the one with no way
    out: `sweep_nacked_to_pending()` skips it at every start, so it is never
    folded, never resent and never counted again.
    """
    out = _capture()
    store = MsgStore(tmp_path)
    store.put_pending(_rec(1))
    if state == "sent":
        store.mark_sent(1)
    elif state == "nacked":
        store.mark_nacked(1, "bad")
    _corrupt(store._dirs[state], 2)

    assert store.list(state) == [1]
    assert store.count(state) == 1
    assert store.get(2) is None
    assert store.file_count(state) == 2, (
        "the file is on disk and file_count is the count that admits it")
    assert store.total() == 1
    assert "msg_store_corrupt_record" in out.getvalue()
    assert '"level": "error"' in out.getvalue()

    # A bulk sweep walks straight past it instead of dying on it.
    if state == "sent":
        assert store.sweep_sent_to_pending() == 1
    elif state == "nacked":
        assert store.sweep_nacked_to_pending() == 1
    assert (store._dirs[state] / "2.json").exists()


def test_a_corrupt_record_does_not_stop_a_cleaner_pass(tmp_path):
    """§4.4 + §3: one unreadable file must not cost the pass its retries, its
    demotions or its alarm. An exception out of `_retry_pending` unwinds the
    whole pass, and the loop swallows it -- so the failure mode is a client
    that looks alive and never ships anything again.

    Both phases are exercised in one pass, each with a corrupt file sitting in
    the directory it walks, and the unreadable record is never handed to
    `send_fn`: there is no envelope to send."""
    sent: list[int] = []
    store, cleaner, _ = _cleaner(
        tmp_path,
        lambda rec: (sent.append(rec.msg_id), Code.STORED_UNREACHABLE)[1],
        sent_timeout_s=30.0)
    store.put_pending(_rec(1))                    # retried this pass
    store.put_pending(_rec(2))
    store.mark_sent(2)
    _age_sent_record(store, 2, seconds=60)        # demoted this pass
    _corrupt(store._dirs["pending"], 8)
    _corrupt(store._dirs["sent"], 9)

    counts = cleaner.sweep_once()

    assert counts == {"retried": 1, "demoted": 1, "purged": 0}
    assert sent == [1], "a record no one can read is never handed to send_fn"
    assert sorted(store.list("pending")) == [1, 2]


def test_close_never_reports_drained_with_a_corrupt_record_in_sent(tmp_path):
    """§2.4: `unresolved` is "what is still on disk", and an unreadable
    `sent/12.json` is still a msg that never got an outcome.

    `count()` hides it because the cleaner cannot act on it; `close()` uses
    `file_count()` precisely so it cannot. `drained` is a claim that nothing
    is outstanding, and this is the case that would make the claim false.
    """
    server = MockServer()
    pid = _registered(tmp_path, server)
    store = clnt._store_for_tests(pid)
    _corrupt(store._dirs["sent"], 12)

    result = clnt.close(pid, timeout_s=0.3)

    assert result.status == "timeout"
    assert result.unresolved == 1
    assert (store._dirs["sent"] / "12.json").exists()


# -- msg_id durability (D29, §3.1) ----------------------------------------


def test_no_crash_between_reserving_and_using_a_block_ever_reissues_an_id(
        tmp_path):
    """D29 (§3.1): block reservation is only safe because **gaps are fine and
    reuse is not.**

    A reused `msg_id` is worse than a lost one: the server dedupes on
    `(producer_id, msg_id)`, so the second msg to wear an id is silently
    dropped -- an at-least-once library losing a msg while reporting `ok`.
    Twelve simulated crashes at every offset within a block, including the
    boundary itself (the dangerous one: the reservation write happens *before*
    any id from the new block is handed out, and the reverse order is what an
    implementation gets wrong by accident).
    """
    path = tmp_path / "msg_id.json"
    block = 4
    handed_out: list[int] = []
    for round_ in range(12):
        allocator = MsgIdAllocator(path, block=block)
        for _ in range(round_ % (2 * block + 1)):
            handed_out.append(allocator.next())
        del allocator                             # a crash: no close()
        on_disk = json.loads(path.read_text())["next_msg_id"]
        if handed_out:
            assert on_disk > max(handed_out), (
                f"round {round_}: persisted {on_disk} is not past every id "
                f"handed out (max {max(handed_out)})")

    assert len(set(handed_out)) == len(handed_out), "an id was reissued"
    assert handed_out == sorted(handed_out), "ids went backwards across a restart"
    assert len(handed_out) > 2 * block, "the run never crossed a block boundary"


@pytest.mark.parametrize("body, label", [
    ('{"next_msg_id": 99', "torn mid-write"),
    ("", "truncated to nothing"),
    ('{"next_msg_id": "abc"}', "not an integer"),
    ("{}", "missing its key"),
])
def test_an_unreadable_counter_is_treated_as_lost_not_as_fatal(tmp_path, body,
                                                               label):
    """D29 (§3.1) + §8.3: an unreadable `msg_id.json` must not strand a
    producer.

    Letting the parse error escape keeps the crash invariant -- nothing is
    issued, so nothing is reissued -- and pays for it with an availability
    failure that is strictly worse: `JSONDecodeError`/`ValueError`/`KeyError`
    out of `MsgIdAllocator.__init__`, through `_Producer.__init__` and out of
    `register()`, which §8.3 says raises only `ConfigError` and
    `RegistrationError`. Nothing an app can branch on, no producer, and a
    store full of durable msgs going nowhere until an operator deletes the
    counter by hand -- which lands on this same recovery anyway.

    So every unusable shape collapses to §3.1's lost-store answer: log loudly
    (§10.4) and start from a random base. The file is left on disk for
    inspection; the next write replaces it atomically.
    """
    stream = _capture()
    path = tmp_path / "msg_id.json"
    path.write_text(body, encoding="utf-8")

    allocator = MsgIdAllocator(path, block=10)

    assert allocator.next() > 0, f"counter {label}: no id was handed out"
    assert "msg_id_counter_unreadable" in stream.getvalue(), (
        "§10.4: losing the counter is an operator-visible event")
    assert json.loads(path.read_text())["next_msg_id"] > 0, (
        "the counter is rewritten with a usable value")


def test_a_lost_counter_never_reissues_an_id_the_store_still_holds(tmp_path):
    """D29 (§3.1): gaps are fine, **reuse is not** -- and that has to hold on
    the fallback path too.

    §3.1's random base answers a *lost* store. This store is not lost: its
    records survived and only the counter died, so a random base drawn from
    `[1, 10**6)` landing at or below a live record's id is an ordinary event,
    not a negligible one -- and the id it would hand out is one a durable
    record already wears. The allocator therefore raises the base past the
    highest msg_id on disk (`store.highest_record_id`).

    A **corrupt** record counts here, unlike everywhere else in the store: the
    cleaner hides one because it cannot act on it, but id 900_001 is spoken
    for whether or not anyone can parse the file wearing it.
    """
    store = MsgStore(tmp_path)
    store.put_pending(_rec(900_000))
    store.mark_sent(900_000)
    _corrupt(store._dirs["nacked"], 900_001)
    path = tmp_path / "msg_id.json"
    path.write_text('{"next_msg_id": 99', encoding="utf-8")

    handed_out = [MsgIdAllocator(path, block=10).next() for _ in range(3)]

    assert min(handed_out) > 900_001, (
        f"reissued an id the store still holds: {handed_out}")


def test_a_counter_that_reads_below_the_store_is_raised_past_it(tmp_path):
    """The same floor on the *normal* path (D29, §3.1).

    A counter that parses cleanly can still be wrong -- rolled back by a
    restore from an older snapshot, or a store directory copied over a stale
    `msg_id.json`. Nothing about it being readable makes handing out an id a
    live record already wears any safer, so the floor is unconditional.

    The floor is a floor, never the source: §3.1 rejects *deriving* the
    counter from max msg_id, because a store that has ACKed and purged
    everything is empty and would restart the counter low. Pinned below.
    """
    store = MsgStore(tmp_path)
    store.put_pending(_rec(5_000))
    path = tmp_path / "msg_id.json"
    path.write_text(json.dumps({"next_msg_id": 12}), encoding="utf-8")

    assert MsgIdAllocator(path, block=10).next() == 5_001


@pytest.mark.parametrize("live_ids, label", [
    ((5, 6), "a store whose records are all older than the counter"),
    ((), "a store that has ACKed and purged everything"),
])
def test_the_store_floor_never_lowers_a_healthy_counter(tmp_path, live_ids,
                                                        label):
    """§3.1's rejected alternative, pinned so nobody re-adopts it.

    "Derive the counter by scanning the store for max msg_id" breaks once
    everything is ACKed and purged: the directory empties and the counter
    restarts low, and server-side dedup then falsely rejects genuinely new
    msgs. The scan here is a **floor**, so it may only ever raise the counter.

    Both directions of that have to be pinned, and the empty store alone does
    not do it -- it never reaches the branch. The first case is the one that
    catches a floor written as an assignment instead of a maximum: the store
    is populated, so `highest_record_id` returns a number, and it is *below*
    a perfectly healthy counter. Taking it would reissue ids 700_000 onward.
    """
    store = MsgStore(tmp_path)
    for msg_id in live_ids:
        store.put_pending(_rec(msg_id))
    path = tmp_path / "msg_id.json"
    path.write_text(json.dumps({"next_msg_id": 700_000}), encoding="utf-8")

    assert MsgIdAllocator(path, block=10).next() == 700_000, label


def test_a_crash_at_every_offset_of_the_counter_write_never_tears_the_file(
        tmp_path, monkeypatch):
    """D30 applied to `msg_id.json` -- the fix for the torn counter above.

    The counter goes down through `durable.write_atomic`, so a crash at any
    single instruction of `write tmp -> fsync tmp -> rename -> fsync dir`
    leaves the *previous* value published, never a half-written one: the real
    name only ever changes by `os.replace`. Eight offsets, one per os call in
    the sequence, each one killed at both a block-boundary reservation and a
    `close()` write-back -- the two writes that hit a live counter file.

    The `.tmp` a crash leaves behind is expected and inert; it is not the
    counter's name, so nothing reads it.

    Sister to `test_no_crash_between_reserving_and_using_a_block_ever_reissues
    _an_id`, which crashes *between* writes; this one crashes *inside* one.

    The injection counts os calls that touch the counter file, its `.tmp` or
    its directory -- by path, and by the fds opened on them -- rather than
    wrapping `write_atomic`. Deliberate: a wrapper keyed on the helper's name
    goes quiet the moment an implementation stops calling it, which is exactly
    the regression (an in-place `O_TRUNC` write) this test exists to catch. It
    was verified to fail against that mutant.
    """
    path = tmp_path / "msg_id.json"
    block = 4
    handed_out: list[int] = []
    targets = {path, path.with_name(path.name + ".tmp"), tmp_path}
    state = {"n": 0, "offset": 0}
    tracked_fds: set[int] = set()

    real_os = {name: getattr(os, name)
               for name in ("open", "write", "fsync", "close", "replace")}

    def _tick(hit: bool) -> None:
        if not hit:
            return
        state["n"] += 1
        if state["n"] == state["offset"]:
            raise OSError(28, "injected: no space left on device")

    def fake_open(p, *a, **kw):
        hit = Path(p) in targets
        _tick(hit)
        fd = real_os["open"](p, *a, **kw)
        if hit:
            tracked_fds.add(fd)
        return fd

    def fake_close(fd, *a, **kw):
        _tick(fd in tracked_fds)
        tracked_fds.discard(fd)
        return real_os["close"](fd, *a, **kw)

    def fake_replace(src, dst, *a, **kw):
        _tick(Path(src) in targets or Path(dst) in targets)
        return real_os["replace"](src, dst, *a, **kw)

    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "close", fake_close)
    monkeypatch.setattr(os, "replace", fake_replace)
    for name in ("write", "fsync"):
        def fake(fd, *a, _name=name, **kw):
            _tick(fd in tracked_fds)
            return real_os[_name](fd, *a, **kw)
        monkeypatch.setattr(os, name, fake)

    fired = 0
    for offset in range(1, 9):
        for victim in ("reserve", "close"):
            allocator = MsgIdAllocator(path, block=block)   # unarmed: offset 0
            handed_out.extend(allocator.next() for _ in range(block))

            state["offset"], state["n"] = offset, 0
            try:
                if victim == "reserve":
                    # Crosses the boundary: the reservation write runs first,
                    # so a failure here must hand out nothing.
                    handed_out.append(allocator.next())
                else:
                    allocator.close()
            except OSError:
                fired += 1
            finally:
                state["offset"] = 0
            del allocator                          # a crash: no clean close

            on_disk = json.loads(path.read_text(encoding="utf-8"))
            assert on_disk["next_msg_id"] > max(handed_out), (
                f"offset {offset}/{victim}: counter {on_disk} is not past "
                f"every id handed out (max {max(handed_out)})")

    assert fired == 16, (
        f"the injected crash fired {fired} times, not once per round -- the\n"
        "sequence has fewer steps than the offsets being tested")
    assert len(set(handed_out)) == len(handed_out), "an id was reissued"
    assert handed_out == sorted(handed_out), "ids went backwards across a restart"
    assert len(handed_out) > 4 * block, "the run never crossed a block boundary"


def test_a_torn_counter_no_longer_strands_a_producer_that_still_has_msgs(
        tmp_path):
    """The operational consequence, now the other way round (D29, §3.1, §2.3).

    The store survived the crash exactly as designed -- the record is in
    `pending/`, durable, waiting -- and `register()` now brings the producer
    back up and ships it, instead of raising a `JSONDecodeError` §8.3 never
    listed for that call and waiting for an operator to delete a file.

    The resumed producer must also not reissue the id that surviving record
    wears: the store floor is what guarantees that end to end, not just in the
    allocator's own unit test.
    """
    server = MockServer()
    cfg = write_cfg(tmp_path)
    clnt.clnt_init(cfg)
    pid = clnt.register({"host": "x"},
                        _transport=server.transport())["producer_id"]
    store_dir = clnt._producer_for_tests(pid).store_dir
    msg_id = clnt.send(pid, {"x": 1}).msg_id
    clnt._store_for_tests(pid).demote_to_pending(msg_id)
    clnt._reset_for_tests()
    (store_dir / "msg_id.json").write_text('{"next_msg_id": 99',
                                           encoding="utf-8")

    clnt.clnt_init(cfg)
    pid = clnt.register({"host": "x"},
                        _transport=server.transport())["producer_id"]

    assert wait_until(lambda: msg_id in [e.msg_id for e in server.envelopes[1:]]), (
        "the stranded msg is resent once the producer can start again")
    assert clnt.send(pid, {"x": 2}).msg_id > msg_id, (
        "and the resumed producer does not reissue the surviving record's id")


# -- a failing disk mid-transition (§8.3, §5.1) ---------------------------


def test_a_disk_failure_marking_sent_hands_the_slot_back_and_keeps_the_record(
        tmp_path, monkeypatch):
    """§5.1 + §8.3: the POST landed, the `pending/` -> `sent/` rename did not.

    Two things must hold and they pull in opposite directions. The window slot
    has to go back, because nothing this client can account for is in flight --
    a slot leaked per failure silently shrinks the window until nothing ships.
    And the record has to stay, because the msg is not resolved -- losing it
    here would be an at-least-once library dropping a msg on a disk hiccup. The
    duplicate the later resend produces is at-least-once behaving as specified
    (§9.1: the app dedupes).
    """
    server = MockServer()
    pid = _registered(tmp_path, server)
    producer = clnt._producer_for_tests(pid)
    store = clnt._store_for_tests(pid)
    monkeypatch.setattr(store_mod.os, "replace", _replace_failing_into("sent"))

    result = clnt.send(pid, {"x": 1})

    assert result.err_code == Code.OK             # the POST really did land
    assert store.list("pending") == [result.msg_id], "the record is not lost"
    assert store.list("sent") == []
    assert producer.window.in_flight() == 0, "no slot is left holding nothing"


def test_a_disk_failure_between_the_two_nack_hops_keeps_the_slot_and_the_record(
        tmp_path, monkeypatch):
    """§5.1 + §8.3: a 400 moves the record `pending/` -> `sent/` -> `nacked/`,
    and the second hop fails.

    The record is genuinely in `sent/` now -- in flight, by the only definition
    the window has -- so the slot **stays taken**, and `sent_timeout_s` is what
    eventually releases it. Releasing here as well would be a release with no
    acquire, on the one path (a failing disk) guaranteed to repeat, and the
    window would stop bounding anything. Nothing is lost: the record is still
    on disk and the demotion resends it.
    """
    server = MockServer(event_status=400)
    pid = _registered(tmp_path, server)
    producer = clnt._producer_for_tests(pid)
    store = clnt._store_for_tests(pid)
    monkeypatch.setattr(store_mod.os, "replace", _replace_failing_into("nacked"))

    result = clnt.send(pid, {"x": 1})

    assert result.err_code == Code.REJECTED
    assert store.list("sent") == [result.msg_id], "the first hop landed"
    assert store.list("nacked") == []
    assert producer.window.in_flight() == 1, (
        "the record IS in flight; only the demotion may hand this slot back")

    monkeypatch.undo()
    producer.cleaner._sent_timeout_s = 0.0        # the backstop, immediately
    producer.cleaner.sweep_once()
    assert producer.window.in_flight() <= 1
    assert store.get(result.msg_id) is not None


def test_a_disk_failure_storing_the_msg_never_reports_it_stored(tmp_path,
                                                                monkeypatch):
    """§8.3 + §2.2: `put_pending` is the at-least-once promise being made.

    If it fails there is no promise to keep, and every `SendResult` this
    library can return says the opposite -- `ok` and the four `stored_*` codes
    all mean "durable, we own it now". So the failure surfaces to the caller
    rather than being flattened into a code that would be a lie, and the caller
    still holds the event. Pinned because a well-meaning `try/except OSError`
    around this call would turn a disk that stopped accepting writes into a
    producer reporting `stored_unreachable` for msgs that no longer exist.
    """
    server = MockServer()
    pid = _registered(tmp_path, server)
    producer = clnt._producer_for_tests(pid)
    store = clnt._store_for_tests(pid)
    monkeypatch.setattr(store_mod.os, "replace",
                        _replace_failing_into("pending"))

    with pytest.raises(OSError):
        clnt.send(pid, {"x": 1})

    monkeypatch.undo()
    assert store.total() == 0, "nothing half-written is visible as a record"
    assert producer.window.in_flight() == 0, "no slot was taken for a msg that "\
                                             "was never stored"
    assert server.calls.get("event") is None, "and nothing went on the wire"
