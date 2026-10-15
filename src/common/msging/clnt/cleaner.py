"""The cleaner loop (design §4.4) -- the client's at-least-once promise.

`send()` makes exactly one POST and returns (§2.2). Everything that happens
*after* that one attempt is here: retry, `SENT -> PENDING` demotion, the epoch
sweep, and the `max_pending` alarm. If this loop stops running, msgs that lost
their outcome in transit sit in `sent/` forever looking handled, and msgs whose
POST never landed sit in `pending/` with nothing to ship them.

One pass, in §4.4's order, and the order is load-bearing:

    epoch changed -> sweep all of sent/ -> pending/   (first, short-circuits)
    ALREADY_PROCESSED -> purge                        (§8.1, reserved for §9.1)
    PENDING  -> send, while in_flight < max_in_flight
    SENT     -> PENDING   if older than sent_timeout_s
    NACKED   -> leave     (unresolved = RETAIN, §2.2)
    pending count > max_pending -> alarm

Retry runs *before* demotion. Reversed, a msg demoted this pass would be
resent in the same pass and land straight back in `sent/`, so `sent_timeout_s`
would silently become "one sweep" for it and the demotion would be invisible
to anything watching the store.

**`retry_num` bounds a cycle, not a msg (§4.4, D27).** This is the rule the
whole guarantee rests on, and it is the one an implementation gets wrong by
accident: a counter that "gives up" at `retry_num` drops a msg the producer
was promised. Here the counter resets at `retry_num` and a new cycle starts --
the backoff curve restarts, the msg does not go anywhere. There is no code
path in this module that deletes or permanently parks a `PENDING` record.
`max_pending` is what bounds disk, not a retry limit (§5.1).

**Backoff is in memory, not on the record.** `attempts` on the record is
bumped by `mark_sent`, i.e. only when a POST *succeeded*, so it cannot pace a
failing one. Pacing a failing msg from disk would also mean rewriting every
`pending/` record on every sweep of an outage -- the exact moment the disk is
already the scarce resource. The table is pruned to what is still in
`pending/` each pass, so it cannot grow without bound, and losing it on
restart costs nothing: §2.3's first pass is allowed to retry immediately.

**`server_busy` honours `Retry-After`** (§8.1) instead of the curve, and the
value is clamped -- see `_deferral_s`. A peer that answers `Retry-After:
86400` must not be able to park this loop for a day.

**The store lock is never held across `send_fn`.** Every store call here is
one short, self-contained mutation; the network call happens between them,
holding nothing. `MsgStore` serialises its mutators on a single process-wide
lock, so a `send_fn` blocking for `req_timeout_s` inside that lock would stall
every producer thread calling `send()` -- the client half is threads (§6.4)
and `send()` is on the producer's own thread.

**Window slots.** §5.1 is explicit that a slot is released on any terminal
notification *and* on `SENT -> PENDING`. This module takes a slot before each
POST and releases it on exactly one of: the POST not landing, a verdict
(`rejected`/`too_large` -> NACKED), a purge, or a later demotion. A slot is
*kept* only when the msg actually became `SENT`, because that is what "in
flight" means. A leaked slot silently shrinks the window until nothing ships;
a double release makes the window stop bounding anything.

Counting: this module owns §10.5's `cleaner_retries_total`,
`cleaner_demotions_total`, `epoch_swept_msgs_total`, and the `pending_count` /
`in_flight_count` gauges (it is the layer that sees both numbers every pass,
and it knows the `server_alias` label -- see `window.py`'s note on why an
unlabelled gauge is wrong). It must **not** count `epoch_sweeps_total`: the
SSE reader already did, and only it can see a link event (`sse.py`). It does
not re-count `msgs_sent_total` / `send_latency_ms` either -- one POST is the
only thing that knows its own latency, and `transport.post_once` owns those.
The `max_pending` alarm is a *log*, not a metric: §10.5 is a closed list.

Never logs a payload, an api_key, or a callback's exception message. A
`send_fn` is injected code and its exception text is app data -- one has
already been observed carrying an api_key and an SSN into a support bundle
(§10.4) -- so only the exception type and the raising source location are
logged.

Nothing here imports `srv/` (§6.4, enforced by
`tests/common/msging/test_boundaries.py`).
"""
from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from common.msging import metrics
from common.msging.clnt.store import MsgStore
from common.msging.clnt.window import Window
from common.msging.codes import Code
from common.msging.log import get_logger
# One implementation of "where was this raised", shared rather than copied --
# `sse.py` owns the same rule (log the type and the location, never the
# message) and a second copy drifts the moment either is edited.
from common.msging.clnt.sse import _origin
from common.msging.models import MsgRecord

