"""Tests for the client facade (design §6.1) -- the whole public surface.

This is the layer every app touches, so the tests here are written against the
*contract* rather than the implementation: the two limits that must never be
conflated (`max_pending` rejects, `max_in_flight` throttles), the two states a
`resolve()` can leave a record in, what `close()` is allowed to call
"drained", and the four §8.3 conditions that raise instead of returning a code.

Nothing here needs a server: `conftest.MockServer` answers all four §5 paths
over an `httpx.MockTransport`, and every assertion about the wire reads the
envelopes it captured.
"""
import io
import json
import subprocess
import sys
import threading
import time

import httpx
import pytest
import structlog

from common.msging import clnt, errors as clnt_errors, metrics as metrics_mod
from common.msging.clnt import transport as transport_mod
from common.msging.codes import Action, Code
from common.msging.errors import (
    ConfigError,
    InvalidEnvelopeError,
    NoCallbackError,
    NotRegisteredError,
    ProducerClosingError,
)
from common.msging.log import configure_logging
from common.msging.models import Envelope, MsgRecord, NotifyEv, SendResult

from .conftest import (
    API_KEY,
    CountingTransport,
    MockServer,
    wait_until,
    write_cfg,
    write_loop_cfg,
)


# -- the brief's fifteen -------------------------------------------------


def test_send_before_register_raises(tmp_path, cfg_file):
    clnt.clnt_init(cfg_file)
    with pytest.raises(NotRegisteredError):
        clnt.send("nope", {"x": 1})


def test_dedup_without_event_id_raises(registered):
    pid = registered
    with pytest.raises(InvalidEnvelopeError):
        clnt.send(pid, {"x": 1}, dedup=True)


def test_send_after_close_raises(registered):
    pid = registered
    clnt.close(pid, timeout_s=0.1)
    with pytest.raises(ProducerClosingError):
        clnt.send(pid, {"x": 1})


def test_info_is_shallow_merged_per_msg_winning(registered, sent_envelopes):
    pid = registered
    clnt.set_info(pid, {"agent": "1.0", "host": "laptop"})
    clnt.send(pid, {"x": 1}, info={"agent": "2.0"})
    info = sent_envelopes[-1].info
    assert info == {"agent": "2.0", "host": "laptop"}


def test_send_returns_ok_on_202(registered):
    r = clnt.send(registered, {"x": 1})
    assert r.err_code == Code.OK and r.msg_id is not None


def test_send_returns_slot_full_and_does_not_store(registered_full_store):
    pid, store = registered_full_store
    before = store.total()
    r = clnt.send(pid, {"x": 1})
    assert r.err_code == Code.SLOT_FULL
    assert store.total() == before          # NOT stored -- the one hard rejection


def test_too_large_is_checked_before_storing(registered_tiny_limit):
    pid, store = registered_tiny_limit
    r = clnt.send(pid, {"blob": "x" * 5000})
    assert r.err_code == Code.TOO_LARGE
    assert store.total() == 0


def test_resolve_purge_deletes_the_record(nacked):
    pid, msg_id, store = nacked
    clnt.resolve(pid, msg_id, Action.PURGE)
    assert store.get(msg_id) is None


def test_resolve_retain_returns_it_to_pending(nacked):
    pid, msg_id, store = nacked
    clnt.resolve(pid, msg_id, Action.RETAIN)
    assert msg_id in store.list("pending")


def test_resolve_defaults_to_retain(nacked):
    pid, msg_id, store = nacked
    clnt.resolve(pid, msg_id)
    assert msg_id in store.list("pending")


def test_close_reports_drained_when_empty(registered):
    result = clnt.close(registered, timeout_s=1.0)
    assert result.status == "drained" and result.unresolved == 0


def test_ack_callback_suppressed_by_default(registered_with_callback):
    pid, seen = registered_with_callback
    clnt._deliver_for_tests(pid, code="ack", msg_id=1)
    assert seen == []


def test_ack_callback_fires_when_opted_in(registered_with_ack_callback):
    pid, seen = registered_with_ack_callback
    clnt._deliver_for_tests(pid, code="ack", msg_id=1)
    assert [e.code for e in seen] == ["ack"]


def test_nack_callback_always_fires(registered_with_callback):
    pid, seen = registered_with_callback
    clnt._deliver_for_tests(pid, code="nack", msg_id=1, reason="bad")
    assert [e.code for e in seen] == ["nack"]


# -- register / clnt_init (§6.1 item 3, §3 stale binding) ----------------


def test_register_without_clnt_init_raises(tmp_path):
    with pytest.raises(ConfigError):
        clnt.register({"host": "x"})


def test_register_on_an_unknown_alias_raises(cfg_file):
    clnt.clnt_init(cfg_file)
    with pytest.raises(ConfigError):
        clnt.register({"host": "x"}, server_alias="nope")


def test_register_on_a_disabled_alias_raises(cfg_file, mock_server):
    clnt.clnt_init(cfg_file)
    with pytest.raises(ConfigError):
        clnt.register({"host": "x"}, server_alias="off_srv",
                      _transport=mock_server.transport())


def test_register_returns_the_servers_limits(cfg_file):
    server = MockServer(max_msg_bytes=4242)
    clnt.clnt_init(cfg_file)
    p = clnt.register({"host": "x"}, _transport=server.transport())
    assert p["max_msg_bytes"] == 4242
    assert p["producer_id"] and p["api_key"] == API_KEY


def test_register_twice_in_one_process_does_not_re_register(registered,
                                                            mock_server):
    again = clnt.register({"host": "laptop-7"},
                          _transport=mock_server.transport())
    assert again["producer_id"] == registered
    assert mock_server.calls["register"] == 1


def test_a_restart_reuses_the_stored_identity(tmp_path, mock_server):
    """§3: startup rebuilds `producer_id -> server` from `identity.json`. A
    fresh producer_id every restart would orphan the previous run's backlog."""
    cfg = write_cfg(tmp_path)
    clnt.clnt_init(cfg)
    first = clnt.register({"host": "x"}, _transport=mock_server.transport())
    clnt._reset_for_tests()               # a "restart": disk survives, memory does not

    clnt.clnt_init(cfg)
    second = clnt.register({"host": "x"}, _transport=mock_server.transport())
    assert second["producer_id"] == first["producer_id"]
    assert mock_server.calls["register"] == 1
    assert second["max_msg_bytes"] == first["max_msg_bytes"]


def test_a_repointed_alias_registers_fresh(tmp_path, mock_server):
    """§3 stale binding: stored base_path != configured -> never silently
    reuse a producer_id/api_key that is worthless against the new endpoint."""
    clnt.clnt_init(write_cfg(tmp_path))
    first = clnt.register({"host": "x"}, _transport=mock_server.transport())
    clnt._reset_for_tests()

    moved = (tmp_path / "moved.yaml")
    moved.write_text(
        (tmp_path / "msging.yaml").read_text(encoding="utf-8")
        .replace("https://ingest.test:8443", "https://elsewhere.test:8443"),
        encoding="utf-8")
    server2 = MockServer(id_prefix="prod-moved")
    clnt.clnt_init(str(moved))
    second = clnt.register({"host": "x"}, _transport=server2.transport())
    assert server2.calls["register"] == 1
    assert second["base_path"] == "https://elsewhere.test:8443"
    assert second["producer_id"] != first["producer_id"]
    # The old directory is left for inspection -- it may hold undelivered msgs.
    assert (tmp_path / "store" / "test_srv" / first["producer_id"]).is_dir()


def test_startup_folds_nacked_and_sent_back_into_pending(tmp_path, mock_server):
    """§2.3: a leftover NACKED means the app's decision never committed, and a
    leftover SENT is resent immediately rather than waiting `sent_timeout_s`
    (§2.4). Both are the startup pass, not the steady-state rule (§4.4)."""
    cfg = write_cfg(tmp_path)
    clnt.clnt_init(cfg)
    pid = clnt.register({"host": "x"},
                        _transport=mock_server.transport())["producer_id"]
    nack_me = clnt.send(pid, {"a": 1}).msg_id
    in_flight = clnt.send(pid, {"b": 2}).msg_id
    clnt._deliver_for_tests(pid, code="nack", msg_id=nack_me, reason="bad")
    store = clnt._store_for_tests(pid)
    assert store.list("nacked") == [nack_me] and store.list("sent") == [in_flight]

    clnt._reset_for_tests()
    clnt.clnt_init(cfg)
    clnt.register({"host": "x"}, _transport=mock_server.transport())
    store = clnt._store_for_tests(pid)
    assert sorted(store.list("pending")) == sorted([nack_me, in_flight])
    assert store.list("nacked") == [] and store.list("sent") == []


