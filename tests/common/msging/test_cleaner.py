"""Tests for the cleaner loop (design §4.4).

This is the loop the at-least-once promise rests on: retry, `SENT -> PENDING`
demotion, the epoch sweep, and the `max_pending` alarm. Most of these tests
are written against a rule that is cheap to break silently -- a msg quietly
dropped after `retry_num`, a window slot released twice, a sweep that fires on
every epoch frame instead of on a change -- so they assert the store and the
window directly rather than trusting the returned counts alone.

Deterministic by construction: clocks are injected, jitter is injected, and
threads hand off with Events. The only `sleep` is the one the brief's own
demotion test uses to put a second-precision `sent_at` in the past.
"""
import io
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import structlog

from common.msging import metrics
from common.msging.clnt.cleaner import Cleaner
from common.msging.clnt.store import MsgStore
from common.msging.clnt.transport import SendResult
from common.msging.clnt.window import Window
from common.msging.codes import Code
from common.msging.log import configure_logging
from common.msging.models import Envelope, MsgRecord


def _rec(n: int, sent_at: str | None = None) -> MsgRecord:
    return MsgRecord(msg_id=n, created_at="2026-07-25T10:15:30Z", sent_at=sent_at,
                     envelope=Envelope(producer_id="p", msg_id=n, service_name="ingest",
                                       created_at="2026-07-25T10:15:30Z"))


def _cleaner(tmp_path, send_fn, **kw):
    store = MsgStore(tmp_path)
    opts = dict(sweep_interval_s=0.01, sent_timeout_s=300.0, retry_num=3)
    opts.update(kw)
    return store, Cleaner(store, Window(64), send_fn, **opts)


# -- the brief's six -----------------------------------------------------


def test_pending_msgs_are_retried(tmp_path):
    sent = []
    store, c = _cleaner(tmp_path, lambda rec: (sent.append(rec.msg_id), Code.OK)[1])
    store.put_pending(_rec(1))
    assert c.sweep_once()["retried"] == 1
    assert sent == [1]


def test_stale_sent_is_demoted_to_pending(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=0.0)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    time.sleep(0.01)
    assert c.sweep_once()["demoted"] == 1
    assert store.list("pending") == [1]


def test_fresh_sent_is_left_alone(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=999.0)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    assert c.sweep_once()["demoted"] == 0
    assert store.list("sent") == [1]