_log = get_logger("msging.clnt.cleaner")

#: First step of the intra-cycle backoff curve, and its ceiling. Small,
#: because `sweep_interval_s` (5 s by default) is the real pacing floor -- a
#: msg is never retried more often than a sweep regardless of these.
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 60.0

#: Ceiling on a server's `Retry-After`. §8.1 says `server_busy` waits for it
#: instead of the curve, which hands a peer control of this loop's schedule: a
#: broken proxy answering `Retry-After: 86400` would park every msg for a day,
#: and a `sent_timeout_s`-shaped outage would go unnoticed behind it. Clamped
#: to something an operator would still call a retry.
RETRY_AFTER_CAP_S = 60.0

#: `ClientDefaults.max_pending` (§5.1). Only ever an alarm here -- rejection is
#: `send()`'s job, before the msg is stored (§5.1, D22).
DEFAULT_MAX_PENDING = 10000

_JOIN_TIMEOUT_S = 10.0

#: §8.1's two verdicts *on the msg itself*. Both mean no future attempt can
#: succeed -- a malformed envelope stays malformed, and a msg over
#: `max_msg_bytes` stays over it -- so retrying either forever would burn a
#: window slot every sweep for a msg the server will never take. They become
#: `NACKED`, which is the state that hands the app the decision (§2.2).
_VERDICTS = frozenset({Code.REJECTED, Code.TOO_LARGE})


@dataclass
class _Retry:
    """One msg's place in the current retry cycle. In memory only."""

    fails: int = 0
    not_before: float = 0.0