# -- send: the two limits (§5.1) -----------------------------------------


def test_a_full_window_throttles_and_never_fails_a_send(tmp_path, mock_server):
    """§5.1: `max_in_flight` full -> stored, `stored_throttled`, ships later.
    It must not reject, and it must not even attempt the POST."""
    pid = _registered(tmp_path, mock_server, max_in_flight=1)
    first = clnt.send(pid, {"a": 1})
    second = clnt.send(pid, {"b": 2})
    store = clnt._store_for_tests(pid)
    assert first.err_code == Code.OK
    assert second.err_code == Code.STORED_THROTTLED
    assert second.msg_id in store.list("pending")     # stored, unlike slot_full
    assert mock_server.calls["event"] == 1


def test_slot_full_and_stored_throttled_are_different_conditions(tmp_path):
    """The recurring mix-up: one bounds the store and rejects, the other
    bounds the wire and throttles. Same producer, both limits, one test."""
    server = MockServer()
    pid = _registered(tmp_path, server, max_pending=1, max_in_flight=1)
    ok = clnt.send(pid, {"a": 1})
    full = clnt.send(pid, {"b": 2})
    assert ok.err_code == Code.OK
    # The store is at max_pending *and* the window is full; the store limit is
    # the one that rejects, and it rejects before anything is written.
    assert full.err_code == Code.SLOT_FULL
    assert full.msg_id is None
    assert clnt._store_for_tests(pid).total() == 1


def test_slot_full_waits_slot_wait_s_before_rejecting(tmp_path, mock_server):
    pid = _registered(tmp_path, mock_server, max_pending=1, slot_wait_s=0.3)
    clnt.send(pid, {"a": 1})
    started = time.monotonic()
    assert clnt.send(pid, {"b": 2}).err_code == Code.SLOT_FULL
    assert time.monotonic() - started >= 0.3


def test_a_slot_freed_while_waiting_lets_the_send_through(tmp_path, mock_server):
    """`slot_wait_s` is a wait, not a formality: a slot that frees inside the
    window must be taken rather than rejected on a stale first look."""
    pid = _registered(tmp_path, mock_server, max_pending=1, slot_wait_s=2.0)
    first = clnt.send(pid, {"a": 1}).msg_id

    def _free():
        time.sleep(0.2)
        clnt._deliver_for_tests(pid, code="ack", msg_id=first)

    freer = threading.Thread(target=_free, daemon=True)
    freer.start()
    assert clnt.send(pid, {"b": 2}).err_code == Code.OK
    freer.join(2)


def test_too_large_is_measured_against_the_registered_limit(tmp_path):
    """§8.1: client-side, against what `register()` returned. Otherwise an
    oversized msg lands in `pending/` and is retried forever."""
    # 400 bytes: comfortably over the ~200-byte envelope §7 wraps a small
    # payload in, and far under the 5 KB one below.
    server = MockServer(max_msg_bytes=400)
    pid = _registered(tmp_path, server)
    assert clnt.send(pid, {"blob": "x" * 5000}).err_code == Code.TOO_LARGE
    assert clnt.send(pid, {"s": "ok"}).err_code == Code.OK
    assert server.calls["event"] == 1          # the big one never went out


def test_send_makes_exactly_one_post_attempt(tmp_path):
    """§2.2. Retry belongs to the cleaner loop; a 503 here is one POST."""
    server = MockServer(event_status=503)
    pid = _registered(tmp_path, server)
    assert clnt.send(pid, {"x": 1}).err_code == Code.SERVER_BUSY
    assert server.calls["event"] == 1


def test_a_timeout_leaves_the_record_pending(tmp_path):
    server = MockServer(event_error=lambda: httpx.ConnectTimeout("mock"))
    pid = _registered(tmp_path, server)
    r = clnt.send(pid, {"x": 1})
    assert r.err_code == Code.STORED_TIMEOUT
    assert r.msg_id in clnt._store_for_tests(pid).list("pending")


def test_an_unreachable_server_leaves_the_record_pending(tmp_path):
    server = MockServer(event_error=lambda: httpx.ConnectError("mock"))
    pid = _registered(tmp_path, server)
    r = clnt.send(pid, {"x": 1})
    assert r.err_code == Code.STORED_UNREACHABLE
    assert r.msg_id in clnt._store_for_tests(pid).list("pending")


def test_a_400_nacks_the_record_so_the_app_can_resolve_it(tmp_path):
    """§8.1: `rejected` -> NACKED -> "fix + resend, or purge", which is what
    `resolve()` is for. Left PENDING it would be retried forever."""
    server = MockServer(event_status=400)
    pid = _registered(tmp_path, server)
    r = clnt.send(pid, {"x": 1})
    assert r.err_code == Code.REJECTED
    assert r.msg_id in clnt._store_for_tests(pid).list("nacked")


def test_a_server_413_stores_nothing(tmp_path):
    """The server's 413 backstops a stale client-side limit (§8.1). Whatever
    the client thought, a `too_large` return must mean nothing was stored."""
    server = MockServer(event_status=413)
    pid = _registered(tmp_path, server)
    r = clnt.send(pid, {"x": 1})
    assert r.err_code == Code.TOO_LARGE
    assert clnt._store_for_tests(pid).total() == 0


def test_a_401_leaves_the_record_pending(tmp_path):
    server = MockServer(event_status=401)
    pid = _registered(tmp_path, server)
    r = clnt.send(pid, {"x": 1})
    assert r.err_code == Code.UNAUTHORIZED
    assert r.msg_id in clnt._store_for_tests(pid).list("pending")


def test_send_returns_the_models_result_not_the_transport_tuple():
    """The two `SendResult`s are different types with different fields. The
    one §6.1 hands the app is the model."""
    assert SendResult is not transport_mod.SendResult
    assert set(SendResult.model_fields) == {"err_code", "reason", "msg_id"}


def test_send_result_type_is_the_model(registered):
    r = clnt.send(registered, {"x": 1})
    assert isinstance(r, SendResult)
    assert not isinstance(r, tuple)


def test_a_non_dict_event_raises(registered):
    with pytest.raises(InvalidEnvelopeError):
        clnt.send(registered, ["not", "a", "dict"])


def test_dedup_with_an_event_id_is_carried_on_the_envelope(registered,
                                                           sent_envelopes):
    clnt.send(registered, {"x": 1}, event_id="sess-1", dedup=True)
    env = sent_envelopes[-1]
    assert env.dedup is True and env.event_id == "sess-1"


def test_the_envelope_carries_the_seven_shape(registered, sent_envelopes):
    clnt.send(registered, {"x": 1})
    env = sent_envelopes[-1]
    assert env.schema_version == "1.0"
    assert env.producer_id == registered
    assert env.service_name == "ingest"
    assert env.created_at and env.msg_id > 0
    assert env.payload == {"x": 1}


def test_msg_ids_are_monotonic(registered):
    ids = [clnt.send(registered, {"i": i}).msg_id for i in range(3)]
    assert ids == sorted(ids) and len(set(ids)) == 3


# -- set_info (§6.1 item 4, §7) ------------------------------------------


def test_set_info_replaces_wholesale_and_affects_later_sends_only(
        registered, sent_envelopes):
    clnt.set_info(registered, {"a": 1, "b": 2})
    clnt.send(registered, {"x": 1})
    clnt.set_info(registered, {"c": 3})
    clnt.send(registered, {"x": 2})
    assert sent_envelopes[0].info == {"a": 1, "b": 2}
    assert sent_envelopes[1].info == {"c": 3}


def test_a_per_msg_info_does_not_leak_into_the_next_send(registered,
                                                         sent_envelopes):
    clnt.set_info(registered, {"agent": "1.0"})
    clnt.send(registered, {"x": 1}, info={"once": True})
    clnt.send(registered, {"x": 2})
    assert sent_envelopes[0].info == {"agent": "1.0", "once": True}
    assert sent_envelopes[1].info == {"agent": "1.0"}


def test_mutating_the_dict_passed_to_set_info_does_not_change_the_producer(
        registered, sent_envelopes):
    bag = {"agent": "1.0"}
    clnt.set_info(registered, bag)
    bag["agent"] = "tampered"
    clnt.send(registered, {"x": 1})
    assert sent_envelopes[-1].info == {"agent": "1.0"}