def test_nacked_is_left_alone_pending_the_apps_decision(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    store.mark_nacked(1, "bad")
    c.sweep_once()
    assert store.list("nacked") == [1]


def test_a_msg_is_never_given_up_on(tmp_path):
    """retry_num bounds a cycle, not the msg — at-least-once depends on this."""
    attempts = []
    store, c = _cleaner(tmp_path, lambda rec: (attempts.append(1), Code.STORED_UNREACHABLE)[1],
                        retry_num=1)
    store.put_pending(_rec(1))
    for _ in range(5):
        c.sweep_once()
    assert len(attempts) >= 5
    assert store.list("pending") == [1]        # still there, still ours


def test_window_full_stops_the_sweep_sending(tmp_path):
    sent = []
    store = MsgStore(tmp_path)
    window = Window(1)
    window.acquire()                            # window is now full
    c = Cleaner(store, window, lambda rec: (sent.append(rec.msg_id), Code.OK)[1],
                sweep_interval_s=0.01, sent_timeout_s=300.0, retry_num=3)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert sent == []


# -- helpers -------------------------------------------------------------


class _Clock:
    """An injected `time.monotonic`. Nothing in these tests waits on wall time."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _Recorder:
    """A `send_fn` that records every attempt and answers from a script."""

    def __init__(self, *codes, default=Code.STORED_UNREACHABLE) -> None:
        self._codes = list(codes)
        self._default = default
        self.seen: list[int] = []

    def __call__(self, rec: MsgRecord):
        self.seen.append(rec.msg_id)
        if self._codes:
            return self._codes.pop(0)
        return self._default

    @property
    def count(self) -> int:
        return len(self.seen)


def _capture() -> io.StringIO:
    stream = io.StringIO()
    configure_logging(json=True,
                      logger_factory=structlog.PrintLoggerFactory(file=stream))
    return stream


def _cleaner_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "msging-cleaner"]


# -- construction --------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1])
def test_retry_num_below_one_is_a_config_error(tmp_path, bad):
    with pytest.raises(ValueError):
        Cleaner(MsgStore(tmp_path), Window(4), lambda rec: Code.OK,
                sweep_interval_s=1.0, sent_timeout_s=1.0, retry_num=bad)


def test_negative_intervals_are_a_config_error(tmp_path):
    with pytest.raises(ValueError):
        Cleaner(MsgStore(tmp_path), Window(4), lambda rec: Code.OK,
                sweep_interval_s=-1.0, sent_timeout_s=1.0, retry_num=3)
    with pytest.raises(ValueError):
        Cleaner(MsgStore(tmp_path), Window(4), lambda rec: Code.OK,
                sweep_interval_s=1.0, sent_timeout_s=-1.0, retry_num=3)


# -- retry ---------------------------------------------------------------


def test_a_successful_send_moves_the_record_to_sent(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert store.list("sent") == [1]
    assert store.list("pending") == []


def test_a_failed_send_leaves_the_record_pending(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.STORED_TIMEOUT)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert store.list("pending") == [1]
    assert store.list("sent") == []


def test_every_pending_msg_is_attempted_in_one_pass(tmp_path):
    send = _Recorder(default=Code.OK)
    store, c = _cleaner(tmp_path, send)
    for n in (1, 2, 3):
        store.put_pending(_rec(n))
    assert c.sweep_once()["retried"] == 3
    assert send.seen == [1, 2, 3]


def test_sent_and_nacked_records_are_never_resent(tmp_path):
    send = _Recorder(default=Code.OK)
    store, c = _cleaner(tmp_path, send, sent_timeout_s=999.0)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    store.put_pending(_rec(2))
    store.mark_sent(2)
    store.mark_nacked(2, "bad")
    c.sweep_once()
    assert send.seen == []


def test_the_whole_record_is_handed_to_send_fn(tmp_path):
    """A resend has to rebuild the request verbatim (§3), so the envelope --
    not just a msg_id -- is what the loop passes down."""
    seen = []
    store, c = _cleaner(tmp_path, lambda rec: (seen.append(rec), Code.OK)[1])
    rec = _rec(1)
    rec.envelope.event_id = "sess-abc"
    store.put_pending(rec)
    c.sweep_once()
    assert seen[0].envelope.event_id == "sess-abc"
    assert seen[0].envelope.producer_id == "p"


# -- never gives up (§4.4, D27) -----------------------------------------


def test_retry_num_is_a_cycle_bound_not_a_give_up_threshold(tmp_path):
    """The whole guarantee: 50 failures with retry_num=3 is 50 attempts."""
    clock = _Clock()
    send = _Recorder(default=Code.STORED_UNREACHABLE)
    store, c = _cleaner(tmp_path, send, retry_num=3, _monotonic_fn=clock,
                        _jitter_fn=lambda span: span, backoff_cap_s=60.0)
    store.put_pending(_rec(1))
    for _ in range(50):
        c.sweep_once()
        clock.advance(120.0)               # past any capped backoff
    assert send.count == 50
    assert store.list("pending") == [1]
    assert store.list("nacked") == []
    assert store.get(1) is not None


def test_a_forever_failing_msg_is_never_purged(tmp_path):
    clock = _Clock()
    store, c = _cleaner(tmp_path, lambda rec: Code.SERVER_BUSY, retry_num=2,
                        _monotonic_fn=clock, _jitter_fn=lambda span: span)
    store.put_pending(_rec(1))
    for _ in range(20):
        c.sweep_once()
        clock.advance(120.0)
    assert store.total() == 1
    assert store.list("pending") == [1]


def test_a_failing_msg_does_not_block_the_ones_behind_it(tmp_path):
    send = _Recorder(Code.STORED_UNREACHABLE, Code.OK, Code.OK)
    store, c = _cleaner(tmp_path, send)
    for n in (1, 2, 3):
        store.put_pending(_rec(n))
    c.sweep_once()
    assert send.seen == [1, 2, 3]
    assert store.list("sent") == [2, 3]
    assert store.list("pending") == [1]


# -- backoff -------------------------------------------------------------


def test_backoff_defers_the_next_attempt_inside_a_cycle(tmp_path):
    clock = _Clock()
    send = _Recorder(default=Code.STORED_UNREACHABLE)
    store, c = _cleaner(tmp_path, send, retry_num=5, backoff_base_s=10.0,
                        _monotonic_fn=clock, _jitter_fn=lambda span: span)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert send.count == 1
    clock.advance(9.0)
    c.sweep_once()
    assert send.count == 1, "retried inside its own backoff window"
    clock.advance(2.0)
    c.sweep_once()
    assert send.count == 2


def test_the_backoff_curve_is_capped(tmp_path):
    """Exponential without a cap parks a msg for hours after a few failures."""
    clock = _Clock()
    send = _Recorder(default=Code.STORED_UNREACHABLE)
    store, c = _cleaner(tmp_path, send, retry_num=40, backoff_base_s=1.0,
                        backoff_cap_s=4.0, _monotonic_fn=clock,
                        _jitter_fn=lambda span: span)
    store.put_pending(_rec(1))
    for _ in range(20):
        c.sweep_once()
        clock.advance(4.001)
    assert send.count == 20


def test_a_new_cycle_restarts_the_backoff_curve(tmp_path):
    """What `retry_num` MEANS: the fail count resets at the cycle boundary and
    the curve starts over. Without the reset every later failure looks like a
    cycle end, the deferral is 0 forever, and the msg is hammered on every
    single sweep for as long as the outage lasts."""
    spans = []
    clock = _Clock()
    send = _Recorder(default=Code.STORED_UNREACHABLE)
    store, c = _cleaner(tmp_path, send, retry_num=2, backoff_base_s=10.0,
                        _monotonic_fn=clock,
                        _jitter_fn=lambda span: (spans.append(span), span)[1])
    store.put_pending(_rec(1))
    c.sweep_once()                     # fail 1 of the cycle: deferred 10s
    clock.advance(11.0)
    c.sweep_once()                     # fail 2 ends the cycle: deferred 0
    c.sweep_once()                     # fail 1 of the *new* cycle
    assert send.count == 3
    c.sweep_once()
    assert send.count == 3, "the curve vanished after the first cycle"
    assert spans == [10.0, 10.0], "the new cycle did not restart at the base"


def test_backoff_is_jittered(tmp_path):
    """Full jitter, so a store that went dark all at once does not come back
    all at once. The span handed to the jitter function is the curve."""
    spans = []
    clock = _Clock()
    store, c = _cleaner(tmp_path, lambda rec: Code.STORED_UNREACHABLE,
                        retry_num=5, backoff_base_s=2.0, _monotonic_fn=clock,
                        _jitter_fn=lambda span: (spans.append(span), 0.0)[1])
    store.put_pending(_rec(1))
    for _ in range(3):
        c.sweep_once()
    assert spans == [2.0, 4.0, 8.0]


def test_a_successful_send_clears_the_backoff_state(tmp_path):
    """Asserted on the *curve*, not on the next attempt's timing: by the time
    a demoted msg comes round again its `not_before` is already in the past,
    so "it was retried" is true whether or not the state was dropped. What is
    only true if it was dropped is that the next failure starts the curve over
    at the first step instead of continuing where the old cycle left off."""
    spans = []
    clock = _Clock()
    send = _Recorder(Code.STORED_UNREACHABLE, Code.OK, default=Code.STORED_UNREACHABLE)
    store, c = _cleaner(tmp_path, send, retry_num=9, backoff_base_s=10.0,
                        sent_timeout_s=0.0, _monotonic_fn=clock,
                        _jitter_fn=lambda span: (spans.append(span), span)[1],
                        _now_fn=lambda: datetime.now(timezone.utc) + timedelta(days=1))
    store.put_pending(_rec(1))
    c.sweep_once()                      # fails: first step of the curve
    clock.advance(11.0)
    c.sweep_once()                      # succeeds -> SENT, then demoted (age > 0)
    assert send.count == 2
    c.sweep_once()                      # fails again: a fresh cycle, not fails=2
    assert send.count == 3
    assert spans == [10.0, 10.0], "the OK left the old cycle's fail count behind"


# -- Retry-After (§8.1) --------------------------------------------------


def test_server_busy_honors_retry_after_instead_of_the_curve(tmp_path):
    clock = _Clock()
    send = _Recorder(default=SendResult(Code.SERVER_BUSY, "busy", 30.0))
    store, c = _cleaner(tmp_path, send, retry_num=1, _monotonic_fn=clock,
                        retry_after_cap_s=60.0)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert send.count == 1
    clock.advance(29.0)
    c.sweep_once()
    assert send.count == 1, "retried before the server's Retry-After elapsed"
    clock.advance(2.0)
    c.sweep_once()
    assert send.count == 2


def test_an_absurd_retry_after_is_clamped(tmp_path):
    """A peer must not be able to park this loop for a day."""
    clock = _Clock()
    send = _Recorder(default=SendResult(Code.SERVER_BUSY, "busy", 86400.0))
    store, c = _cleaner(tmp_path, send, retry_num=1, _monotonic_fn=clock,
                        retry_after_cap_s=5.0)
    store.put_pending(_rec(1))
    c.sweep_once()
    clock.advance(6.0)
    c.sweep_once()
    assert send.count == 2


def test_a_non_finite_retry_after_falls_back_to_the_curve(tmp_path):
    """`inf` straight into `not_before` is a permanently parked msg."""
    clock = _Clock()
    send = _Recorder(default=SendResult(Code.SERVER_BUSY, "busy", float("inf")))
    store, c = _cleaner(tmp_path, send, retry_num=1, _monotonic_fn=clock)
    store.put_pending(_rec(1))
    for _ in range(3):
        c.sweep_once()
    assert send.count == 3


def test_a_negative_retry_after_does_not_rewind_the_schedule(tmp_path):
    """A deferral is a floor, so "retried on the next pass" looks identical
    with and without the clamp -- the only place the difference is visible is
    the schedule itself, which must never be set behind the clock."""
    clock = _Clock()
    send = _Recorder(default=SendResult(Code.SERVER_BUSY, "busy", -900.0))
    store, c = _cleaner(tmp_path, send, retry_num=1, _monotonic_fn=clock)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert c._retries[1].not_before >= clock.t
    c.sweep_once()
    assert send.count == 2
    assert store.list("pending") == [1]


class _BusyWith:
    """A `send_fn` result whose `retry_after_s` is whatever the app made it.
    `send_fn` is injected code -- `transport.SendResult` is the intended
    argument, not the guaranteed one."""

    code = Code.SERVER_BUSY

    def __init__(self, retry_after_s) -> None:
        self.retry_after_s = retry_after_s


def test_a_non_numeric_retry_after_does_not_abort_the_pass(tmp_path):
    """The whole pass used to die on it, on every pass, on the same msg: the
    ones behind it were never attempted, `sent/` was never demoted again, and
    `_run` swallowed the raise so the loop looked healthy while shipping
    nothing."""
    out = _capture()
    send = _Recorder(default=_BusyWith("60s"))
    store, c = _cleaner(tmp_path, send, sent_timeout_s=0.0)
    for n in (1, 2, 3):
        store.put_pending(_rec(n))
    store.put_pending(_rec(9))
    store.mark_sent(9)
    counts = c.sweep_once()
    assert send.seen == [1, 2, 3], "msgs behind the bad one were never attempted"
    assert counts["retried"] == 3
    assert counts["demoted"] == 1, "the pass died before the demotion phase"
    assert store.list("pending") == [1, 2, 3, 9]
    assert "cleaner_retry_after_invalid" in out.getvalue()


def test_an_unusable_retry_after_falls_back_to_the_curve(tmp_path):
    """Not to zero: an un-coercible value is no instruction at all, and
    treating it as one hammers the msg every sweep."""
    clock = _Clock()
    send = _Recorder(default=_BusyWith(object()))
    store, c = _cleaner(tmp_path, send, retry_num=5, backoff_base_s=10.0,
                        _monotonic_fn=clock, _jitter_fn=lambda span: span)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert send.count == 1
    clock.advance(9.0)
    c.sweep_once()
    assert send.count == 1, "an unusable Retry-After skipped the backoff curve"
    clock.advance(2.0)
    c.sweep_once()
    assert send.count == 2


def test_a_numeric_string_retry_after_is_still_honoured(tmp_path):
    clock = _Clock()
    send = _Recorder(default=_BusyWith("30"))
    store, c = _cleaner(tmp_path, send, retry_num=1, _monotonic_fn=clock,
                        retry_after_cap_s=60.0)
    store.put_pending(_rec(1))
    c.sweep_once()
    clock.advance(29.0)
    c.sweep_once()
    assert send.count == 1
    clock.advance(2.0)
    c.sweep_once()
    assert send.count == 2


def test_a_bare_code_without_retry_after_still_works(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert store.list("sent") == [1]


def test_a_send_result_namedtuple_is_understood(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: SendResult(Code.OK, "", None))
    store.put_pending(_rec(1))
    c.sweep_once()
    assert store.list("sent") == [1]


def test_an_unrecognised_send_result_keeps_the_msg_pending(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: object())
    store.put_pending(_rec(1))
    assert c.sweep_once()["retried"] == 1
    assert store.list("pending") == [1]


def test_an_unrecognised_send_result_is_correlatable(tmp_path):
    """Every line this loop logs says who it is about (§10.4) -- a bundle from
    a process with two producers is otherwise unreadable."""
    out = _capture()
    store, c = _cleaner(tmp_path, lambda rec: object(), producer_id="prod-7",
                        server_alias="ingest_prod")
    store.put_pending(_rec(1))
    c.sweep_once()
    lines = [ln for ln in out.getvalue().splitlines()
             if "cleaner_send_result_invalid" in ln]
    assert lines, "expected the invalid-result line"
    assert '"producer_id": "prod-7"' in lines[0]
    assert '"server_alias": "ingest_prod"' in lines[0]


# -- §8.1 verdicts -------------------------------------------------------


def test_a_rejected_msg_becomes_nacked_not_a_forever_retry(tmp_path):
    send = _Recorder(default=Code.REJECTED)
    store, c = _cleaner(tmp_path, send)
    store.put_pending(_rec(1))
    c.sweep_once()
    c.sweep_once()
    assert store.list("nacked") == [1]
    assert send.count == 1


def test_too_large_becomes_nacked(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.TOO_LARGE)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert store.list("nacked") == [1]
    assert store.get(1).nack_reason == str(Code.TOO_LARGE)


def test_already_processed_is_purged(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.ALREADY_PROCESSED)
    store.put_pending(_rec(1))
    assert c.sweep_once()["purged"] == 1
    assert store.total() == 0


def test_unauthorized_halts_the_pass_and_keeps_every_msg_pending(tmp_path):
    send = _Recorder(default=Code.UNAUTHORIZED)
    store, c = _cleaner(tmp_path, send)
    for n in (1, 2, 3):
        store.put_pending(_rec(n))
    c.sweep_once()
    assert send.count == 1, "kept hammering a key the server already refused"
    assert store.list("pending") == [1, 2, 3]


def test_unauthorized_is_retried_on_a_later_pass(tmp_path):
    """'PENDING, halted' is not 'dropped' — re-registration may have fixed it."""
    clock = _Clock()
    send = _Recorder(default=Code.UNAUTHORIZED)
    store, c = _cleaner(tmp_path, send, retry_num=1, _monotonic_fn=clock)
    store.put_pending(_rec(1))
    c.sweep_once()
    c.sweep_once()
    assert send.count == 2


# -- demotion (§2.1, §4.4) ----------------------------------------------


def test_demotion_is_measured_from_sent_at(tmp_path):
    now = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=300.0,
                        _now_fn=lambda: now)
    store.put_pending(_rec(1))
    store.put_pending(_rec(2))
    # 1 is 299s old (fresh), 2 is 301s old (stale).
    _plant_sent(tmp_path, 1, "2026-07-26T11:55:01Z")
    _plant_sent(tmp_path, 2, "2026-07-26T11:54:59Z")
    assert c.sweep_once()["demoted"] == 1
    assert store.list("sent") == [1]
    assert store.list("pending") == [2]


def _plant_sent(root, msg_id: int, sent_at: str | None) -> None:
    """Put a record straight into `sent/` with a chosen `sent_at`."""
    rec = _rec(msg_id, sent_at=sent_at)
    (root / "pending" / f"{msg_id}.json").unlink(missing_ok=True)
    (root / "sent" / f"{msg_id}.json").write_text(rec.model_dump_json(),
                                                  encoding="utf-8")


def test_a_sent_record_without_sent_at_is_demoted_not_parked(tmp_path):
    """It can never age out, so leaving it is a permanent park."""
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=999999.0)
    store.put_pending(_rec(1))
    _plant_sent(tmp_path, 1, None)
    assert c.sweep_once()["demoted"] == 1
    assert store.list("pending") == [1]


def test_a_sent_record_with_an_unparseable_sent_at_is_demoted(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=999999.0)
    store.put_pending(_rec(1))
    _plant_sent(tmp_path, 1, "yesterday-ish")
    assert c.sweep_once()["demoted"] == 1
    assert store.list("pending") == [1]


def test_a_naive_sent_at_is_read_as_utc(tmp_path):
    """§3's field is UTC. Read as anything else -- or discarded as unusable --
    a fresh record demotes and gets resent while the server still has it in
    flight, so the fresh one below is the half that matters."""
    now = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=300.0,
                        _now_fn=lambda: now)
    store.put_pending(_rec(1))
    store.put_pending(_rec(2))
    _plant_sent(tmp_path, 1, "2026-07-26T11:59:50")     # 10s old, no offset
    _plant_sent(tmp_path, 2, "2026-07-26T11:00:00")     # 1h old, no offset
    assert c.sweep_once()["demoted"] == 1
    assert store.list("sent") == [1]
    assert store.list("pending") == [2]


def test_a_naive_clock_does_not_abort_the_pass(tmp_path):
    """`_now_fn` is injected too. A naive `now` against an aware `sent_at`
    raises `TypeError` out of the subtraction, which would take the rest of
    the demotions, the gauges and the `max_pending` alarm with it."""
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=300.0,
                        _now_fn=lambda: datetime(2026, 7, 26, 12, 0, 0))
    store.put_pending(_rec(1))
    _plant_sent(tmp_path, 1, "2026-07-26T11:59:50Z")
    assert c.sweep_once()["demoted"] == 1        # unusable -> demoted, not raised
    assert store.list("pending") == [1]


def test_a_sent_at_in_the_future_does_not_demote(tmp_path):
    """A clock skewed forward must not read as 'infinitely stale'."""
    now = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=300.0,
                        _now_fn=lambda: now)
    store.put_pending(_rec(1))
    _plant_sent(tmp_path, 1, "2026-07-26T12:10:00Z")
    assert c.sweep_once()["demoted"] == 0


def test_retry_runs_before_demotion_in_one_pass(tmp_path):
    """Reversed, `sent_timeout_s` silently becomes 'one sweep' for that msg."""
    send = _Recorder(default=Code.OK)
    store, c = _cleaner(tmp_path, send, sent_timeout_s=0.0)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    time.sleep(0.01)
    counts = c.sweep_once()
    assert (counts["retried"], counts["demoted"]) == (0, 1)
    assert send.seen == []
    assert c.sweep_once()["retried"] == 1


def test_demotion_does_not_touch_nacked(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=0.0)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    store.mark_nacked(1, "bad")
    assert c.sweep_once()["demoted"] == 0
    assert store.list("nacked") == [1]


# -- the epoch sweep (§4.3) ---------------------------------------------


def test_an_epoch_change_sweeps_all_of_sent_back_to_pending(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=999999.0)
    for n in (1, 2, 3):
        store.put_pending(_rec(n))
        store.mark_sent(n)
    c.on_epoch("boot-2")
    assert c.sweep_once()["demoted"] == 3
    assert store.list("pending") == [1, 2, 3]
    assert store.list("sent") == []


def test_without_an_epoch_change_sent_is_not_swept(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=999999.0)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    assert c.sweep_once()["demoted"] == 0
    assert store.list("sent") == [1]


def test_the_same_epoch_never_sweeps_twice(tmp_path):
    """Sweeping on every epoch frame resends the whole in-flight set on every
    reconnect blip."""
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=999999.0)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    c.on_epoch("boot-1")
    assert c.sweep_once()["demoted"] == 1
    store.mark_sent(1)
    c.on_epoch("boot-1")                     # same server, reconnect only
    assert c.sweep_once()["demoted"] == 0
    assert store.list("sent") == [1]


def test_a_second_epoch_change_sweeps_again(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=999999.0)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    c.on_epoch("boot-1")
    c.sweep_once()
    store.mark_sent(1)
    c.on_epoch("boot-2")
    assert c.sweep_once()["demoted"] == 1


def test_a_blank_epoch_is_ignored(tmp_path):
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=999999.0)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    c.on_epoch("")
    assert c.sweep_once()["demoted"] == 0
    assert c.epoch is None


def test_the_epoch_sweep_short_circuits_the_pass(tmp_path):
    """§4.3's herd control: the resends it just created ship on the next tick,
    under `max_in_flight`, not in the same breath."""
    send = _Recorder(default=Code.OK)
    store, c = _cleaner(tmp_path, send, sent_timeout_s=999999.0)
    store.put_pending(_rec(1))
    store.put_pending(_rec(2))
    store.mark_sent(2)
    c.on_epoch("boot-2")
    counts = c.sweep_once()
    assert counts == {"retried": 0, "demoted": 1, "purged": 0}
    assert send.seen == []
    assert c.sweep_once()["retried"] == 2


def test_an_epoch_change_with_an_empty_sent_still_consumes_the_flag(tmp_path):
    send = _Recorder(default=Code.OK)
    store, c = _cleaner(tmp_path, send)
    store.put_pending(_rec(1))
    c.on_epoch("boot-2")
    assert c.sweep_once() == {"retried": 0, "demoted": 0, "purged": 0}
    assert c.sweep_once()["retried"] == 1


def test_the_epoch_sweep_clears_backoff_so_resends_are_not_held_back(tmp_path):
    clock = _Clock()
    send = _Recorder(default=Code.STORED_UNREACHABLE)
    store, c = _cleaner(tmp_path, send, retry_num=9, backoff_base_s=600.0,
                        _monotonic_fn=clock, _jitter_fn=lambda span: span)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert send.count == 1
    c.sweep_once()
    assert send.count == 1                   # parked on a 600s backoff
    c.on_epoch("boot-2")
    c.sweep_once()                           # the sweep pass itself
    c.sweep_once()
    assert send.count == 2


def test_the_epoch_sweep_pass_still_publishes_the_gauges_and_the_alarm(tmp_path):
    """The pass that just dumped all of `sent/` into `pending/` is the one an
    operator most needs the numbers from -- and the one most likely to have
    pushed `pending/` over the budget."""
    metrics.reset_metrics()
    out = _capture()
    store = MsgStore(tmp_path)
    window = _window_at(8, 5)
    c = Cleaner(store, window, lambda rec: Code.OK, sweep_interval_s=0.01,
                sent_timeout_s=999999.0, retry_num=3, max_pending=1,
                server_alias="ingest_prod")
    for n in (1, 2):
        store.put_pending(_rec(n))
        store.mark_sent(n)
    c.on_epoch("boot-2")
    assert c.sweep_once()["demoted"] == 2
    snap = metrics.metrics()
    assert list(snap["pending_count"].values()) == [2]
    assert list(snap["in_flight_count"].values()) == [3]
    assert "cleaner_max_pending" in out.getvalue()


def test_on_epoch_records_the_epoch(tmp_path):
    _, c = _cleaner(tmp_path, lambda rec: Code.OK)
    assert c.epoch is None
    c.on_epoch("boot-7")
    assert c.epoch == "boot-7"


# -- the window (§5.1) ---------------------------------------------------


def _window_at(size: int, taken: int) -> Window:
    window = Window(size)
    for _ in range(taken):
        assert window.acquire()
    return window


def test_a_successful_send_keeps_its_window_slot(tmp_path):
    store = MsgStore(tmp_path)
    window = _window_at(8, 0)
    c = Cleaner(store, window, lambda rec: Code.OK, sweep_interval_s=0.01,
                sent_timeout_s=999.0, retry_num=3)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert window.in_flight() == 1


def test_a_failed_send_releases_exactly_one_slot(tmp_path):
    """`release()` clamps at zero, so a double release only shows up against
    slots someone else is holding."""
    store = MsgStore(tmp_path)
    window = _window_at(8, 5)
    c = Cleaner(store, window, lambda rec: Code.STORED_UNREACHABLE,
                sweep_interval_s=0.01, sent_timeout_s=999.0, retry_num=3)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert window.in_flight() == 5


def test_a_nacked_verdict_releases_exactly_one_slot(tmp_path):
    store = MsgStore(tmp_path)
    window = _window_at(8, 5)
    c = Cleaner(store, window, lambda rec: Code.REJECTED, sweep_interval_s=0.01,
                sent_timeout_s=999.0, retry_num=3)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert window.in_flight() == 5


def test_a_purge_releases_exactly_one_slot(tmp_path):
    store = MsgStore(tmp_path)
    window = _window_at(8, 5)
    c = Cleaner(store, window, lambda rec: Code.ALREADY_PROCESSED,
                sweep_interval_s=0.01, sent_timeout_s=999.0, retry_num=3)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert window.in_flight() == 5


def test_demotion_releases_exactly_one_slot_per_msg(tmp_path):
    store = MsgStore(tmp_path)
    window = _window_at(8, 5)
    c = Cleaner(store, window, lambda rec: Code.OK, sweep_interval_s=0.01,
                sent_timeout_s=0.0, retry_num=3)
    for n in (1, 2):
        store.put_pending(_rec(n))
        store.mark_sent(n)
    time.sleep(0.01)
    assert c.sweep_once()["demoted"] == 2
    assert window.in_flight() == 3


def test_the_epoch_sweep_releases_exactly_one_slot_per_msg(tmp_path):
    store = MsgStore(tmp_path)
    window = _window_at(8, 5)
    c = Cleaner(store, window, lambda rec: Code.OK, sweep_interval_s=0.01,
                sent_timeout_s=999999.0, retry_num=3)
    for n in (1, 2):
        store.put_pending(_rec(n))
        store.mark_sent(n)
    c.on_epoch("boot-2")
    assert c.sweep_once()["demoted"] == 2
    assert window.in_flight() == 3


def test_a_half_done_nack_keeps_the_slot_for_the_record_it_left_sent(tmp_path):
    """`_nack` is two hops. If the second fails -- disk full, EIO, i.e. the
    outage this loop exists for -- the record really is in `sent/`, so the slot
    is genuinely taken and the later demotion is what gives it back. Releasing
    at the top of the branch as well invents one slot per pass, forever, until
    the window bounds nothing."""

    class _NoNack(MsgStore):
        def mark_nacked(self, msg_id, reason):
            raise OSError("EIO")

    store = _NoNack(tmp_path)
    window = _window_at(8, 5)
    c = Cleaner(store, window, lambda rec: Code.REJECTED, sweep_interval_s=0.01,
                sent_timeout_s=0.0, retry_num=3)
    store.put_pending(_rec(1))
    c.sweep_once()                     # nack fails half way, demotion undoes it
    assert store.list("pending") == [1]
    assert window.in_flight() == 5
    c.sweep_once()
    assert window.in_flight() == 5, "a release with no acquire, once per pass"


def test_a_failed_demotion_does_not_release_a_slot(tmp_path):
    """The record is still `SENT` and still in flight."""

    class _NoDemote(MsgStore):
        def demote_to_pending(self, msg_id):
            raise OSError("EIO")

    store = _NoDemote(tmp_path)
    window = _window_at(8, 5)
    c = Cleaner(store, window, lambda rec: Code.OK, sweep_interval_s=0.01,
                sent_timeout_s=0.0, retry_num=3)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    assert c.sweep_once()["demoted"] == 0
    assert window.in_flight() == 5


def test_a_demote_target_that_vanished_does_not_release_a_slot(tmp_path):
    """An ACK purged it and the notification path already released its slot."""

    class _Gone(MsgStore):
        def get(self, msg_id):
            return None

    store = _Gone(tmp_path)
    window = _window_at(8, 5)
    c = Cleaner(store, window, lambda rec: Code.OK, sweep_interval_s=0.01,
                sent_timeout_s=0.0, retry_num=3)
    store.put_pending(_rec(1))
    store.mark_sent(1)
    assert c.sweep_once()["demoted"] == 0
    assert window.in_flight() == 5


def test_the_window_bounds_how_many_ship_per_pass(tmp_path):
    send = _Recorder(default=Code.OK)
    store = MsgStore(tmp_path)
    window = Window(2)
    c = Cleaner(store, window, send, sweep_interval_s=0.01,
                sent_timeout_s=999.0, retry_num=3)
    for n in (1, 2, 3, 4):
        store.put_pending(_rec(n))
    assert c.sweep_once()["retried"] == 2
    assert store.list("pending") == [3, 4]
    window.release()
    window.release()
    assert c.sweep_once()["retried"] == 2
    assert store.list("pending") == []


def test_a_full_window_never_loses_a_msg(tmp_path):
    store = MsgStore(tmp_path)
    window = _window_at(1, 1)
    c = Cleaner(store, window, lambda rec: Code.OK, sweep_interval_s=0.01,
                sent_timeout_s=999.0, retry_num=3)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert store.list("pending") == [1]
    window.release()
    c.sweep_once()
    assert store.list("sent") == [1]


# -- max_pending: an alarm, never a rejection (§5.1, §4.4) --------------


def test_the_max_pending_alarm_fires(tmp_path):
    out = _capture()
    store, c = _cleaner(tmp_path, lambda rec: Code.STORED_UNREACHABLE,
                        max_pending=1)
    store.put_pending(_rec(1))
    store.put_pending(_rec(2))
    c.sweep_once()
    assert "cleaner_max_pending" in out.getvalue()
    assert '"pending_count": 2' in out.getvalue()


def test_the_alarm_does_not_fire_at_the_limit(tmp_path):
    out = _capture()
    store, c = _cleaner(tmp_path, lambda rec: Code.STORED_UNREACHABLE,
                        max_pending=2)
    store.put_pending(_rec(1))
    store.put_pending(_rec(2))
    c.sweep_once()
    assert "cleaner_max_pending" not in out.getvalue()


def test_the_alarm_never_rejects_or_drops_a_msg(tmp_path):
    """Rejection is `send()`'s `slot_full`, before storing (§5.1, D22)."""
    store, c = _cleaner(tmp_path, lambda rec: Code.STORED_UNREACHABLE,
                        max_pending=1)
    for n in (1, 2, 3):
        store.put_pending(_rec(n))
    c.sweep_once()
    assert store.list("pending") == [1, 2, 3]


# -- send_fn is injected code -------------------------------------------


def test_a_raising_send_fn_does_not_stop_the_sweep(tmp_path):
    def send_fn(rec):
        if rec.msg_id == 1:
            raise RuntimeError("boom")
        return Code.OK

    store, c = _cleaner(tmp_path, send_fn)
    store.put_pending(_rec(1))
    store.put_pending(_rec(2))
    c.sweep_once()
    assert store.list("sent") == [2]
    assert store.list("pending") == [1]


def test_a_raising_send_fn_releases_its_window_slot(tmp_path):
    store = MsgStore(tmp_path)
    window = _window_at(8, 5)

    def send_fn(rec):
        raise RuntimeError("boom")

    c = Cleaner(store, window, send_fn, sweep_interval_s=0.01,
                sent_timeout_s=999.0, retry_num=3)
    store.put_pending(_rec(1))
    c.sweep_once()
    assert window.in_flight() == 5


def test_a_send_fn_exception_message_is_never_logged(tmp_path):
    """Task 11 shipped a Critical here: handler exception text carried an
    api_key and an SSN into a support bundle (§10.4)."""
    out = _capture()
    secret = "sk-live-abcdef012345 ssn 123-45-6789"

    def send_fn(rec):
        raise RuntimeError(secret)

    store, c = _cleaner(tmp_path, send_fn)
    store.put_pending(_rec(1))
    c.sweep_once()
    logged = out.getvalue()
    assert "sk-live-abcdef012345" not in logged
    assert "123-45-6789" not in logged
    assert "RuntimeError" in logged


def test_the_payload_is_never_logged(tmp_path):
    out = _capture()
    store, c = _cleaner(tmp_path, lambda rec: Code.REJECTED, max_pending=0)
    rec = _rec(1)
    rec.envelope.payload = {"ssn": "123-45-6789"}
    store.put_pending(rec)
    c.sweep_once()
    assert out.getvalue().strip(), "expected a log line"
    assert "123-45-6789" not in out.getvalue()


# -- concurrency ---------------------------------------------------------


def test_the_store_lock_is_not_held_across_send_fn(tmp_path):
    """A `send_fn` blocking for `req_timeout_s` under the store lock stalls
    every producer thread in `send()` (§6.4)."""
    in_send = threading.Event()
    proceed = threading.Event()
    stored = threading.Event()

    def send_fn(rec):
        in_send.set()
        proceed.wait(5.0)
        return Code.OK

    store, c = _cleaner(tmp_path, send_fn)
    store.put_pending(_rec(1))
    sweeper = threading.Thread(target=c.sweep_once, daemon=True)
    sweeper.start()
    assert in_send.wait(5.0)

    def producer():
        store.put_pending(_rec(99))
        stored.set()

    other = threading.Thread(target=producer, daemon=True)
    other.start()
    free = stored.wait(2.0)
    proceed.set()
    sweeper.join(5.0)
    other.join(5.0)
    assert free, "a producer thread blocked on the store lock during send_fn"


def test_an_ack_that_purges_mid_post_does_not_break_the_pass(tmp_path):
    """The SSE reader can ACK and purge a record while this POST is on the
    wire. Nothing is in flight for us then, so the slot goes back and the
    pass carries on."""
    store = MsgStore(tmp_path)
    window = _window_at(8, 5)

    def send_fn(rec):
        store.purge(rec.msg_id)
        return Code.OK

    c = Cleaner(store, window, send_fn, sweep_interval_s=0.01,
                sent_timeout_s=999.0, retry_num=3)
    store.put_pending(_rec(1))
    store.put_pending(_rec(2))
    assert c.sweep_once()["retried"] == 2
    assert store.total() == 0
    assert window.in_flight() == 5


def test_the_backoff_table_forgets_msgs_that_left_pending(tmp_path):
    """In-memory retry state for msgs that are gone is a slow leak in a
    process that runs for months. Asserted on the private table because it has
    no other observable effect -- that is the point of the invariant."""
    clock = _Clock()
    store, c = _cleaner(tmp_path, lambda rec: Code.STORED_UNREACHABLE,
                        retry_num=5, _monotonic_fn=clock,
                        _jitter_fn=lambda span: span)
    for n in (1, 2, 3):
        store.put_pending(_rec(n))
    c.sweep_once()
    assert len(c._retries) == 3
    for n in (1, 2, 3):
        store.purge(n)
    c.sweep_once()
    assert c._retries == {}


def test_two_passes_do_not_interleave(tmp_path):
    """Two overlapping passes would double every POST and race the window.

    The second pass is given a real window in which to arrive: it is caught by
    an Event nothing has set yet, so the wait actually elapses. Waiting on the
    *first* pass's Event instead returns True immediately and proves nothing.
    """
    entered = []
    lock = threading.Lock()
    first_in_send = threading.Event()
    second_in_send = threading.Event()
    proceed = threading.Event()

    def send_fn(rec):
        with lock:
            entered.append(rec.msg_id)
            first = len(entered) == 1
        if first:
            first_in_send.set()
            proceed.wait(5.0)
        else:
            second_in_send.set()
        return Code.OK

    store, c = _cleaner(tmp_path, send_fn)
    store.put_pending(_rec(1))
    first = threading.Thread(target=c.sweep_once, daemon=True)
    first.start()
    assert first_in_send.wait(5.0)
    second = threading.Thread(target=c.sweep_once, daemon=True)
    second.start()
    overlapped = second_in_send.wait(0.3)
    proceed.set()
    first.join(5.0)
    second.join(5.0)
    assert not overlapped, "a second pass reached send_fn while one was running"
    assert entered == [1], "the msg was POSTed twice"


# -- thread lifecycle ----------------------------------------------------


def test_start_sweeps_on_a_background_thread(tmp_path):
    swept = threading.Event()
    store, c = _cleaner(tmp_path, lambda rec: (swept.set(), Code.OK)[1])
    store.put_pending(_rec(1))
    c.start()
    try:
        assert swept.wait(5.0)
    finally:
        c.stop()


def test_the_thread_is_a_named_daemon(tmp_path):
    _, c = _cleaner(tmp_path, lambda rec: Code.OK, sweep_interval_s=0.05)
    c.start()
    try:
        threads = _cleaner_threads()
        assert len(threads) == 1
        assert threads[0].daemon
    finally:
        c.stop()


def test_start_twice_does_not_spawn_two_loops(tmp_path):
    _, c = _cleaner(tmp_path, lambda rec: Code.OK, sweep_interval_s=0.05)
    c.start()
    c.start()
    try:
        assert len(_cleaner_threads()) == 1
    finally:
        c.stop()


def test_stop_joins_the_thread(tmp_path):
    _, c = _cleaner(tmp_path, lambda rec: Code.OK, sweep_interval_s=0.05)
    c.start()
    c.stop()
    assert _cleaner_threads() == []


def test_stop_leaves_no_thread_behind_after_many_cycles(tmp_path):
    _, c = _cleaner(tmp_path, lambda rec: Code.OK, sweep_interval_s=0.01)
    before = threading.active_count()
    for _ in range(5):
        c.start()
        c.stop()
    assert _cleaner_threads() == []
    assert threading.active_count() <= before


def test_stop_is_idempotent(tmp_path):
    _, c = _cleaner(tmp_path, lambda rec: Code.OK, sweep_interval_s=0.05)
    c.start()
    c.stop()
    c.stop()
    assert _cleaner_threads() == []


def test_stop_without_start_does_not_raise(tmp_path):
    _, c = _cleaner(tmp_path, lambda rec: Code.OK)
    c.stop()


def test_stop_does_not_wait_out_the_sweep_interval(tmp_path):
    """The loop parks on `Event.wait`, never on `sleep`."""
    _, c = _cleaner(tmp_path, lambda rec: Code.OK, sweep_interval_s=30.0)
    c.start()
    started = time.monotonic()
    c.stop()
    assert time.monotonic() - started < 2.0
    assert _cleaner_threads() == []


def test_stop_cuts_the_loops_pass_short_instead_of_waiting_out_the_backlog(tmp_path):
    """A stop during an outage must not have to wait for every queued POST.

    This is the *background loop's* pass. A direct `sweep_once()` deliberately
    runs to the end even when the flag is set -- see the test below.
    """
    seen = []
    stopped = threading.Event()

    def send_fn(rec):
        seen.append(rec.msg_id)
        c.stop()                             # on the cleaner thread: no join
        stopped.set()
        return Code.STORED_UNREACHABLE

    store, c = _cleaner(tmp_path, send_fn, sweep_interval_s=0.01)
    for n in range(1, 11):
        store.put_pending(_rec(n))
    c.start()
    try:
        assert stopped.wait(5.0)
        deadline = time.monotonic() + 5.0
        while _cleaner_threads() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert _cleaner_threads() == []
        assert seen == [1]
    finally:
        c.stop()


def test_a_direct_sweep_after_stop_still_flushes_pending(tmp_path):
    """§2.4's `close()` is exactly this sequence: stop accepting sends, then
    drain `pending/` until it is empty. `stop()` leaves the flag set until the
    next `start()`, so a drain that skipped every msg on it would report the
    whole backlog unresolved while the records sat there, shippable."""
    send = _Recorder(default=Code.OK)
    store, c = _cleaner(tmp_path, send)
    c.stop()
    store.put_pending(_rec(1))
    store.put_pending(_rec(2))
    assert c.sweep_once()["retried"] == 2
    assert send.seen == [1, 2]
    assert store.list("pending") == []


def test_stop_from_inside_send_fn_on_the_loop_thread_does_not_hang(tmp_path):
    """The cleaner thread cannot join itself -- and the caller must not be the
    one to find that out: `stop()` swallowing nothing, the `RuntimeError` would
    surface in whatever called it, here disguised as a failed send."""
    called = threading.Event()
    raised = []

    def send_fn(rec):
        try:
            c.stop(timeout_s=1.0)
        except BaseException as exc:               # noqa: BLE001
            raised.append(type(exc).__name__)
        called.set()
        return Code.OK

    store, c = _cleaner(tmp_path, send_fn, sweep_interval_s=0.01)
    store.put_pending(_rec(1))
    c.start()
    assert called.wait(5.0)
    deadline = time.monotonic() + 5.0
    while _cleaner_threads() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _cleaner_threads() == []
    assert raised == [], f"stop() raised on the cleaner thread: {raised}"


def test_stop_does_not_hang_on_a_send_fn_that_will_not_return(tmp_path):
    """The join is bounded. An app's `send_fn` wedged on a socket must not be
    able to hold a shutdown open."""
    in_send = threading.Event()
    proceed = threading.Event()

    def send_fn(rec):
        in_send.set()
        proceed.wait(10.0)
        return Code.OK

    store, c = _cleaner(tmp_path, send_fn, sweep_interval_s=0.01)
    store.put_pending(_rec(1))
    c.start()
    try:
        assert in_send.wait(5.0)
        started = time.monotonic()
        c.stop(timeout_s=0.05)
        assert time.monotonic() - started < 2.0, "stop() waited out the pass"
    finally:
        proceed.set()
        c.stop()


def test_a_timed_out_stop_does_not_let_the_next_start_spawn_a_second_thread(tmp_path):
    """`start()`'s guard is the only thing between a thread that outlived its
    join and a second sweeper on the same store -- and two sweepers double
    every POST."""
    in_send = threading.Event()
    proceed = threading.Event()

    def send_fn(rec):
        in_send.set()
        proceed.wait(10.0)
        return Code.OK

    store, c = _cleaner(tmp_path, send_fn, sweep_interval_s=0.01)
    store.put_pending(_rec(1))
    c.start()
    try:
        assert in_send.wait(5.0)
        c.stop(timeout_s=0.05)
        assert _cleaner_threads(), "precondition: the thread outlived the join"
        c.start()
        assert len(_cleaner_threads()) == 1
    finally:
        proceed.set()
        c.stop()
        deadline = time.monotonic() + 5.0
        while _cleaner_threads() and time.monotonic() < deadline:
            time.sleep(0.01)


def test_start_does_not_revive_the_flag_a_previous_run_was_stopped_with(tmp_path):
    """A fresh Event per run, never `clear()`. Not observable through the API
    -- `start()` refuses to spawn while the old thread is alive, which is the
    only case that can reach it -- so the invariant is asserted directly."""
    _, c = _cleaner(tmp_path, lambda rec: Code.OK, sweep_interval_s=0.05)
    c.start()
    c.stop()
    stopped = c._stop
    c.start()
    try:
        assert c._stop is not stopped
        assert stopped.is_set(), "a previous run's stop flag was cleared"
    finally:
        c.stop()


def test_a_raising_sweep_does_not_kill_the_loop(tmp_path):
    """`store` blowing up must not silently end the at-least-once guarantee."""
    calls = []

    class _Boom(MsgStore):
        def list(self, state):
            calls.append(state)
            if len(calls) < 3:
                raise OSError("disk hiccup")
            return super().list(state)

    store = _Boom(tmp_path)
    c = Cleaner(store, Window(4), lambda rec: Code.OK, sweep_interval_s=0.01,
                sent_timeout_s=999.0, retry_num=3)
    store.put_pending(_rec(1))
    shipped = tmp_path / "sent" / "1.json"
    c.start()
    try:
        deadline = time.monotonic() + 5.0
        while not shipped.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert shipped.exists(), "the loop died on the first raising pass"
        assert len(calls) > 2
    finally:
        c.stop()


# -- metrics (§10.5, closed list) ---------------------------------------


def test_retries_and_demotions_are_counted(tmp_path):
    metrics.reset_metrics()
    store, c = _cleaner(tmp_path, lambda rec: Code.STORED_UNREACHABLE,
                        sent_timeout_s=0.0)
    store.put_pending(_rec(1))
    store.put_pending(_rec(2))
    store.mark_sent(2)
    time.sleep(0.01)
    c.sweep_once()
    snap = metrics.metrics()
    assert sum(snap["cleaner_retries_total"].values()) == 1
    assert sum(snap["cleaner_demotions_total"].values()) == 1


def test_the_epoch_sweep_counts_msgs_but_not_sweeps(tmp_path):
    """`sse.py` owns `epoch_sweeps_total`; double counting it makes restarts
    look twice as frequent as they are."""
    metrics.reset_metrics()
    store, c = _cleaner(tmp_path, lambda rec: Code.OK, sent_timeout_s=999999.0)
    for n in (1, 2):
        store.put_pending(_rec(n))
        store.mark_sent(n)
    c.on_epoch("boot-2")
    c.sweep_once()
    snap = metrics.metrics()
    assert sum(snap["epoch_swept_msgs_total"].values()) == 2
    assert "epoch_sweeps_total" not in snap


def test_the_gauges_are_published_every_pass(tmp_path):
    metrics.reset_metrics()
    store = MsgStore(tmp_path)
    window = _window_at(8, 3)
    c = Cleaner(store, window, lambda rec: Code.STORED_UNREACHABLE,
                sweep_interval_s=0.01, sent_timeout_s=999.0, retry_num=3,
                server_alias="ingest_prod")
    store.put_pending(_rec(1))
    store.put_pending(_rec(2))
    c.sweep_once()
    snap = metrics.metrics()
    assert list(snap["pending_count"].values()) == [2]
    assert list(snap["in_flight_count"].values()) == [3]
    assert '"server_alias":"ingest_prod"' in list(snap["pending_count"])[0]


def test_the_cleaner_does_not_recount_the_send_path(tmp_path):
    """One POST is the only thing that knows its own latency —
    `transport.post_once` owns `msgs_sent_total` and `send_latency_ms`."""
    metrics.reset_metrics()
    store, c = _cleaner(tmp_path, lambda rec: Code.OK)
    store.put_pending(_rec(1))
    c.sweep_once()
    snap = metrics.metrics()
    assert "msgs_sent_total" not in snap
    assert "send_latency_ms" not in snap