class Cleaner:
    """§4.4's background sweeper for one producer's store.

    Args:
        store: the producer's `MsgStore`. Every mutation here goes through it;
            this class holds no state that duplicates the folder.
        window: the `max_in_flight` window (§5.1). Acquired before each POST,
            released on every path that leaves the msg not in flight.
        send_fn: called as `send_fn(rec: MsgRecord)` to make **one** POST
            attempt. Returns either a `Code` or anything with a `.code` and an
            optional `.retry_after_s` -- `transport.post_once`'s `SendResult`
            is the intended argument. It must not retry internally (§2.2), and
            it is called with no lock held. An exception out of it is treated
            as `stored_unreachable`; it never stops the sweep.
        sweep_interval_s: seconds between passes on the background thread
            (§5.1). Also the pacing floor for any single msg's retries.
        sent_timeout_s: a `SENT` record older than this is demoted to
            `PENDING` (§5.1, §2.1). Measured from `sent_at`.
        retry_num: attempts in one backoff **cycle** (§5.1). At the cycle's
            end the counter resets and a new cycle begins -- it is never a
            give-up threshold (§4.4).
        max_pending: alarm threshold on `pending/` (§5.1). Strictly greater
            than this logs loudly. Never rejects anything -- `slot_full` is
            `send()`'s, and it happens before the msg is stored.
        backoff_base_s / backoff_cap_s: the intra-cycle curve, full-jittered.
        retry_after_cap_s: clamp on a server-supplied `Retry-After`.
        producer_id / server_alias: log and metric labels only (§10.4, §10.5).

    Raises:
        ValueError: `retry_num` < 1, or a negative `sweep_interval_s` /
            `sent_timeout_s`. Config errors raise; runtime conditions return
            a code (§8.3). `retry_num=0` would make every failure end a cycle
            *and* skip the curve entirely -- a hot loop, not a policy.
    """

    def __init__(self, store: MsgStore, window: Window, send_fn,
                 sweep_interval_s: float, sent_timeout_s: float,
                 retry_num: int, *,
                 max_pending: int = DEFAULT_MAX_PENDING,
                 backoff_base_s: float = BACKOFF_BASE_S,
                 backoff_cap_s: float = BACKOFF_CAP_S,
                 retry_after_cap_s: float = RETRY_AFTER_CAP_S,
                 producer_id: str | None = None,
                 server_alias: str | None = None,
                 _now_fn=None, _monotonic_fn=None, _jitter_fn=None) -> None:
        if int(retry_num) < 1:
            raise ValueError(f"retry_num must be >= 1, got {retry_num}")
        if float(sweep_interval_s) < 0 or float(sent_timeout_s) < 0:
            raise ValueError(
                "sweep_interval_s and sent_timeout_s must be >= 0, got "
                f"{sweep_interval_s} / {sent_timeout_s}")
        self._store = store
        self._window = window
        self._send_fn = send_fn
        self._sweep_interval_s = float(sweep_interval_s)
        self._sent_timeout_s = float(sent_timeout_s)
        self._retry_num = int(retry_num)
        self._max_pending = int(max_pending)
        self._backoff_base_s = float(backoff_base_s)
        self._backoff_cap_s = float(backoff_cap_s)
        self._retry_after_cap_s = float(retry_after_cap_s)
        self._producer_id = producer_id
        self._server_alias = server_alias

        self._now = _now_fn or (lambda: datetime.now(timezone.utc))
        self._monotonic = _monotonic_fn or time.monotonic
        self._jitter = _jitter_fn or (lambda span: random.uniform(0.0, span))

        self._retries: dict[int, _Retry] = {}
        # Guards the epoch handshake and the thread handle. Never held across
        # a store mutation or `send_fn`.
        self._lock = threading.Lock()
        # Serialises passes so the background thread and a test's direct
        # `sweep_once()` cannot interleave two halves of one policy.
        self._pass_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        # Clear, not set: a `sweep_once()` on a cleaner that was never started
        # is a supported call (it is how §2.3's first pass runs, and how tests
        # drive this class), so it must not look pre-stopped.
        self._stop = threading.Event()
        self._epoch: str | None = None
        self._epoch_dirty = False

    # ------------------------------------------------------------- public

    @property
    def epoch(self) -> str | None:
        """The last `server_epoch` handed to `on_epoch`, or None."""
        return self._epoch

    def on_epoch(self, epoch: str) -> None:
        """SSE callback for a **changed** `server_epoch` (§4.3).

        Records the change and returns; the sweep itself happens at the top of
        the next pass. Two reasons it is not done inline: this runs on the SSE
        reader thread, and a bulk `sent/ -> pending/` rename there would block
        the reader from taking outcomes for as long as it takes; and §4.4 puts
        the sweep in the pass, ahead of the sending, so the resends it creates
        are shaped by `max_in_flight` on the very next pass (§4.3's herd
        control) rather than racing it.

        Only a *changed* epoch sweeps. `sse.py` already filters, and this
        filters again on its own state: sweeping on every epoch frame would
        drag all of `sent/` back to `pending/` on every reconnect, resending
        the entire in-flight set each time the link blips.
        """
        with self._lock:
            if not epoch or epoch == self._epoch:
                return
            self._epoch = epoch
            self._epoch_dirty = True

    def sweep_once(self, deadline: float | None = None) -> dict:
        """Run exactly one pass. Returns `{"retried", "demoted", "purged"}`.

        Args:
            deadline: an optional `time.monotonic()` instant the *send phase*
                must not start another POST after. `None` (the default) means
                the pass ships the whole backlog, which is what §2.3's startup
                pass and `flush()` want.

        Synchronous and safe to call directly -- that is how §2.3's startup
        pass and the tests drive it. Passes do not overlap.

        A direct call runs a **full** pass whether or not the loop is stopped.
        The stop flag cuts the *background* loop's pass short; it must not cut
        this one, or §2.4's `close()` -- stop accepting sends, then flush
        `pending/` until it is empty -- would flush nothing and report every
        msg unresolved. `stop()` leaves the flag set until the next `start()`,
        so a `close()` that stops the loop and then drains synchronously is
        exactly the sequence that has to keep working.

        `deadline` exists because the stop flag is the wrong instrument for
        `close(timeout_s)`: a stopped loop still has to flush here, and a drain
        that only checks its deadline *between* passes overshoots by the whole
        backlog. `max_in_flight=64` msgs against a `req_timeout_s=10` server is
        ~640 s inside one pass, so a `close(5)` would return ten minutes late.
        Only the send phase is bounded -- the demotion phase is local renames,
        finishes in bounded time, and is the phase that hands window slots back.
        A POST already on the wire when the deadline passes still finishes, so
        the real bound is `deadline + req_timeout_s`, not `deadline`.
        """
        with self._pass_lock:
            return self._pass(None, deadline)

    def start(self) -> None:
        """Run `sweep_once()` every `sweep_interval_s` on a daemon thread.

        Idempotent: a second call while the loop is alive does not spawn a
        second one. Two sweepers on one store would double every POST.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            # A fresh Event per run rather than `clear()`: a previous thread
            # that outlived its join must not be revived by this start.
            stop = threading.Event()
            self._stop = stop
            thread = threading.Thread(target=self._run, args=(stop,),
                                      name="msging-cleaner", daemon=True)
            self._thread = thread
            thread.start()

    def stop(self, timeout_s: float = _JOIN_TIMEOUT_S) -> None:
        """Stop the loop and join the thread. Idempotent, and must not hang.

        The loop parks on `Event.wait`, never on `sleep`, so setting the flag
        wakes it at once instead of after up to `sweep_interval_s`. A loop pass
        already in flight also checks the flag between msgs, so a long backlog
        being retried against a dead server does not hold this call open for
        the length of the backlog. The join is bounded by `timeout_s` on top of
        that: a `send_fn` that never returns must not turn this into a hang.

        Safe from inside `send_fn`: that runs on the cleaner thread, which
        cannot join itself, so the join is skipped and the loop exits at its
        next check.

        The thread handle is dropped only once the thread has actually
        finished. A join that timed out leaves a thread still sweeping this
        store, and forgetting it would let the next `start()` spawn a second
        sweeper alongside it -- two sweepers double every POST, which is the
        one thing `start()`'s idempotency exists to prevent.
        """
        with self._lock:
            thread = self._thread
            self._stop.set()
        if thread is None or thread is threading.current_thread():
            return
        thread.join(timeout_s)
        if thread.is_alive():
            _log.warning("cleaner_stop_timeout", **self._who(),
                         timeout_s=timeout_s)
            return
        with self._lock:
            if self._thread is thread:
                self._thread = None

    # ---------------------------------------------------------- one pass

    def _pass(self, stop: threading.Event | None,
              deadline: float | None = None) -> dict:
        """One pass. `stop` is the background loop's flag, or None for a
        direct `sweep_once()` -- see that method on why they differ, and on
        what `deadline` bounds."""
        swept = self._take_epoch_sweep()
        if swept is not None:
            # §4.4: the epoch sweep short-circuits the pass. What it just
            # produced is a `pending/` full of resends, and shipping them in
            # the same breath is exactly the storm §4.3 spreads out; the next
            # tick sends them under `max_in_flight`.
            return self._finish({"retried": 0, "demoted": swept, "purged": 0})
        retried, purged = self._retry_pending(stop, deadline)
        demoted = self._demote_stale()
        return self._finish({"retried": retried, "demoted": demoted,
                             "purged": purged})

    def _finish(self, counts: dict) -> dict:
        """Gauges, the `max_pending` alarm, and §10.4's `cleaner_sweep` line."""
        pending = self._store.count("pending")
        metrics.gauge("pending_count", pending, server_alias=self._server_alias)
        metrics.gauge("in_flight_count", self._window.in_flight(),
                      server_alias=self._server_alias)
        if pending > self._max_pending:
            # An alarm, not a rejection (§5.1, D22): the msgs are already
            # durable and the promise holds. What it means is that the outage
            # has outlasted the disk budget and the producer needs to hear it.
            # Loud, and deliberately not a metric -- §10.5 is a closed list.
            _log.warning("cleaner_max_pending", **self._who(),
                         pending_count=pending, max_pending=self._max_pending)
        if any(counts.values()):
            _log.info("cleaner_sweep", **self._who(), **counts)
        return counts

    def _take_epoch_sweep(self) -> int | None:
        """All of `sent/` back to `pending/` if the epoch changed (§4.3).

        Returns the number moved, or None when there was nothing to sweep --
        the two are different: a changed epoch with an empty `sent/` still
        short-circuits the pass, because the flag was consumed either way.
        """
        with self._lock:
            if not self._epoch_dirty:
                return None
            self._epoch_dirty = False
            epoch = self._epoch
        moved = self._store.sweep_sent_to_pending()
        for _ in range(moved):
            # §5.1: a demotion frees the slot, or the window leaks one per msg
            # permanently after every server restart.
            self._window.release()
        # A different server process cannot owe anything to the old one's
        # backoff schedule, and holding resends back on it is exactly wrong
        # after a restart.
        self._retries.clear()
        metrics.add("epoch_swept_msgs_total", moved)
        # Not `epoch_sweeps_total` -- `sse.py` counted that when it saw the
        # change, and double counting it would make restarts look twice as
        # frequent as they are.
        _log.warning("cleaner_epoch_sweep", **self._who(), server_epoch=epoch,
                     swept=moved)
        return moved

    def _retry_pending(self, stop: threading.Event | None,
                       deadline: float | None = None) -> tuple[int, int]:
        """`PENDING -> send`, while a window slot is free. `(retried, purged)`."""
        pending = self._store.list("pending")
        retried = 0
        purged = 0
        for msg_id in pending:
            if stop is not None and stop.is_set():
                # `stop()` must not wait out a backlog of dead-server POSTs.
                # Only the loop's own flag cuts a pass short: a direct
                # `sweep_once()` passes None and always runs to the end.
                break
            if deadline is not None and self._monotonic() >= deadline:
                # `close(timeout_s)`'s budget is spent. Checked before the
                # POST, never after: what is left in `pending/` is durable and
                # `close()` reports it as unresolved (§2.4).
                break
            state = self._retries.get(msg_id)
            if state is not None and self._monotonic() < state.not_before:
                continue
            rec = self._store.get(msg_id)
            if rec is None:
                continue                      # purged under us; nothing owed
            if not self._window.acquire():
                # §4.4: send *while* in_flight < max_in_flight. Full window is
                # a normal steady state, not an error -- the rest of `pending/`
                # ships on a later pass.
                break
            retried += 1
            outcome = self._dispatch(msg_id, rec)
            if outcome is _PURGED:
                purged += 1
            elif outcome is _HALTED:
                break
        # Bounded memory: forget everything that left `pending/`.
        live = set(pending)
        self._retries = {k: v for k, v in self._retries.items() if k in live}
        if retried:
            metrics.add("cleaner_retries_total", retried)
        return retried, purged

    def _dispatch(self, msg_id: int, rec: MsgRecord):
        """One attempt, and the state move its code implies (§8.1).

        A window slot is held on entry. Exactly one of the branches below
        either keeps it (the msg is genuinely in flight) or releases it. The
        release is never done up front for all of them: `_nack` is two store
        hops and the first one can succeed alone, which leaves the record
        `SENT` -- in flight -- with its slot already handed back, and the
        demotion that later picks it up releases a second time. One invented
        slot per pass and the window stops bounding anything.
        """
        code, retry_after_s = self._attempt(rec)
        if code is Code.OK:
            try:
                self._store.mark_sent(msg_id)
            except (FileNotFoundError, OSError) as exc:
                # An ACK for the previous attempt can purge the record while
                # this one is on the wire. Nothing is in flight for us to
                # account for, so the slot goes back.
                self._window.release()
                _log.info("cleaner_record_gone", **self._who(), msg_id=msg_id,
                          exc_type=type(exc).__name__)
                return None
            self._retries.pop(msg_id, None)
            return None                       # slot stays taken: it is SENT

        if code is Code.ALREADY_PROCESSED:
            # §8.1: terminal, purged inline. v1's server never sends it (§9.1
            # owns that path); honoured here so it cannot become a msg that
            # retries forever against a server that already has it. The record
            # is leaving `pending/` either way -- and stays there if `purge`
            # fails -- so it is not in flight on any outcome.
            self._window.release()
            self._store.purge(msg_id)
            self._retries.pop(msg_id, None)
            return _PURGED
        if code in _VERDICTS:
            # `_nack` owns the release: only it knows which of its two hops
            # landed, and the msg is in flight between them.
            #
            # §2.3's startup fold of a leftover `NACKED` into `PENDING` is
            # deliberately NOT here: the steady-state rule is the opposite of
            # the startup one. §4.4 leaves `NACKED` alone because an unresolved
            # NACK means RETAIN and the app may still call `resolve()`, so
            # folding it into this sweep would erase the app's window to
            # decide. The fold lives in `clnt/api.py`'s one-shot
            # `reconcile()` (§2.3, "startup is just its first pass") -- the
            # layer that knows a fresh process has no pending `resolve()` to
            # wait for -- over `MsgStore.sweep_nacked_to_pending()`.
            self._nack(msg_id, code)
            self._retries.pop(msg_id, None)
            return None

        # Nothing landed: still PENDING, so the slot goes back and it is
        # rescheduled.
        self._window.release()
        self._defer(msg_id, retry_after_s)
        if code is Code.UNAUTHORIZED:
            # §8.1: "PENDING, halted". The key is stale for every msg, not
            # this one, so spending the rest of the pass re-proving it against
            # the same key is pure noise. The record is untouched and the next
            # pass tries again -- halted is not dropped (§4.4).
            _log.warning("cleaner_halted", **self._who(), msg_id=msg_id,
                         err_code=str(code))
            return _HALTED
        return None

    def _attempt(self, rec: MsgRecord) -> tuple[Code, float | None]:
        """Call `send_fn` with no lock held, and normalise what comes back."""
        try:
            result = self._send_fn(rec)
        except Exception as exc:                       # noqa: BLE001
            # Injected code. Its message is app data (§10.4) -- type and
            # location only. A failed attempt is not a failed msg: it stays
            # PENDING and the next cycle retries it.
            _log.warning("cleaner_send_error", **self._who(),
                         msg_id=rec.msg_id, exc_type=type(exc).__name__,
                         at=_origin(exc))
            return Code.STORED_UNREACHABLE, None
        return self._normalize(result)

    def _normalize(self, result) -> tuple[Code, float | None]:
        """`send_fn`'s return -> `(Code, retry_after_s)`.

        Accepts a bare `Code` (or its string) and `transport.SendResult`, whose
        third field exists precisely so this loop can honour it (§8.1). An
        unrecognised value is `stored_unreachable`: the msg was never judged,
        so it stays PENDING and is retried -- never dropped on a shape nobody
        understands.

        Both fields are coerced here, because both come from injected code:
        `_retry_after_s` in `transport.py` cannot be the only guard on the
        second one when `send_fn` is whatever the app handed us. An
        un-coercible `retry_after_s` used to raise out of `_deferral_s`, up
        through `_dispatch` and `_retry_pending`, and abort the whole pass
        before demotion and the `max_pending` alarm ever ran -- every pass, on
        the same msg, with `_run` swallowing it and retrying forever. A bad
        value falls back to the curve instead; only its type is logged, since
        the value came from injected code and may be app data (§10.4).
        """
        code = getattr(result, "code", result)
        raw = getattr(result, "retry_after_s", None)
        retry_after_s: float | None = None
        if raw is not None:
            try:
                retry_after_s = float(raw)
            except (ValueError, TypeError):
                _log.warning("cleaner_retry_after_invalid", **self._who(),
                             retry_after_type=type(raw).__name__)
        try:
            return Code(code), retry_after_s
        except (ValueError, TypeError):
            _log.warning("cleaner_send_result_invalid", **self._who(),
                         result_type=type(result).__name__)
            return Code.STORED_UNREACHABLE, None

    def _nack(self, msg_id: int, code: Code) -> None:
        """PENDING -> NACKED, via SENT. The record is still in `pending/` here.

        Still two hops even though `MsgStore.mark_nacked` now also accepts a
        `pending/` record: that widening exists for the SSE outcome path, which
        holds no window slot of its own. Here the split is what makes the slot
        accountable -- `SENT` is the only state that keeps one, so each failure
        point can say whether the msg ended up in flight. Both hops are one
        atomic rename each, so a crash between them leaves the msg `SENT` and
        `sent_timeout_s` picks it up -- never a lost record.

        Owns the window slot it was handed, because only this method can tell
        the two hops apart (§5.1).
        """
        try:
            self._store.mark_sent(msg_id)
        except (FileNotFoundError, OSError) as exc:
            # Never became SENT -- an ACK purged it under us, or the write
            # failed. Nothing is in flight, so the slot goes back.
            self._window.release()
            _log.info("cleaner_record_gone", **self._who(), msg_id=msg_id,
                      exc_type=type(exc).__name__)
            return
        try:
            self._store.mark_nacked(msg_id, str(code))
        except (FileNotFoundError, OSError) as exc:
            # The first hop landed: the record really is in `sent/` now, so
            # the slot stays taken and `_demote_stale` releases it when
            # `sent_timeout_s` elapses. Releasing here as well would be a
            # release with no acquire -- and this path is a failing disk,
            # exactly the outage this loop exists for, so it would repeat.
            _log.warning("cleaner_nack_incomplete", **self._who(),
                         msg_id=msg_id, exc_type=type(exc).__name__)
            return
        self._window.release()
        # `msgs_nacked_total` is the notification path's counter (§10.5); a
        # server verdict on a resend is the same event and must not be counted
        # twice. `reason` is the code, never the envelope.
        _log.warning("msg_resolved", **self._who(), msg_id=msg_id,
                     code=str(code), action="nacked")

    def _defer(self, msg_id: int, retry_after_s: float | None) -> None:
        """Schedule the next attempt. **Never** drops or parks the msg.

        The cycle: `retry_num` attempts on a jittered exponential curve, then
        the counter resets and the next pass opens a new cycle (§4.4, D27).
        Full jitter (`uniform(0, span)`) rather than a fixed step, so a store
        that went dark all at once does not come back all at once.
        """
        state = self._retries.setdefault(msg_id, _Retry())
        state.fails += 1
        cycle_done = state.fails >= self._retry_num
        if cycle_done:
            # A new cycle, not a dead msg. The client never gives up (§4.4).
            state.fails = 0
        state.not_before = self._monotonic() + self._deferral_s(
            retry_after_s, cycle_done, state.fails)

    def _deferral_s(self, retry_after_s: float | None, cycle_done: bool,
                    fails: int) -> float:
        """Seconds to wait before the next attempt at this msg.

        `Retry-After` wins when the server sent one (§8.1: `server_busy`
        "loop retries after Retry-After"), clamped into a range an operator
        would still call a retry -- negative or absurd values are the peer
        steering this loop. It arrives already coerced to a float or None by
        `_normalize`, which is where anything a `send_fn` invented is turned
        into something this arithmetic can survive.

        At a cycle boundary the wait is zero: `sweep_interval_s` is already
        the pacing floor, and a msg that has just exhausted a cycle has waited
        the whole curve. Anything else would make `retry_num=1` mean "one
        attempt then a long park", which is give-up with extra steps.
        """
        if retry_after_s is not None and math.isfinite(retry_after_s):
            return max(0.0, min(retry_after_s, self._retry_after_cap_s))
        if cycle_done:
            return 0.0
        # The cap lives on the span, and the jitter is bounded *by that span*
        # rather than re-clamped against the cap. Clamping the result against
        # the cap instead would silently do the capping twice and make an
        # uncapped curve indistinguishable from a capped one -- a mutation
        # that removed `min(self._backoff_cap_s, ...)` here survived the suite
        # until the second clamp was tied to `span`.
        span = min(self._backoff_cap_s, self._backoff_base_s * (2 ** (fails - 1)))
        return max(0.0, min(float(self._jitter(span)), span))

    def _demote_stale(self) -> int:
        """`SENT -> PENDING` for anything older than `sent_timeout_s` (§2.1).

        This is the coverage half `server_epoch` does not reach (§4.4): an app
        that crashed mid-callback, a dropped notification, an app that never
        calls `send_notice`. Without it those records sit in `sent/` forever
        looking handled.

        No stop check, unlike `_retry_pending`. That one can block for
        `req_timeout_s` per msg against a dead server; this is local renames
        only, so it finishes in bounded time, and it is the phase that hands
        window slots back -- cutting it short would only defer work the next
        run has to redo, for no measurable gain on the way out.
        """
        now = self._now()
        demoted = 0
        for msg_id in self._store.list("sent"):
            rec = self._store.get(msg_id)
            if rec is None:
                continue
            age_s = _age_s(rec.sent_at, now)
            if age_s is None:
                # A `sent/` record with no usable `sent_at` can never age out,
                # so leaving it is a permanent park -- demote and let the
                # resend rebuild the timestamp. The server dedupes (§4.3).
                _log.warning("cleaner_sent_at_unusable", **self._who(),
                             msg_id=msg_id)
            elif age_s < self._sent_timeout_s:
                continue
            try:
                self._store.demote_to_pending(msg_id)
            except (FileNotFoundError, OSError):
                continue                      # an ACK beat us to it
            # §5.1: the window decrements on demotion as well as on a terminal
            # notification, "else the window leaks slots permanently".
            self._window.release()
            demoted += 1
        if demoted:
            metrics.add("cleaner_demotions_total", demoted)
        return demoted

    # --------------------------------------------------------- internals

    def _run(self, stop: threading.Event) -> None:
        """The thread body. Nothing joins this for a result, so a pass that
        raises has to be swallowed here or the loop dies silently and the
        at-least-once promise quietly stops holding."""
        while not stop.is_set():
            try:
                # Not `sweep_once()`: this pass carries the stop flag so
                # `stop()` can cut it short, which a direct call must not do.
                with self._pass_lock:
                    self._pass(stop)
            except Exception as exc:                   # noqa: BLE001
                _log.error("cleaner_sweep_error", **self._who(),
                           exc_type=type(exc).__name__, at=_origin(exc))
            # `wait`, never `sleep`: `stop()` has to be able to cut this short.
            stop.wait(self._sweep_interval_s)

    def _who(self) -> dict:
        return {"producer_id": self._producer_id,
                "server_alias": self._server_alias}