# -- the notification path (§2.2, §4.1) ----------------------------------


def test_register_does_not_open_the_sse_channel(registered):
    """§4.1: the callback is required *before* the channel opens, so nothing
    before `on_notify()` may open one."""
    assert clnt._sse_for_tests(registered) is None
    clnt.on_notify(registered, lambda msg_id, ev: None)
    assert clnt._sse_for_tests(registered) is not None


def test_on_notify_without_a_callable_raises(registered):
    with pytest.raises(NoCallbackError):
        clnt.on_notify(registered, None)
    assert clnt._sse_for_tests(registered) is None


def test_on_notify_before_register_raises(cfg_file):
    clnt.clnt_init(cfg_file)
    with pytest.raises(NotRegisteredError):
        clnt.on_notify("nope", lambda msg_id, ev: None)


def test_an_ack_purges_the_record(registered):
    msg_id = clnt.send(registered, {"x": 1}).msg_id
    clnt._deliver_for_tests(registered, code="ack", msg_id=msg_id)
    assert clnt._store_for_tests(registered).get(msg_id) is None


def test_the_record_is_already_purged_when_the_ack_callback_runs(registered):
    """§2.2: "Purge happens before the callback." An app that lists the store
    from its callback must not see a msg it was just told is done."""
    store = clnt._store_for_tests(registered)
    seen = []
    clnt.on_notify(registered, lambda msg_id, ev: seen.append(store.get(msg_id)),
                   notify_on_ack=True)
    msg_id = clnt.send(registered, {"x": 1}).msg_id
    clnt._deliver_for_tests(registered, code="ack", msg_id=msg_id)
    assert seen == [None]


def test_a_nack_marks_the_record_and_persists_the_reason(registered):
    msg_id = clnt.send(registered, {"x": 1}).msg_id
    clnt._deliver_for_tests(registered, code="nack", msg_id=msg_id,
                            reason="schema drift")
    rec = clnt._store_for_tests(registered).get(msg_id)
    assert msg_id in clnt._store_for_tests(registered).list("nacked")
    assert rec.nack_reason == "schema drift"


def test_a_notice_for_an_unknown_msg_does_not_raise(registered_with_callback):
    """A duplicate outcome after the record was purged is normal traffic on an
    at-least-once channel, and it arrives on the reader thread."""
    pid, seen = registered_with_callback
    clnt._deliver_for_tests(pid, code="nack", msg_id=999_999, reason="late")
    assert [e.msg_id for e in seen] == [999_999]


def test_a_callback_exception_does_not_stop_later_notices(registered):
    seen = []

    def boom(msg_id, ev):
        seen.append(ev.msg_id)
        raise RuntimeError("app bug")

    clnt.on_notify(registered, boom)
    clnt._deliver_for_tests(registered, code="nack", msg_id=1)
    clnt._deliver_for_tests(registered, code="nack", msg_id=2)
    assert seen == [1, 2]


def test_a_callback_exception_message_is_never_logged(registered):
    out = _capture()
    clnt.on_notify(registered,
                   lambda msg_id, ev: (_ for _ in ()).throw(RuntimeError(_SECRET)))
    clnt._deliver_for_tests(registered, code="nack", msg_id=1)
    assert out.getvalue().strip(), "expected a log line"
    assert _SECRET not in out.getvalue()


def test_an_unknown_outcome_code_leaves_the_record_alone(registered):
    """§8.2: `code` is extensible, and a new one "must state whether the
    client purges or retains". Until it does, retain."""
    pid = registered
    seen = []
    clnt.on_notify(pid, lambda msg_id, ev: seen.append(ev.code))
    msg_id = clnt.send(pid, {"x": 1}).msg_id
    clnt._deliver_for_tests(pid, code="quarantined", msg_id=msg_id)
    assert msg_id in clnt._store_for_tests(pid).list("sent")
    assert seen == ["quarantined"]


def test_outcomes_arriving_over_the_stream_reach_the_app(tmp_path):
    """The one end-to-end check that the reader is wired to the outcome path:
    a real `event: notify` frame off the wire has to purge/mark the record and
    reach the callback, not just `_deliver_for_tests`."""
    server = MockServer()
    pid = _registered(tmp_path, server)
    msg_id = clnt.send(pid, {"x": 1}).msg_id
    notice = {"msg_id": msg_id, "producer_id": pid, "code": "nack",
              "reason": "no", "info": {}, "ts": "2026-07-25T10:15:31Z"}
    server.notify_frames = [f"event: notify\ndata: {json.dumps(notice)}\n\n"]

    got = threading.Event()
    seen = []
    clnt.on_notify(pid, lambda mid, ev: (seen.append(mid), got.set()))
    assert got.wait(5), "no outcome reached the callback"
    assert seen[0] == msg_id
    assert msg_id in clnt._store_for_tests(pid).list("nacked")


def test_an_unauthorized_stream_halts_instead_of_retrying_forever(tmp_path):
    """§8.1: `unauthorized` means re-register, which is the *app's* call --
    `/register` mints a new producer_id and would orphan this binding's
    backlog (§3). What this layer owes is a loud stop, not a reconnect loop
    against a key that will never work again."""
    server = MockServer(notify_status=401)
    pid = _registered(tmp_path, server)
    clnt.on_notify(pid, lambda msg_id, ev: None)
    producer = clnt._producer_for_tests(pid)
    reader = clnt._sse_for_tests(pid)
    for _ in range(500):
        if producer.unauthorized and not reader._thread.is_alive():
            break
        time.sleep(0.01)
    assert producer.unauthorized
    assert not reader._thread.is_alive(), "a revoked key must not be retried"
    assert server.calls["notify"] == 1


def test_on_notify_twice_does_not_open_a_second_reader(registered):
    """Two readers on one binding is two copies of every outcome."""
    clnt.on_notify(registered, lambda msg_id, ev: None)
    first = clnt._sse_for_tests(registered)
    clnt.on_notify(registered, lambda msg_id, ev: None, notify_on_ack=True)
    assert clnt._sse_for_tests(registered) is first
    live = [t for t in threading.enumerate()
            if t.name == f"msging-sse-{registered}"]
    assert len(live) == 1


def test_a_401_on_send_flags_the_producer(tmp_path):
    server = MockServer(event_status=401)
    pid = _registered(tmp_path, server)
    clnt.send(pid, {"x": 1})
    assert clnt._producer_for_tests(pid).unauthorized is True


def test_poll_notify_drains_the_queue(registered_with_callback):
    pid, _ = registered_with_callback
    clnt._deliver_for_tests(pid, code="nack", msg_id=1)
    clnt._deliver_for_tests(pid, code="nack", msg_id=2)
    first = clnt.poll_notify(pid)
    assert [e.msg_id for e in first] == [1, 2]
    assert clnt.poll_notify(pid) == []
    assert all(isinstance(e, NotifyEv) for e in first)


def test_poll_notify_honours_notify_on_ack(registered_with_callback):
    pid, _ = registered_with_callback
    clnt._deliver_for_tests(pid, code="ack", msg_id=1)
    clnt._deliver_for_tests(pid, code="nack", msg_id=2)
    assert [e.msg_id for e in clnt.poll_notify(pid)] == [2]


# -- resolve (§2.2 step 4) -----------------------------------------------


def test_resolve_on_an_unknown_msg_id_is_a_no_op(registered):
    clnt.resolve(registered, 123456, Action.PURGE)
    clnt.resolve(registered, 123456, Action.RETAIN)


def test_retain_does_not_resend_inline(nacked, mock_server):
    """Retry belongs to the cleaner loop (§2.2/§4.4), not to `resolve()`."""
    pid, msg_id, _ = nacked
    before = mock_server.calls["event"]
    clnt.resolve(pid, msg_id, Action.RETAIN)
    assert mock_server.calls["event"] == before


def test_flush_ships_what_retain_returned_to_pending(nacked, mock_server):
    pid, msg_id, store = nacked
    clnt.resolve(pid, msg_id, Action.RETAIN)
    clnt.flush(pid)
    assert msg_id in store.list("sent")


def test_flush_before_register_raises(cfg_file):
    clnt.clnt_init(cfg_file)
    with pytest.raises(NotRegisteredError):
        clnt.flush("nope")


# -- close (§2.4) ---------------------------------------------------------


def test_close_drains_pending_and_posts_close(tmp_path, mock_server):
    pid = _registered(tmp_path, mock_server)
    msg_id = clnt.send(pid, {"x": 1}).msg_id
    clnt._deliver_for_tests(pid, code="ack", msg_id=msg_id)
    result = clnt.close(pid, timeout_s=1.0)
    assert result.status == "drained" and result.unresolved == 0
    assert mock_server.calls["close"] == 1


def test_close_reports_timeout_with_the_unresolved_count(tmp_path):
    """A msg the server will not take stays durable; close() must say so
    rather than claim a clean drain (§2.4)."""
    server = MockServer(event_status=503)
    pid = _registered(tmp_path, server)
    clnt.send(pid, {"x": 1})
    result = clnt.close(pid, timeout_s=0.3)
    assert result.status == "timeout" and result.unresolved == 1
    assert clnt._store_for_tests(pid).count("pending") == 1


def test_close_counts_sent_as_unresolved(registered):
    """`sent/` is half the outstanding set (§2.4): a msg whose outcome never
    arrived is not drained."""
    clnt.send(registered, {"x": 1})
    result = clnt.close(registered, timeout_s=0.3)
    assert result.status == "timeout" and result.unresolved == 1


def test_close_flushes_pending_before_giving_up(tmp_path, mock_server):
    """Step 2 of §2.4 is a flush, not a wait: a msg stored but never shipped
    (a throttled one, here) has to go out during the drain."""
    pid = _registered(tmp_path, mock_server, max_in_flight=1)
    first = clnt.send(pid, {"a": 1}).msg_id
    throttled = clnt.send(pid, {"b": 2}).msg_id
    assert throttled in clnt._store_for_tests(pid).list("pending")
    clnt._deliver_for_tests(pid, code="ack", msg_id=first)
    clnt.close(pid, timeout_s=1.0)
    assert throttled in clnt._store_for_tests(pid).list("sent")
    assert mock_server.calls["event"] == 2


def test_close_stops_the_background_threads(registered):
    clnt.on_notify(registered, lambda msg_id, ev: None)
    clnt.close(registered, timeout_s=0.5)
    assert not clnt._sse_for_tests(registered).connected
    live = [t for t in threading.enumerate()
            if t.name.startswith(f"msging-sse-{registered}")]
    assert live == []


def test_close_is_idempotent(registered):
    first = clnt.close(registered, timeout_s=0.5)
    second = clnt.close(registered, timeout_s=0.5)
    assert first.status == second.status == "drained"


def test_close_before_register_raises(cfg_file):
    clnt.clnt_init(cfg_file)
    with pytest.raises(NotRegisteredError):
        clnt.close("nope")


def test_close_defaults_to_close_timeout_s(tmp_path):
    server = MockServer(event_status=503)
    pid = _registered(tmp_path, server, close_timeout_s=0.3)
    clnt.send(pid, {"x": 1})
    started = time.monotonic()
    assert clnt.close(pid).status == "timeout"
    elapsed = time.monotonic() - started
    assert 0.3 <= elapsed < 3.0


# -- logging and metrics (§10.4, §10.5) ----------------------------------


_SECRET = "s3cr3t-user-data-do-not-log"


@pytest.fixture(autouse=True)
def _structlog_config_is_per_test():
    """`configure_logging()` writes structlog's *global* config, so a test that
    captures log output was redirecting every later test's logging into a
    dead StringIO -- including tests in other files, depending on order. Save
    and restore it."""
    saved = structlog.get_config()
    yield
    structlog.configure(**saved)


def _capture() -> io.StringIO:
    out = io.StringIO()
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=out))
    return out


def test_the_api_key_and_the_payload_are_never_logged(tmp_path, mock_server):
    out = _capture()
    pid = _registered(tmp_path, mock_server)
    clnt.set_info(pid, {"note": _SECRET})
    clnt.send(pid, {"blob": _SECRET})
    clnt.close(pid, timeout_s=0.5)
    text = out.getvalue()
    assert text.strip(), "expected log lines"
    assert API_KEY not in text
    assert _SECRET not in text


def test_no_log_line_echoes_the_info_bag_or_the_payload(tmp_path):
    """§10.4 covers the *rejection* paths too, and those are the ones with the
    most tempting context to dump: the msg that was too large, the msg there
    was no room for, and the outcome that said no.

    Every rejected send here carries the secret in **both** legs -- the `info`
    bag and the payload -- because a test that drives `slot_full` with
    `{"k": "v"}` proves only that the `info` bag stays out of that line. It
    would pass with `payload=event` added to it, which is exactly the leak
    §10.4 forbids.
    """
    out = _capture()
    # Big enough that the two small sends fit (they carry the secret and must
    # reach `slot_full`, not `too_large`), small enough that the 1 KB one does
    # not.
    server = MockServer(max_msg_bytes=500)
    clnt.clnt_init(write_cfg(tmp_path, max_pending=1, slot_wait_s=0.01))
    pid = clnt.register({"host": "x"},
                        _transport=server.transport())["producer_id"]
    clnt.set_info(pid, {"note": _SECRET})
    assert clnt.send(pid, {"blob": _SECRET * 40}).err_code == Code.TOO_LARGE
    msg_id = clnt.send(pid, {"k": "v"}).msg_id                  # fills the store
    # The rejected send's own payload is secret too: `slot_full` is logged with
    # the store's counts, never with the event it refused.
    assert clnt.send(pid, {"blob": _SECRET}).err_code == Code.SLOT_FULL
    clnt._deliver_for_tests(pid, code="nack", msg_id=msg_id, reason=_SECRET,
                            info={"ssn": _SECRET})
    clnt.resolve(pid, msg_id, Action.RETAIN)
    clnt.flush(pid)
    text = out.getvalue()
    assert text.strip(), "expected log lines"
    assert _SECRET not in text
    assert API_KEY not in text


def test_a_server_413_is_logged_without_the_payload(tmp_path):
    out = _capture()
    server = MockServer(event_status=413)
    pid = _registered(tmp_path, server)
    clnt.send(pid, {"blob": _SECRET})
    assert out.getvalue().strip(), "expected a log line"
    assert _SECRET not in out.getvalue()
    assert API_KEY not in out.getvalue()


def test_send_counts_what_it_stored(registered):
    metrics_mod.reset_metrics()
    clnt.send(registered, {"x": 1})
    snapshot = clnt.metrics()
    assert snapshot["msgs_stored_total"]["{}"] == 1


def test_a_rejected_send_counts_slot_full(registered_full_store):
    pid, _ = registered_full_store
    metrics_mod.reset_metrics()
    clnt.send(pid, {"x": 1})
    assert clnt.metrics()["slot_full_total"]["{}"] == 1


def test_outcomes_are_counted(registered):
    metrics_mod.reset_metrics()
    msg_id = clnt.send(registered, {"x": 1}).msg_id
    clnt._deliver_for_tests(registered, code="ack", msg_id=msg_id)
    assert clnt.metrics()["msgs_acked_total"]["{}"] == 1


def test_resolve_is_counted_by_action(nacked):
    pid, msg_id, _ = nacked
    metrics_mod.reset_metrics()
    clnt.resolve(pid, msg_id, Action.PURGE)
    assert clnt.metrics()["msgs_resolved_total"]['{"action":"purge"}'] == 1


# -- package surface (§6.1, §6.4) ----------------------------------------


def test_the_package_exposes_both_halves_lazily():
    src = (
        "import sys; import common.msging as msg;"
        "assert 'common.msging.srv' not in sys.modules;"
        "assert 'fastapi' not in sys.modules;"
        "assert callable(msg.send) and callable(msg.register);"
        "assert 'fastapi' not in sys.modules, 'client access pulled fastapi';"
        "assert msg.clnt is not None and 'common.msging.clnt' in sys.modules;"
        "print('ok')"
    )
    proc = subprocess.run([sys.executable, "-c", src], capture_output=True,
                          text=True, cwd=_repo_root(), env=_env())
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_the_public_names_are_all_there():
    for name in ("clnt_init", "register", "on_notify", "set_info", "send",
                 "resolve", "flush", "close", "poll_notify", "metrics"):
        assert callable(getattr(clnt, name)), name
    assert clnt.PURGE is Action.PURGE and clnt.RETAIN is Action.RETAIN


def test_metrics_is_a_snapshot_not_a_live_reference(registered):
    clnt.send(registered, {"x": 1})
    snapshot = clnt.metrics()
    clnt.send(registered, {"x": 2})
    assert snapshot["msgs_stored_total"]["{}"] != clnt.metrics()["msgs_stored_total"]["{}"]