#: `_dispatch` outcomes the *caller* has to act on. Sentinels rather than an
#: exception: ending the send phase must not skip the demotion phase, which is
#: what an exception unwinding to `sweep_once` would do.
_PURGED = object()      # the record is gone (§8.1 `already_processed`)
_HALTED = object()      # end the send phase, keep the pass (§8.1 unauthorized)


def _age_s(sent_at: str | None, now: datetime) -> float | None:
    """Seconds since `sent_at`, or None when it is missing/unusable.

    `sent_at` is second-precision (`2026-07-25T10:15:30Z`, §3), so the parsed
    instant is at or before the real one and the age is never negative from
    truncation alone. A clock stepped backwards can still produce one, and it
    clamps to 0 rather than going negative -- a negative age would read as
    "fresh" and park the record until the clock caught up.

    The subtraction is inside the guard, not just the parse. A timestamp
    without an offset is read as UTC (§3 says the field is UTC), but if either
    side is still naive when they meet, `-` raises `TypeError` -- and a raise
    here aborts the pass, taking the rest of the demotions, the `pending_count`
    gauge and the `max_pending` alarm with it. An unusable timestamp is a
    demotion (the caller's `None` branch), never a dead pass.
    """
    if not sent_at:
        return None
    try:
        when = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (now - when).total_seconds())
    except (ValueError, TypeError, AttributeError, OverflowError, OSError):
        return None