# -- the cleaner loop, actually running (§4.4) ---------------------------
#
# Every fixture above pins `sweep_interval_s` at an hour so nothing runs behind
# a test's back. That is the right default and it is also a blind spot: with it
# everywhere, `register()` could simply stop starting the cleaner, the SSE
# reader could stop being wired to §4.3's epoch sweep, and half of §5.1's knobs
# could stop reaching their consumers, with the suite green. These tests use
# `write_loop_cfg`, run the real loop, and wait on conditions rather than
# sleeps.


def test_register_starts_the_cleaner_loop(tmp_path):
    """§4.4 is the at-least-once promise. Without the loop running, a record
    the first POST never landed sits in `pending/` until someone calls
    `flush()` -- which for most apps is never."""
    server = MockServer(event_status=503)
    clnt.clnt_init(write_loop_cfg(tmp_path))
    pid = clnt.register({"host": "x"},
                        _transport=server.transport())["producer_id"]
    store = clnt._store_for_tests(pid)
    msg_id = clnt.send(pid, {"x": 1}).msg_id
    assert msg_id in store.list("pending")            # the 503 left it PENDING

    server.event_status = 202                          # the outage ends
    assert wait_until(lambda: msg_id in store.list("sent")), (
        "the cleaner loop never retried a PENDING record: register() must "
        "start it (design §4.4)")
    assert server.calls["event"] >= 2


def test_the_reader_is_wired_to_the_cleaners_epoch_sweep(tmp_path):
    """§4.3: a changed `server_epoch` means every outstanding outcome is void,
    so all of `sent/` goes back to `pending/` and is resent. The reader sees
    the epoch; only the cleaner can do the sweep. If that hand-off is not
    wired, §4.3 is off entirely and nothing else in the suite notices."""
    server = MockServer()
    clnt.clnt_init(write_loop_cfg(tmp_path))
    pid = clnt.register({"host": "x"},
                        _transport=server.transport())["producer_id"]
    msg_id = clnt.send(pid, {"x": 1}).msg_id
    assert msg_id in clnt._store_for_tests(pid).list("sent")

    epoch = json.dumps({"server_epoch": "srv-restarted-1"})
    server.notify_frames = [f"event: epoch\ndata: {epoch}\n\n"]
    clnt.on_notify(pid, lambda mid, ev: None)

    assert wait_until(lambda: server.calls["event"] >= 2), (
        "a changed server_epoch did not sweep sent/ back to pending/: the "
        "reader's on_epoch must reach the cleaner (design §4.3)")
    assert clnt._producer_for_tests(pid).cleaner.epoch == "srv-restarted-1"
    assert clnt.metrics()["epoch_swept_msgs_total"]["{}"] >= 1


def test_sent_timeout_s_demotes_a_stale_sent_record(tmp_path):
    """§2.1/§4.4's other half of the coverage `server_epoch` does not reach: a
    dropped outcome. Without it the record sits in `sent/` looking handled."""
    server = MockServer()
    clnt.clnt_init(write_loop_cfg(tmp_path, sent_timeout_s=0))
    pid = clnt.register({"host": "x"},
                        _transport=server.transport())["producer_id"]
    clnt.send(pid, {"x": 1})
    assert wait_until(lambda: server.calls["event"] >= 2), (
        "a SENT record older than sent_timeout_s was never demoted and "
        "resent (design §2.1, §4.4)")
    assert clnt.metrics()["cleaner_demotions_total"]["{}"] >= 1


def test_the_max_pending_alarm_fires_from_the_loop(tmp_path):
    """§5.1/§4.4: over `max_pending` the loop alarms -- it never rejects, that
    is `send()`'s `slot_full` and it happens before the msg is stored."""
    out = _capture()
    server = MockServer(event_status=503)
    clnt.clnt_init(write_loop_cfg(tmp_path, max_pending=2, slot_wait_s=0))
    pid = clnt.register({"host": "x"},
                        _transport=server.transport())["producer_id"]
    store = clnt._store_for_tests(pid)
    for i in range(3):
        store.put_pending(_record(i + 1))
    assert wait_until(lambda: "cleaner_max_pending" in out.getvalue()), (
        "pending/ over max_pending did not raise the §4.4 alarm")
    line = [json.loads(ln) for ln in out.getvalue().splitlines()
            if "cleaner_max_pending" in ln][0]
    assert line["max_pending"] == 2, "the configured max_pending never arrived"
    assert line["pending_count"] == 3


def test_every_5_1_setting_reaches_the_object_that_consumes_it(tmp_path,
                                                               monkeypatch):
    """§5.1: "every timeout is configurable -- no hardcoded waits". A knob that
    is parsed, validated and then never passed on is indistinguishable from a
    hardcoded default, and with the loop switched off in every fixture that is
    exactly what four of them were."""
    seen = {}
    real = clnt.api.post_once

    def spy(base, service, key, env, timeout_s, gzip_min_bytes, **kw):
        seen["req_timeout_s"] = timeout_s
        seen["gzip_min_bytes"] = gzip_min_bytes
        return real(base, service, key, env, timeout_s, gzip_min_bytes, **kw)

    monkeypatch.setattr(clnt.api, "post_once", spy)
    server = MockServer()
    clnt.clnt_init(write_cfg(tmp_path, req_timeout_s=7, sweep_interval_s=11,
                             sent_timeout_s=13, max_pending=17,
                             max_in_flight=19, retry_num=23))
    pid = clnt.register({"host": "x"},
                        _transport=server.transport())["producer_id"]
    clnt.send(pid, {"x": 1})
    cleaner = clnt._producer_for_tests(pid).cleaner
    assert seen["req_timeout_s"] == 7          # -> transport.post_once (§2.2)
    assert cleaner._sweep_interval_s == 11     # -> the loop's period (§4.4)
    assert cleaner._sent_timeout_s == 13       # -> demotion (§2.1)
    assert cleaner._max_pending == 17          # -> the alarm (§5.1)
    assert cleaner._retry_num == 23            # -> the backoff cycle (D27)
    assert clnt._producer_for_tests(pid).window.maxsize == 19


# -- /register's failure paths (§10.1, §6.1) ------------------------------


def test_a_refused_registration_raises_and_is_not_retried(tmp_path):
    """§10.1: terminal. Retrying a refused enrollment never helps, and a
    RegistrationError names the endpoint, never the descriptor."""
    server = MockServer(register_status=403, register_body={"reason": "nope"})
    clnt.clnt_init(write_cfg(tmp_path))
    with pytest.raises(clnt_errors.RegistrationError) as exc:
        clnt.register({"host": "x"}, _transport=server.transport())
    assert "403" in str(exc.value)
    assert server.calls["register"] == 1


def test_register_never_follows_a_redirect(tmp_path):
    """A 3xx would hand the producer descriptor to whatever host it names and
    take an identity back from it -- one that this client would then store
    against the *configured* endpoint.

    The redirect target answers with a perfectly good identity, deliberately:
    pointed at a 404 the test cannot tell "refused the redirect" from
    "followed it and got nothing", and a `follow_redirects=True` mutant
    survives.
    """
    server = MockServer(
        register_status=302, register_once=True, register_body="",
        register_headers={"location":
                          "https://evil.test:8443/talium/ingest/v1/register"})
    clnt.clnt_init(write_cfg(tmp_path))
    with pytest.raises(clnt_errors.RegistrationError):
        clnt.register({"host": "x"}, _transport=server.transport())
    assert server.calls["register"] == 1, "the redirect was followed"
    assert not (tmp_path / "store" / "test_srv").exists(), (
        "an identity minted by a redirect target was stored")


def test_a_non_json_register_body_raises(tmp_path):
    server = MockServer(register_body="<html>gateway</html>")
    clnt.clnt_init(write_cfg(tmp_path))
    with pytest.raises(clnt_errors.RegistrationError, match="non-JSON"):
        clnt.register({"host": "x"}, _transport=server.transport())


def test_a_register_body_missing_the_limits_raises(tmp_path):
    """§6.1: "the response carries the server's limits so client and server
    configs cannot drift". A body without them is not a usable identity --
    `max_msg_bytes` is what §8.1's client-side `too_large` check measures."""
    server = MockServer(register_body={"producer_id": "p1", "api_key": API_KEY})
    clnt.clnt_init(write_cfg(tmp_path))
    with pytest.raises(clnt_errors.RegistrationError, match="max_msg_bytes"):
        clnt.register({"host": "x"}, _transport=server.transport())


# -- the window: released once, and only on a real outcome (§5.1) ---------


def test_a_nack_frees_a_window_slot(tmp_path, mock_server):
    """§5.1: a slot is released on any terminal notification. Leaked, the
    window shrinks by one per NACK until nothing ships at all."""
    pid = _registered(tmp_path, mock_server, max_in_flight=1)
    first = clnt.send(pid, {"a": 1}).msg_id
    clnt._deliver_for_tests(pid, code="nack", msg_id=first, reason="bad")
    assert clnt._producer_for_tests(pid).window.in_flight() == 0
    assert clnt.send(pid, {"b": 2}).err_code == Code.OK   # not stored_throttled


def test_a_busy_server_frees_the_window_slot(tmp_path):
    """§8.1: `server_busy` leaves the record PENDING -- nothing is in flight,
    so the slot must go back or the window bleeds one per failed POST."""
    server = MockServer(event_status=503)
    pid = _registered(tmp_path, server, max_in_flight=1)
    assert clnt.send(pid, {"a": 1}).err_code == Code.SERVER_BUSY
    assert clnt._producer_for_tests(pid).window.in_flight() == 0
    assert clnt.send(pid, {"b": 2}).err_code == Code.SERVER_BUSY
    assert server.calls["event"] == 2, "the second send never reached the wire"


def test_a_timeout_frees_the_window_slot(tmp_path):
    server = MockServer(event_error=lambda: httpx.ConnectTimeout("mock"))
    pid = _registered(tmp_path, server, max_in_flight=1)
    assert clnt.send(pid, {"a": 1}).err_code == Code.STORED_TIMEOUT
    assert clnt._producer_for_tests(pid).window.in_flight() == 0


def test_a_duplicate_ack_does_not_invent_a_window_slot(tmp_path):
    """Duplicate outcomes are ROUTINE (§2.2 step 5: a resend hits the server's
    dedup and comes back as an ACK), so a release per notice is a release per
    duplicate. Measured before the fix: `max_in_flight=2`, three ACKs for an
    unknown msg_id, and four msgs went out under a window of two."""
    server = MockServer()
    pid = _registered(tmp_path, server, max_in_flight=2)
    clnt.send(pid, {"a": 1})
    clnt.send(pid, {"b": 2})
    window = clnt._producer_for_tests(pid).window
    assert window.in_flight() == 2

    for _ in range(3):
        clnt._deliver_for_tests(pid, code="ack", msg_id=999_999)
    assert window.in_flight() == 2, (
        "an outcome that matched no record handed back a window slot")
    assert clnt.send(pid, {"c": 3}).err_code == Code.STORED_THROTTLED
    assert server.calls["event"] == 2


def test_a_duplicate_nack_does_not_invent_a_window_slot(tmp_path):
    server = MockServer()
    pid = _registered(tmp_path, server, max_in_flight=2)
    clnt.send(pid, {"a": 1})
    clnt.send(pid, {"b": 2})
    window = clnt._producer_for_tests(pid).window
    for _ in range(3):
        clnt._deliver_for_tests(pid, code="nack", msg_id=999_999, reason="late")
    assert window.in_flight() == 2


# -- close: the gate, the deadline, and what `drained` means (§2.4) -------


def test_close_does_not_report_drained_with_a_send_in_flight(tmp_path,
                                                             monkeypatch):
    """§2.4 step 1 is a gate, not a flag read.

    `producer.closing = True` is a check-then-act against `send()`: a send that
    passed the flag and has not yet reached `put_pending()` is invisible to a
    drain that only counts the store, so `close()` returned `{drained, 0}` --
    and `shutdown()` then stopped the cleaner, leaving the record that landed a
    moment later sitting in `pending/` until the next process start.

    The interleaving is forced rather than raced: `_wait_for_slot` is the real
    window (§5.1 lets a send wait there for `slot_wait_s`), so the test parks a
    send inside it and closes while it is there. A `sleep`-based version of
    this reproduces only intermittently and would be worthless as a regression.
    """
    server = MockServer()
    pid = _registered(tmp_path, server, close_timeout_s=5)
    store = clnt._store_for_tests(pid)

    inside_gate = threading.Event()
    release = threading.Event()
    real_wait = clnt.api._wait_for_slot

    def paused(producer):
        inside_gate.set()
        release.wait(5)
        return real_wait(producer)

    monkeypatch.setattr(clnt.api, "_wait_for_slot", paused)

    result = {}

    def sender():
        try:
            result["send"] = clnt.send(pid, {"x": 1})
        except ProducerClosingError as exc:
            result["send"] = exc

    thread = threading.Thread(target=sender, daemon=True)
    thread.start()
    assert inside_gate.wait(5), "the send never reached the gate"
    # Let the send out again a moment after close() has begun, so a correct
    # close() has something to wait for and a broken one has already counted.
    threading.Timer(0.3, release.set).start()

    closed = clnt.close(pid, timeout_s=3.0)
    thread.join(5)
    release.set()

    # Either outcome is correct, and they are the only two: the send was
    # refused at the gate (nothing stored), or it was counted and the drain
    # waited for it (stored, and reported). What must never happen is the
    # third: `drained` with the record landing behind close()'s back, into a
    # store whose cleaner `shutdown()` has just stopped.
    assert not (closed.status == "drained" and store.total() > 0), (
        f"close() reported {closed.status}/{closed.unresolved} with "
        f"{store.total()} msg(s) still in the store")
    if isinstance(result["send"], ProducerClosingError):
        assert store.total() == 0
    else:
        assert result["send"].err_code == Code.OK
        assert closed.unresolved == store.total() == 1


def test_close_honours_its_timeout_against_a_slow_server(tmp_path):
    """The drain used to check its deadline only *between* sweeps, and
    `sweep_once()` POSTs the whole backlog serially. Measured: 8 pending msgs
    against a 0.4 s server, `close(timeout_s=0.2)` returned after 3.31 s. At
    the defaults (`max_in_flight=64`, `req_timeout_s=10`) a `close(5)` could
    block for ~640 s."""
    server = MockServer(event_status=503)
    pid = _registered(tmp_path, server, max_in_flight=64)
    store = clnt._store_for_tests(pid)
    for i in range(8):
        store.put_pending(_record(i + 1))
    server.event_delay_s = 0.4

    started = time.monotonic()
    result = clnt.close(pid, timeout_s=0.2)
    elapsed = time.monotonic() - started

    assert result.status == "timeout"
    # One POST already on the wire still finishes, so the honest bound is
    # timeout_s + one req_timeout_s -- not eight of them.
    assert elapsed < 1.5, f"close(0.2) took {elapsed:.2f}s"


def test_close_counts_a_corrupt_record_as_unresolved(tmp_path, mock_server):
    """`count()` hides a record that will not parse, because the cleaner
    cannot act on one. A drain cannot hide it: it is still an undelivered msg
    on disk, and `drained` is a claim that there are none."""
    pid = _registered(tmp_path, mock_server)
    store = clnt._store_for_tests(pid)
    (store._dirs["pending"] / "77.json").write_text("{not json", encoding="utf-8")
    result = clnt.close(pid, timeout_s=0.3)
    assert result.status == "timeout" and result.unresolved == 1


def test_unresolved_excludes_nacked(tmp_path, mock_server):
    """§2.4: `nacked/` waits on the app's `resolve()`, which draining cannot
    supply, so it is deliberately not part of `unresolved` -- §2.3 folds it
    into `PENDING` next start. Pinned either way so the choice is a decision,
    not an accident."""
    pid = _registered(tmp_path, mock_server)
    msg_id = clnt.send(pid, {"x": 1}).msg_id
    clnt._deliver_for_tests(pid, code="nack", msg_id=msg_id, reason="bad")
    store = clnt._store_for_tests(pid)
    assert store.list("nacked") == [msg_id]

    result = clnt.close(pid, timeout_s=0.5)
    assert result.status == "drained" and result.unresolved == 0
    assert store.list("nacked") == [msg_id], "the record must stay for resolve()"


def test_two_concurrent_closes_drain_and_post_close_once(tmp_path, mock_server):
    """`already_closing` was a check-then-set: two threads both read False,
    both drained, and both POSTed the informational `/close`."""
    pid = _registered(tmp_path, mock_server)
    clnt.send(pid, {"x": 1})
    results = []
    barrier = threading.Barrier(2)

    def closer():
        barrier.wait(5)
        results.append(clnt.close(pid, timeout_s=1.0))

    threads = [threading.Thread(target=closer, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(results) == 2
    assert mock_server.calls["close"] == 1


def test_flush_after_close_raises(registered):
    """§2.4 step 1 stops sends, and `flush()` is a send -- against a producer
    whose cleaner and reader are already stopped and whose msgs `close()` has
    already reported as unresolved."""
    clnt.close(registered, timeout_s=0.3)
    with pytest.raises(ProducerClosingError):
        clnt.flush(registered)


def test_resolve_still_works_after_close(nacked):
    """Deliberately *not* closed off: `resolve()` writes no msg and makes no
    request, and §2.2 step 5 leaves the decision open until the app makes it.
    A PURGE after the drain is the app declining §2.3's default resend."""
    pid, msg_id, store = nacked
    clnt.close(pid, timeout_s=0.3)
    clnt.resolve(pid, msg_id, Action.PURGE)
    assert store.get(msg_id) is None


# -- stale bindings and reattachment (§3) ---------------------------------


def test_a_repointed_alias_reattaches_on_the_following_start(tmp_path):
    """§3 leaves the old directory on disk, so an alias accumulates one
    identity per endpoint it has pointed at. Choosing the newest and *then*
    testing staleness re-registers forever: the stale directory is the newest,
    it wins the tiebreak, it fails the test, and the identity issued against
    the *current* endpoint is never even considered. Measured before the fix:
    three producer_ids and two registrations against the new endpoint over two
    restarts, orphaning a backlog each time."""
    clnt.clnt_init(write_cfg(tmp_path))
    original = MockServer()
    first = clnt.register({"host": "x"}, _transport=original.transport())
    clnt._reset_for_tests()

    moved = tmp_path / "moved.yaml"
    moved.write_text(
        (tmp_path / "msging.yaml").read_text(encoding="utf-8")
        .replace("https://ingest.test:8443", "https://elsewhere.test:8443"),
        encoding="utf-8")
    new_server = MockServer(id_prefix="prod-moved")

    clnt.clnt_init(str(moved))
    second = clnt.register({"host": "x"}, _transport=new_server.transport())
    clnt._reset_for_tests()
    assert second["producer_id"] != first["producer_id"]

    clnt.clnt_init(str(moved))
    third = clnt.register({"host": "x"}, _transport=new_server.transport())
    assert third["producer_id"] == second["producer_id"], (
        "a repointed alias re-registered on every start, orphaning the "
        "backlog each time (design §3)")
    assert new_server.calls["register"] == 1


def test_an_alias_pointed_back_reattaches_the_original_identity(tmp_path):
    """The same bug, the other way round: an alias flipped back to an endpoint
    it used before has a perfectly good identity -- and its undelivered msgs --
    sitting on disk, and must not mint a third one.

    The two identities carry the *same* `registered_at` (§3's timestamps are
    second-precision, and a mock issues both in the same second), so the old
    selection fell through to a tiebreak on the producer_id **string** -- i.e.
    to alphabetical order. `zzz` makes the away identity win that tiebreak,
    which is the arrangement the old code gets wrong; with `prod-away` it
    happened to pick correctly for a reason that has nothing to do with
    endpoints.
    """
    home = write_cfg(tmp_path)
    away = tmp_path / "away.yaml"
    away.write_text(
        (tmp_path / "msging.yaml").read_text(encoding="utf-8")
        .replace("https://ingest.test:8443", "https://elsewhere.test:8443"),
        encoding="utf-8")

    home_server = MockServer(id_prefix="prod-home")
    away_server = MockServer(id_prefix="prod-zzz-away")

    clnt.clnt_init(home)
    first = clnt.register({"host": "x"}, _transport=home_server.transport())
    clnt._reset_for_tests()
    clnt.clnt_init(str(away))
    clnt.register({"host": "x"}, _transport=away_server.transport())
    clnt._reset_for_tests()

    clnt.clnt_init(home)
    back = clnt.register({"host": "x"}, _transport=home_server.transport())
    assert back["producer_id"] == first["producer_id"]
    assert back["base_path"] == "https://ingest.test:8443"
    assert home_server.calls["register"] == 1


def test_a_restart_reuses_the_persisted_max_msg_bytes(tmp_path):
    """§6.1: the limit travels with the identity, because a process that
    reattaches never sees a `/register` response to learn it from -- and §8.1's
    `too_large` is checked client-side against it. A non-default value, so the
    assertion is not default-equals-default."""
    server = MockServer(max_msg_bytes=4242)
    cfg = write_cfg(tmp_path)
    clnt.clnt_init(cfg)
    first = clnt.register({"host": "x"}, _transport=server.transport())
    assert first["max_msg_bytes"] == 4242
    clnt._reset_for_tests()

    clnt.clnt_init(cfg)
    second = clnt.register({"host": "x"}, _transport=server.transport())
    assert server.calls["register"] == 1, "it re-registered instead of reattaching"
    assert second["max_msg_bytes"] == 4242, (
        "the persisted max_msg_bytes was lost across the restart")
    # And it is the limit §8.1 measures against, not just a number in a dict.
    assert clnt.send(second["producer_id"],
                     {"blob": "x" * 5000}).err_code == Code.TOO_LARGE


def test_registering_against_a_repointed_alias_is_never_silent(tmp_path,
                                                               mock_server):
    """§3: "never silent". A live producer is still handed back -- its backlog
    belongs to the old endpoint and only close() or a restart can retire it --
    but the operator has to be told the binding it is bound to is not the one
    in the config."""
    clnt.clnt_init(write_cfg(tmp_path))
    first = clnt.register({"host": "x"}, _transport=mock_server.transport())
    out = _capture()
    moved = tmp_path / "moved.yaml"
    moved.write_text(
        (tmp_path / "msging.yaml").read_text(encoding="utf-8")
        .replace("https://ingest.test:8443", "https://elsewhere.test:8443"),
        encoding="utf-8")
    clnt.clnt_init(str(moved))
    again = clnt.register({"host": "x"}, _transport=mock_server.transport())
    assert again["producer_id"] == first["producer_id"]
    assert "producer_bound_to_stale_endpoint" in out.getvalue()


# -- §8.1's "re-register" (finding 5) -------------------------------------


def test_producer_status_exposes_the_halted_state(tmp_path):
    """§8.1's `unauthorized` is "PENDING, halted". Observable only in a log
    line, no app can branch on it."""
    server = MockServer(event_status=401)
    pid = _registered(tmp_path, server)
    assert clnt.producer_status(pid)["unauthorized"] is False
    clnt.send(pid, {"x": 1})
    status = clnt.producer_status(pid)
    assert status["unauthorized"] is True
    assert status["pending"] == 1 and status["sent"] == 0
    assert status["producer_id"] == pid


def test_re_register_mints_a_new_identity_after_a_revoked_key(tmp_path):
    """§8.1 says "re-register", and there was no route to it: `register()`
    returns the live producer, and after `close()` it returns the stored
    identity -- with the same revoked key."""
    server = MockServer(event_status=401)
    pid = _registered(tmp_path, server)
    clnt.send(pid, {"x": 1})
    assert clnt.producer_status(pid)["unauthorized"] is True

    fresh = clnt.re_register(pid, {"host": "x"})
    assert fresh["producer_id"] != pid
    assert server.calls["register"] == 2
    # The old binding is gone from the process but not from disk -- it may
    # hold undelivered msgs (§3).
    with pytest.raises(NotRegisteredError):
        clnt.producer_status(pid)
    assert (tmp_path / "store" / "test_srv" / pid / "pending").is_dir()
    assert clnt.producer_status(fresh["producer_id"])["unauthorized"] is False


def test_register_after_close_still_returns_the_revoked_identity(tmp_path):
    """Why `re_register()` has to exist: `register()` is idempotent by design
    (§3), so it cannot be the recovery path -- closing and registering again
    hands back the same producer_id and the same key."""
    server = MockServer()
    cfg = write_cfg(tmp_path)
    clnt.clnt_init(cfg)
    pid = clnt.register({"host": "x"},
                        _transport=server.transport())["producer_id"]
    clnt.close(pid, timeout_s=0.3)
    again = clnt.register({"host": "x"}, _transport=server.transport())
    assert again["producer_id"] == pid
    assert server.calls["register"] == 1


# -- the global lock, and whose transport it is (findings 7, 8) -----------


def test_register_does_not_block_an_unrelated_binding(tmp_path):
    """§2.2 bounds `send()` at one `req_timeout_s`. Holding the process-wide
    state lock across `/register` bounds it at `_REGISTER_TIMEOUT_S` = 30 s
    instead: an unrelated binding's send() was measured blocked 0.86 s behind
    another thread's registration."""
    fast = MockServer()
    clnt.clnt_init(write_cfg(tmp_path))
    pid = clnt.register({"host": "x"},
                        _transport=fast.transport())["producer_id"]

    slow = MockServer(id_prefix="prod-slow")
    gate = threading.Event()
    real_handle = slow.handle

    def blocking(request):
        if request.url.path.endswith("/register"):
            gate.set()
            time.sleep(1.0)
        return real_handle(request)

    slow.handle = blocking
    thread = threading.Thread(
        target=lambda: clnt.register({"host": "y"}, server_alias="other_srv",
                                     _transport=slow.transport()),
        daemon=True)
    thread.start()
    assert gate.wait(5), "the slow /register never started"

    started = time.monotonic()
    assert clnt.send(pid, {"x": 1}).err_code == Code.OK
    elapsed = time.monotonic() - started
    thread.join(10)
    assert elapsed < 0.5, (
        f"send() on an unrelated binding waited {elapsed:.2f}s behind a "
        "/register: the global lock must not be held across a network call")


def test_register_and_close_do_not_close_the_callers_transport(tmp_path):
    """A caller passing a long-lived pool for connection reuse would find it
    dead after the first `register()`. `httpx.MockTransport.close()` is a
    no-op, which is exactly why this was invisible."""
    server = MockServer()
    counting = CountingTransport(server.transport())
    clnt.clnt_init(write_cfg(tmp_path))
    pid = clnt.register({"host": "x"},
                        _transport=counting)["producer_id"]
    assert counting.closes == 0, "register() closed the caller's transport"
    clnt.send(pid, {"x": 1})
    assert counting.closes == 0, "send() closed the caller's transport"
    clnt.close(pid, timeout_s=0.5)
    assert counting.closes == 0, "close() closed the caller's transport"


# -- surface details nothing else pins ------------------------------------


def test_the_notify_queue_is_bounded_and_drops_the_oldest(registered):
    """An app that registers a callback and never polls must not accumulate
    one object per outcome forever; the newest outcomes are the actionable
    ones, so the oldest go first."""
    pid = registered
    limit = clnt.api._NOTIFY_QUEUE_MAX
    clnt.on_notify(pid, lambda msg_id, ev: None)
    for i in range(limit + 5):
        clnt._deliver_for_tests(pid, code="nack", msg_id=i, reason="x")
    queued = clnt.poll_notify(pid)
    assert len(queued) == limit
    assert queued[0].msg_id == 5 and queued[-1].msg_id == limit + 4


def test_producer_desc_rides_on_every_envelope(tmp_path, mock_server):
    """§7: a `producer_desc` string in the descriptor travels with every msg
    for troubleshooting."""
    clnt.clnt_init(write_cfg(tmp_path))
    pid = clnt.register({"host": "x", "producer_desc": "collector-7"},
                        _transport=mock_server.transport())["producer_id"]
    clnt.send(pid, {"a": 1})
    clnt.send(pid, {"b": 2})
    assert [e.producer_desc for e in mock_server.envelopes] == ["collector-7"] * 2


def test_a_non_string_producer_desc_is_dropped_not_coerced(tmp_path,
                                                           mock_server):
    clnt.clnt_init(write_cfg(tmp_path))
    pid = clnt.register({"host": "x", "producer_desc": {"not": "a string"}},
                        _transport=mock_server.transport())["producer_id"]
    clnt.send(pid, {"a": 1})
    assert mock_server.envelopes[-1].producer_desc is None


def test_an_envelope_exactly_at_max_msg_bytes_is_sent(tmp_path):
    """§8.1 is "over max_msg_bytes", so the boundary is `>`, not `>=`: an
    envelope of exactly `max_msg_bytes` must go out."""
    server = MockServer(max_msg_bytes=10_000)
    pid = _registered(tmp_path, server)
    producer = clnt._producer_for_tests(pid)
    probe = clnt.send(pid, {"blob": "x"})
    baseline = len(server.envelopes[-1].model_dump_json().encode("utf-8"))
    assert probe.err_code == Code.OK
    # A payload sized so the whole envelope lands exactly on the limit.
    exact = "x" * (producer.max_msg_bytes - baseline + 1)
    assert clnt.send(pid, {"blob": exact}).err_code == Code.OK
    at_limit = len(server.envelopes[-1].model_dump_json().encode("utf-8"))
    assert at_limit == producer.max_msg_bytes
    assert clnt.send(pid, {"blob": exact + "x"}).err_code == Code.TOO_LARGE


def test_a_403_on_the_stream_halts_like_a_401(tmp_path):
    """§8.1 pairs them: a revoked key and a forbidden one are both "stop", not
    "reconnect on the curve"."""
    server = MockServer(notify_status=403)
    pid = _registered(tmp_path, server)
    clnt.on_notify(pid, lambda msg_id, ev: None)
    producer = clnt._producer_for_tests(pid)
    reader = clnt._sse_for_tests(pid)
    assert wait_until(lambda: producer.unauthorized
                      and not reader._thread.is_alive())
    assert server.calls["notify"] == 1


def test_the_package_advertises_every_client_name(tmp_path):
    """`msg.send` resolves through `__getattr__`, so a name missing from
    `_CLIENT_NAMES` is an AttributeError at the app's first call -- or worse,
    silently resolves to a submodule of the same name."""
    import common.msging as pkg
    for name in sorted(pkg._CLIENT_NAMES):
        assert callable(getattr(pkg, name)), name
    # `metrics` is the one deliberate omission -- see the next test. `PURGE` /
    # `RETAIN` are eager enum values, not lazily-resolved functions.
    assert set(pkg._CLIENT_NAMES) == (set(clnt.__all__)
                                      - {"PURGE", "RETAIN", "metrics"})


def test_package_metrics_is_the_module_not_a_function():
    """README documented `msg.metrics()`, which raised
    `TypeError: 'module' object is not callable`: `__getattr__` is consulted
    only on an attribute *miss*, and importing `common.msging.metrics` (every
    clnt module does) binds the submodule onto the package. The README was the
    side that was wrong -- shadowing a real submodule with a same-named
    function would make the meaning depend on import order."""
    import common.msging as pkg
    from common.msging import metrics as metrics_module
    assert pkg.metrics is metrics_module
    assert not callable(pkg.metrics)
    assert isinstance(pkg.metrics.metrics(), dict)
    assert isinstance(clnt.metrics(), dict)


def test_import_star_pulls_neither_half(tmp_path):
    """`import *` resolves every name in `__all__`, so advertising `send` and
    `srv_init` there imports both halves -- FastAPI included -- on a collector
    that has neither. §6.4's lazy import is load-bearing, not a nicety."""
    src = (
        "import sys;"
        "ns = {};"
        "exec('from common.msging import *', ns);"
        "assert 'common.msging.srv' not in sys.modules, 'import * pulled srv';"
        "assert 'common.msging.clnt' not in sys.modules, 'import * pulled clnt';"
        "assert 'fastapi' not in sys.modules, 'import * pulled fastapi';"
        "assert ns['PURGE'] is not None and ns['Code'] is not None;"
        "import common.msging as m;"
        "assert 'send' in dir(m) and 'srv_init' in dir(m);"
        "print('ok')"
    )
    proc = subprocess.run([sys.executable, "-c", src], capture_output=True,
                          text=True, cwd=_repo_root(), env=_env())
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


# -- helpers --------------------------------------------------------------


def _record(msg_id: int) -> MsgRecord:
    """A durable record with a valid envelope, for seeding `pending/` without
    going through `send()` (which would POST)."""
    env = Envelope(producer_id="seed", msg_id=msg_id, service_name="ingest",
                   created_at="2026-07-25T10:15:30Z", info={},
                   payload={"seed": msg_id})
    return MsgRecord(msg_id=msg_id, envelope=env,
                     created_at="2026-07-25T10:15:30Z")


def _registered(tmp_path, server: MockServer, **cfg) -> str:
    clnt.clnt_init(write_cfg(tmp_path, **cfg))
    return clnt.register({"host": "x"},
                         _transport=server.transport())["producer_id"]


def _repo_root() -> str:
    import pathlib
    return str(pathlib.Path(__file__).resolve().parents[3])


def _env() -> dict:
    import os
    import pathlib
    env = dict(os.environ)
    root = pathlib.Path(_repo_root())
    env["PYTHONPATH"] = f"{root}:{root / 'src'}"
    return env
