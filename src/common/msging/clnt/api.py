"""The client facade: design §6.1's public surface, and nothing else.

Everything below this module is a part; this is the assembly. One
`producer_id` names one binding (§3), and behind it sit a `MsgStore`, a
`MsgIdAllocator`, a `Window`, a `Cleaner` and -- once the app has registered a
callback -- an `SseReader`. The app never sees any of them: §3 is explicit
that a startup scan of `<base>/*/*/identity.json` rebuilds `producer_id ->
binding`, and that is precisely why `send()`, `close()`, `flush()`,
`set_info()` and `on_notify()` take only a `producer_id`.

Module-level functions, not a class, because §6.1 is written that way and the
README's usage is `msg.send(pid, ...)`. The state is therefore process-wide and
guarded by one lock; per-producer work happens on the producer's own objects,
which have their own locks.

**What raises and what returns is §8.3, and it is not negotiable.** Four
programming errors raise -- `NotRegisteredError`, `NoCallbackError`,
`InvalidEnvelopeError`, `ProducerClosingError`. Every runtime condition,
including a dead network, comes back as a §8.1 `err_code` on a `SendResult`.

**Two limits, two behaviours (§5.1), and conflating them is the classic bug:**

    max_pending    bounds the durable store.  send() waits slot_wait_s and
                   then REJECTS with `slot_full`. The msg is NOT stored.
    max_in_flight  bounds what is on the wire. A full window NEVER fails a
                   send: the msg is stored, `stored_throttled` comes back,
                   and the cleaner ships it when a slot frees.

**`send()` makes exactly one POST** (§2.2). Retry, backoff, demotion and the
epoch sweep are the cleaner loop's (§4.4), which is why `send()`'s latency is
bounded at one `req_timeout_s` and why nothing here loops on the network.

**`too_large` is checked client-side, before storing** (§8.1), against the
`max_msg_bytes` `register()` returned. Otherwise an oversized msg lands in
`pending/` and is retried forever against a server that will never take it.
The server's 413 is a backstop for a stale limit, and it is handled the same
way -- the record is purged, so a `too_large` return always means "not
stored", exactly as §8.1's table promises. **"Not stored" is the END STATE,
not "never written":** the 413 path stores, POSTs, then purges, and a crash
inside that window leaves a `PENDING` record the cleaner turns into `NACKED`
for the app to resolve -- never a msg retried forever. (The cleaner cannot do
that: it has
no caller to hand the msg back to, so there it becomes `NACKED` and the app
decides. Different layer, different information, deliberately different rule.)

**Restart reconciliation (§2.3) happens here, once per producer per process.**
A leftover `NACKED` can only mean the app's PURGE/RETAIN decision never
finished committing, so it folds into `PENDING`; a leftover `SENT` is resent
immediately rather than waiting out `sent_timeout_s` (§2.4). Neither belongs in
the cleaner's steady-state sweep -- §4.4 leaves `NACKED` alone precisely
because a live app may still be deciding. This layer is the one that knows the
process is fresh and no `resolve()` is pending.

**The notify callback runs on the SSE reader thread** (§6.4). It must be
thread-safe and quick; an exception in it is caught so one bad notice cannot
cost every later outcome, and its *message* is never logged -- app data (§10.4).

Never logs an api_key (only `redact_key`'s prefix) and never logs a payload or
an `info` bag; `size_bytes` is what debugging needs (§10.4).

Nothing here imports `srv/` (§6.4, enforced by
`tests/common/msging/test_boundaries.py`).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx
from urllib.parse import urlparse

from common.msging import metrics as _metrics
from common.msging.clnt.binding import Binding, BindingRegistry
from common.msging.clnt.cleaner import Cleaner
from common.msging.clnt.msg_id import MsgIdAllocator
from common.msging.clnt.sse import SseReader, _origin, notify_url
from common.msging.clnt.store import MsgStore
# The same wrapper `post_once` and `sse.py` use. `/register` and `/close` open
# their own short-lived `httpx.Client` around whatever transport the caller
# injected, and closing a Client closes its transport -- so without this a
# caller's long-lived pool is dead after the first `register()`.
from common.msging.clnt.transport import _CallerOwnedTransport, post_once
# `transport.SendResult` is a NamedTuple `(code, reason, retry_after_s)` -- what
# ONE POST came back with. `models.SendResult` is the pydantic
# `{err_code, reason, msg_id}` that §6.1 says `send()` returns. They are not
# interchangeable and must never be confused, so the wire one is imported under
# a name that says what it is and is never returned from this module.
from common.msging.clnt.transport import SendResult as PostOutcome
from common.msging.clnt.window import Window
from common.msging.codes import Action, Code
from common.msging.config import MsgingConfig, load_config
from common.msging.errors import (
    ConfigError,
    InvalidEnvelopeError,
    NoCallbackError,
    NotRegisteredError,
    ProducerClosingError,
    RegistrationError,
)
from common.msging.log import get_logger, redact_key
from common.msging.models import CloseResult, Envelope, MsgRecord, NotifyEv, SendResult

_log = get_logger("msging.clnt.api")

#: §6.1's `Action` values, re-exported so an app writes `msg.PURGE` rather than
#: importing an enum out of a private module (the README's example does).
PURGE = Action.PURGE
RETAIN = Action.RETAIN

#: How often the `close()` drain and the `slot_wait_s` wait look again. Short
#: enough that neither overshoots its deadline by anything a caller would
#: notice, long enough not to spin a core.
_TICK_S = 0.01

#: Notices held for `poll_notify()` when nobody drains them. Bounded because an
#: app that registers a callback and never polls would otherwise accumulate one
#: object per outcome forever; the oldest go first, since the newest outcomes
#: are the ones still actionable.
_NOTIFY_QUEUE_MAX = 1000

#: Used when a stored identity predates `max_msg_bytes` being persisted with it
#: (see `binding.Binding`). Matches §5.2's server default; the server's 413
#: backstops it either way.
_FALLBACK_MAX_MSG_BYTES = 33554432

#: Deadline for the two small out-of-band POSTs this module makes itself,
#: `/register` and `/close`. Not `req_timeout_s`: that one is §2.2's per-msg
#: budget, and these are not msgs.
_REGISTER_TIMEOUT_S = 30.0

#: How long `re_register()` waits for the retired producer's threads. Short:
#: the identity it is replacing has already been refused by the server, so
#: there is nothing useful left for those threads to do.
_RETIRE_JOIN_S = 2.0


def _utc_now_iso() -> str:
    """`2026-07-25T10:15:30Z` -- the shape §3 and §7 use."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Producer:
    """One live binding: identity, durable store, window, cleaner, reader.

    Not part of the public surface -- the app holds a `producer_id` string and
    everything here is reached through it.
    """

    def __init__(self, binding: Binding, settings, max_msg_bytes: int,
                 producer_desc: str | None, transport) -> None:
        self.producer_id = binding.producer_id
        self.api_key = binding.api_key
        self.alias = binding.alias
        self.base_path = binding.base_path
        self.service_name = binding.service_name or binding.alias
        self.registered_at = binding.registered_at
        self.store_dir = binding.store_dir
        self.max_msg_bytes = max_msg_bytes
        self.producer_desc = producer_desc
        self.settings = settings
        self.transport = transport
        # Derived from the server's own keepalive, never configured as an
        # absolute -- see _idle_timeout_for. Both the fresh-register and the
        # reattach path land here, so a producer that never sees a /register
        # response still gets a timer sized to its server.
        self.idle_timeout_s = _idle_timeout_for(binding, settings, binding.alias)

        self.store = MsgStore(binding.store_dir)
        self.allocator = MsgIdAllocator(binding.store_dir / "msg_id.json",
                                        block=settings.msg_id_block)
        self.window = Window(settings.max_in_flight)
        self.cleaner = Cleaner(
            self.store, self.window, self._send_one,
            sweep_interval_s=settings.sweep_interval_s,
            sent_timeout_s=settings.sent_timeout_s,
            retry_num=settings.retry_num,
            max_pending=settings.max_pending,
            producer_id=self.producer_id, server_alias=self.alias)
        self.sse: SseReader | None = None

        self._lock = threading.Lock()
        self._info: dict = {}
        self._callback: Callable | None = None
        self._notify_on_ack = False
        self._notices: deque[NotifyEv] = deque(maxlen=_NOTIFY_QUEUE_MAX)
        self.closing = False
        self.unauthorized = False
        self.reconciled = False

        # §2.4 step 1 vs §2.2's one POST. `closing` alone is a check-then-act:
        # a send that passed the flag and is still inside `store.put_pending()`
        # or its POST is invisible to the drain, so `close()` counts an empty
        # store, reports `drained`, and `shutdown()` then stops the cleaner --
        # leaving the record that landed a microsecond later sitting in
        # `pending/` until the next process start. This condition makes the
        # gate and the drain mutually exclusive: a send is either counted in
        # `_active_sends` (and the drain waits for it) or refused.
        self._gate = threading.Condition(threading.Lock())
        self._active_sends = 0

    # -- the send/close gate (§2.4 step 1) --------------------------------

    def begin_send(self) -> None:
        """Claim the right to run one `send()` to completion.

        Raises:
            ProducerClosingError: `close()` has already taken the gate (§2.4
                step 1, §8.3).
        """
        with self._gate:
            if self.closing:
                raise ProducerClosingError(
                    f"producer {self.producer_id!r} is draining: close() stops "
                    "accepting sends (design §2.4)")
            self._active_sends += 1

    def end_send(self) -> None:
        """Release the gate. Always paired with `begin_send()` in a `finally`
        -- a send that raised still has to stop blocking a drain."""
        with self._gate:
            self._active_sends -= 1
            if self._active_sends <= 0:
                self._gate.notify_all()

    def begin_close(self, deadline: float) -> bool:
        """Shut the gate and wait out every send already inside it.

        Returns True when the producer was *already* closing -- so `close()`
        can be idempotent without a check-then-set of its own, which is what
        let two concurrent `close()` calls both drain and both POST `/close`.

        Bounded by the caller's deadline: a send parked in `slot_wait_s` or in
        a `req_timeout_s` POST must not turn `close(0.5)` into a hang. A send
        still running at the deadline is simply counted by the drain like any
        other outstanding msg.
        """
        with self._gate:
            already = self.closing
            self.closing = True
            while self._active_sends > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _log.warning("close_gate_timeout", **self._who(),
                                 active_sends=self._active_sends)
                    break
                self._gate.wait(remaining)
            return already

    # -- identity ---------------------------------------------------------

    def describe(self) -> dict:
        """§6.1's Producer object. A dict, because the README's example
        subscripts it (`p["producer_id"]`)."""
        return {
            "producer_id": self.producer_id,
            "api_key": self.api_key,
            "max_msg_bytes": self.max_msg_bytes,
            "server_alias": self.alias,
            "base_path": self.base_path,
            "service_name": self.service_name,
            "registered_at": self.registered_at,
        }

    # -- info (§7) --------------------------------------------------------

    def set_info(self, info: dict) -> None:
        with self._lock:
            # Copied, not aliased: §6.1 says set_info "replaces wholesale", and
            # keeping the caller's dict would let a later mutation of it change
            # msgs already described as sent with the old bag.
            self._info = dict(info or {})

    def merged_info(self, per_msg: dict | None) -> dict:
        """Producer-level bag with the per-msg one **shallow**-merged over it,
        per-msg keys winning (§7). Shallow by design -- deep merge is where
        this gets surprising."""
        with self._lock:
            merged = dict(self._info)
        if per_msg:
            merged.update(per_msg)
        return merged

    # -- notifications ----------------------------------------------------

    def set_callback(self, fn: Callable, notify_on_ack: bool) -> None:
        with self._lock:
            self._callback = fn
            self._notify_on_ack = notify_on_ack

    def drain_notices(self) -> list[NotifyEv]:
        with self._lock:
            out = list(self._notices)
            self._notices.clear()
            return out

    def handle_notice(self, ev: NotifyEv) -> None:
        """One outcome, from the SSE reader thread (§2.2 ACK/NACK paths).

        **A window slot is handed back only when the outcome actually matched
        a `SENT` record.** Duplicate outcomes are routine, not exotic: §2.2
        step 5 says a resend of a msg the server already has "hits its dedup
        constraint and comes back as an ACK", so a demoted-then-resent msg is
        ACKed twice, and a late NACK can arrive for a record another thread
        already purged. Releasing on every notice makes `max_in_flight` stop
        bounding anything after the first duplicate -- measured at 4 msgs on
        the wire under `max_in_flight=2` after three ACKs for an unknown
        msg_id. A slot belongs to a record in `sent/` and to nothing else.
        """
        code = str(ev.code)
        if code == Code.ACK:
            # Purge BEFORE the callback (§2.2): the record is already gone and
            # the app has no decision to make, so a callback that inspects the
            # store must not still see it.
            try:
                was_in = self.store.purge(ev.msg_id)
            except OSError as exc:
                # §8.3: a failing disk is a runtime condition. The record stays
                # where it is, the cleaner keeps owning it, and the app still
                # hears the outcome.
                was_in = None
                _log.warning("notice_purge_failed", **self._who(),
                             msg_id=ev.msg_id, exc_type=type(exc).__name__)
            if was_in == "sent":
                self.window.release()
            elif was_in is None:
                _log.info("notice_for_unknown_msg", **self._who(),
                          msg_id=ev.msg_id, code=code)
            _metrics.counter("msgs_acked_total")
            deliver = self._notify_on_ack
        elif code == Code.NACK:
            # Order-independent, exactly like the ACK branch above, and for the
            # same reason: the server answers a msg twice (the 202 and the SSE
            # outcome) and nothing sequences them, so over loopback the NACK
            # routinely lands while `send()` is still between `put_pending()`
            # and `mark_sent()` and the record is still `PENDING`.
            # `mark_nacked` therefore accepts it from `pending/` as well as
            # `sent/` and says which -- see `MsgStore.mark_nacked`. The
            # `_apply` still in flight is not able to undo it: its `mark_sent`
            # finds nothing in `pending/` and takes the `send_record_gone`
            # branch it already had for the ACK case.
            try:
                was_in = self.store.mark_nacked(ev.msg_id, ev.reason)
            except (FileNotFoundError, OSError) as exc:
                # A duplicate or late outcome for a record already purged or
                # resolved. Normal traffic on an at-least-once channel, and the
                # app still hears about it.
                was_in = None
                _log.info("notice_for_unknown_msg", **self._who(),
                          msg_id=ev.msg_id, code=code,
                          exc_type=type(exc).__name__)
            if was_in == "sent":
                self.window.release()
            # `pending` deliberately does NOT release. The slot for a record
            # still in `pending/` is held by whoever is mid-POST for it, and
            # that caller hands it back on its own failed `mark_sent`;
            # releasing here too would be the second release for one acquire
            # -- the accounting bug that made `max_in_flight` stop bounding
            # anything (§5.1). A record in `pending/` with nobody mid-POST
            # (throttled, or demoted) holds no slot at all, so there is
            # likewise nothing to give back.
            _metrics.counter("msgs_nacked_total")
            deliver = True
        else:
            # §8.2: `code` is extensible and "a new code must state whether the
            # client purges or retains". This version has not been told, so it
            # retains -- the record is left exactly where it is, and the slot
            # stays taken because nothing terminal has happened to it.
            _log.info("notice_unknown_code", **self._who(), msg_id=ev.msg_id,
                      code=code)
            deliver = True
        if not deliver:
            return
        with self._lock:
            self._notices.append(ev)
            callback = self._callback
        if callback is None:
            return
        try:
            # §2.2/§6.1: `notify_callback_fn(msg_id, notify_ev)`.
            callback(ev.msg_id, ev)
        except Exception as exc:                       # noqa: BLE001
            # App code on the reader thread. One bad notice must not cost every
            # later outcome, and the exception *message* is app data (§10.4) --
            # type and raising location only.
            _log.warning("notify_callback_error", **self._who(),
                         msg_id=ev.msg_id, exc_type=type(exc).__name__,
                         at=_origin(exc))

    def on_unauthorized(self, status: int) -> bool:
        """The SSE reader's `401`/`403` hook (§8.1: re-register).

        Returns False -- stop reconnecting. This library will not re-register
        on the app's behalf: `/register` issues a *new* `producer_id`, which
        would orphan this binding's durable backlog (§3), so the decision is
        the app's. What this layer owes is a loud, unambiguous signal, a way
        to *observe* the state (`producer_status()`) and a way to act on it
        (`re_register()`) -- not an endless reconnect against a key that will
        never work again, and not a log line as the only trace.
        """
        self.unauthorized = True
        _log.error("producer_unauthorized", **self._who(), status=status,
                   key_prefix=redact_key(self.api_key),
                   action="the api_key is no longer accepted; call "
                          "re_register() to mint a new identity (§8.1)")
        return False

    # -- the wire ---------------------------------------------------------

    def _send_one(self, rec: MsgRecord) -> PostOutcome:
        """One POST attempt. Shared by `send()` and the cleaner loop, so both
        speak to the server identically -- and neither retries here."""
        return post_once(self.base_path, self.service_name, self.api_key,
                         rec.envelope, self.settings.req_timeout_s,
                         self.settings.gzip_min_bytes,
                         _transport=self.transport, attempts=rec.attempts)

    def _who(self) -> dict:
        return {"producer_id": self.producer_id, "server_alias": self.alias}

    # -- lifecycle --------------------------------------------------------

    def reconcile(self) -> None:
        """§2.3's startup pass. Once per producer per process."""
        if self.reconciled:
            return
        self.reconciled = True
        folded = self.store.sweep_nacked_to_pending()
        resend = self.store.sweep_sent_to_pending()
        if folded or resend:
            # Loud: it means the previous run did not finish, and msgs are
            # about to go out again.
            _log.warning("startup_reconcile", **self._who(),
                         nacked_folded=folded, sent_resend=resend,
                         pending_count=self.store.count("pending"))

    def shutdown(self, join_s: float) -> None:
        """Stop both threads and write the msg_id counter back. Never raises:
        this runs on the way out, and a failure here must not mask whatever the
        caller was actually doing."""
        try:
            self.cleaner.stop(timeout_s=join_s)
        except Exception as exc:                       # noqa: BLE001
            _log.warning("cleaner_stop_failed", **self._who(),
                         exc_type=type(exc).__name__)
        if self.sse is not None:
            try:
                self.sse.stop(timeout_s=join_s)
            except Exception as exc:                   # noqa: BLE001
                _log.warning("sse_stop_failed", **self._who(),
                             exc_type=type(exc).__name__)
        try:
            # §3.1: a clean close writes back the true next id so the rest of
            # the reserved block is not burned.
            self.allocator.close()
        except OSError as exc:
            _log.warning("msg_id_close_failed", **self._who(),
                         exc_type=type(exc).__name__)


# -- process state --------------------------------------------------------

#: Guards the three module globals below, and **nothing that blocks**. §2.2
#: bounds `send()` at one `req_timeout_s`, and `send()` reaches this lock on
#: every call (`_require`), so anything held across a network call here stalls
#: every producer in the process: an unrelated binding's `send()` was measured
#: blocked 0.86 s behind another thread's `/register`, bounded only by
#: `_REGISTER_TIMEOUT_S` = 30 s. Take it, read or write a dict, drop it.
_STATE_LOCK = threading.RLock()
_config: MsgingConfig | None = None
_registry: BindingRegistry | None = None
_producers: dict[str, _Producer] = {}
#: One lock per server alias, so two threads calling `register()` on the *same*
#: binding serialise (a second `/register` would mint a second producer_id and
#: orphan the first's backlog, §3) while `register()` on a different alias --
#: and every `send()` in the process -- runs unimpeded. This is the lock that
#: is allowed to be held across `/register`; `_STATE_LOCK` is not.
_alias_locks: dict[str, threading.Lock] = {}


def _need_init() -> tuple[MsgingConfig, BindingRegistry]:
    with _STATE_LOCK:
        if _config is None or _registry is None:
            raise ConfigError(
                "clnt_init() must be called once per process before any other "
                "client call (design §6.1)")
        return _config, _registry


def _require(producer_id: str) -> _Producer:
    with _STATE_LOCK:
        producer = _producers.get(producer_id)
    if producer is None:
        raise NotRegisteredError(
            f"no registered producer {producer_id!r}: register() returns the "
            "producer_id every other client call takes (design §6.1)")
    return producer


def _alias_lock(alias: str) -> threading.Lock:
    with _STATE_LOCK:
        lock = _alias_locks.get(alias)
        if lock is None:
            lock = _alias_locks[alias] = threading.Lock()
        return lock


# -- §6.1 item 1 ----------------------------------------------------------


def clnt_init(config: MsgingConfig | str | Path) -> None:
    """Load client config. Call once per process (§6.1 item 1).

    Args:
        config: a `MsgingConfig`, or a path to a YAML file in §5.1's shape.
            A path is loaded through `config.load_config`, so `${VAR}` /
            `${VAR:-default}` interpolation applies and a typo'd key raises
            `ConfigError` rather than being silently ignored.

    Raises:
        ConfigError: the file is unreadable, malformed, or has unknown keys.

    Every timeout the client uses comes from here -- there are no hardcoded
    waits (§5.1). A second call replaces the config for *subsequent*
    `register()` calls; producers already registered keep the settings they
    were built with, because their store, window and cleaner are already sized
    against them.
    """
    global _config, _registry
    cfg = config if isinstance(config, MsgingConfig) else load_config(config)
    base_dir = Path(cfg.client.defaults.base_dir).expanduser()
    with _STATE_LOCK:
        if _config is not None:
            _log.info("clnt_reinit", base_dir=str(base_dir),
                      live_producers=len(_producers))
        _config = cfg
        _registry = BindingRegistry(cfg.client, base_dir)


# -- §6.1 item 3 ----------------------------------------------------------


def register(producer_description: dict, server_alias: str | None = None, *,
             _transport: httpx.BaseTransport | None = None) -> dict:
    """Bind this process to one server and return the Producer (§6.1 item 3).

    Args:
        producer_description: free-form descriptor, stored by the server and
            carried for troubleshooting (§7's `producer_desc`). A
            `producer_desc` string in it rides on every envelope.
        server_alias: `None` -> `client.default_server`. An alias names a
            *binding* (endpoint + service), never an environment: switching
            environments is a config or env-var change, never a code change
            (§5.1).
        _transport: injection seam for tests and for a caller that wants one
            long-lived connection pool. Used for `/register`, `/event`,
            `/notify` and `/close` alike.

    Returns:
        `{producer_id, api_key, max_msg_bytes, server_alias, base_path,
        service_name, registered_at}` (§6.1: "the response carries the
        server's limits so client and server configs cannot drift").

    Raises:
        ConfigError: `clnt_init()` was never called, or the alias is unset,
            unknown, or disabled.
        RegistrationError: the server refused. Terminal, no retry loop --
            retrying a rejected enrollment never helps (§10.1).
        OSError: the store directory cannot be created or written to. Not one
            of §8.3's codes -- see the TODO below.

    **Damaged on-disk state is not an error here.** A `msg_id.json` that
    cannot be parsed used to come out of this call as a bare
    `JSONDecodeError` from `MsgIdAllocator.__init__` -- neither of §8.3's two
    codes, nothing an app could branch on, and a producer that could not start
    while its `pending/` still held durable msgs. §3.1's answer for a counter
    it cannot trust is a random base raised past the highest msg_id on disk,
    so that is what happens: loud log, fallback, producer up, backlog shipped.
    A corrupt *record* has always been treated the same way (§3's
    corrupt-record policy).

    TODO: §8.3 enumerates no error for "the store directory is unusable", so
    an unwritable or full `base_dir` still surfaces from `register()` as a raw
    `OSError` -- from `MsgStore.__init__`'s `mkdir` and from the allocator's
    first block reservation alike. Wrapping it in `ConfigError` would be a
    lie (the config is fine, the disk is not) and a new `StoreError` is a
    §8.3 change, not a code change. Left raw and documented until §8.3 says
    what it should be.

    **One `register()` per binding, and it is idempotent** (§3). A stored
    `identity.json` for this alias is reused rather than re-registered: a fresh
    `producer_id` on every start would orphan the previous run's `pending/` and
    `sent/`, and the at-least-once promise with them. The exception is §3's
    stale binding -- a stored `base_path` that no longer matches the configured
    one -- where the stored credentials are worthless against the new endpoint:
    that logs loudly, leaves the old directory for inspection (it may hold
    undelivered msgs) and registers fresh.

    Reattaching also runs §2.3's restart reconciliation and starts the cleaner
    loop. It does **not** open the SSE channel -- that needs `on_notify()`
    first (§4.1).

    See `re_register()` for §8.1's `unauthorized` recovery: this function
    cannot serve it, because it reuses the stored identity by design.
    """
    return _bind(server_alias, producer_description, _transport,
                 force_new=False)


def _bind(server_alias: str | None, producer_description: dict, transport,
          *, force_new: bool) -> dict:
    """`register()` and `re_register()`'s shared body.

    The global lock is deliberately not held across `_post_register`: it is on
    `send()`'s path and §2.2 bounds `send()` at one `req_timeout_s`, not at
    `_REGISTER_TIMEOUT_S`. The per-alias lock takes its place, which is the
    only mutual exclusion registration actually needs -- two threads must not
    both mint an identity for one binding.
    """
    cfg, registry = _need_init()
    binding_cfg = registry.resolve(server_alias)     # ConfigError: unset/unknown/disabled
    alias = binding_cfg.alias
    settings = cfg.client.settings_for(alias)
    desc = dict(producer_description or {})
    producer_desc = desc.get("producer_desc")
    if not isinstance(producer_desc, str):
        producer_desc = None

    with _alias_lock(alias):
        if not force_new:
            with _STATE_LOCK:
                live = _live_for(alias)
            if live is not None:
                if live.base_path != binding_cfg.base_path:
                    # §3: never silent. The alias was repointed while this
                    # process held a producer bound to the old endpoint, and
                    # handing it back without a word is exactly the silent
                    # stale binding the rule forbids. Returning it anyway is
                    # still right -- its backlog belongs to the old endpoint
                    # and only `close()` or a restart can retire it.
                    registry.check_stale(alias, live.base_path)
                    _log.error("producer_bound_to_stale_endpoint",
                               **live._who(), bound_base_path=live.base_path,
                               configured_base_path=binding_cfg.base_path,
                               action="close() this producer and register() "
                                      "again to bind to the new endpoint")
                return live.describe()

            stored = _stored_identity(registry, alias)
        else:
            stored = None

        if stored is not None:
            binding = stored
            max_msg_bytes = stored.max_msg_bytes or _FALLBACK_MAX_MSG_BYTES
            if stored.max_msg_bytes is None:
                _log.warning("binding_limit_unknown", producer_id=stored.producer_id,
                             server_alias=alias, max_msg_bytes=max_msg_bytes)
            _log.info("producer_reattached", producer_id=stored.producer_id,
                      server_alias=alias, base_path=stored.base_path,
                      max_msg_bytes=max_msg_bytes,
                      key_prefix=redact_key(stored.api_key))
        else:
            # Outside `_STATE_LOCK`, inside the alias lock -- see the docstring.
            issued = _post_register(binding_cfg, desc, transport)
            registry.save_identity(alias, issued)
            binding = _binding_from(registry, alias, binding_cfg, issued)
            max_msg_bytes = int(issued["max_msg_bytes"])
            _log.info("producer_registered", producer_id=binding.producer_id,
                      server_alias=alias, base_path=binding.base_path,
                      service_name=binding.service_name,
                      max_msg_bytes=max_msg_bytes,
                      # A prefix, never the key (§10.4).
                      key_prefix=redact_key(binding.api_key))

        _warn_on_cleartext_transport(binding.base_path, alias)

        producer = _Producer(binding, settings, max_msg_bytes, producer_desc,
                             transport)
        with _STATE_LOCK:
            _producers[producer.producer_id] = producer

    # Outside every lock: reconciliation touches the filesystem and the cleaner
    # spawns a thread, and neither needs to hold up another producer.
    producer.reconcile()
    # §4.4 is the at-least-once promise, not an optimisation: without this the
    # only thing that ever retries a `PENDING` record is an explicit `flush()`
    # or `close()`, and a msg whose outcome died in transit sits in `sent/`
    # forever looking handled.
    producer.cleaner.start()
    return producer.describe()


def _live_for(alias: str) -> _Producer | None:
    """An already-registered producer for `alias` in this process. §6.1 says
    one register() per binding; a second call is the same binding, not a
    second one, and must not mint a new identity."""
    for producer in _producers.values():
        if producer.alias == alias and not producer.closing:
            return producer
    return None


def _stored_identity(registry: BindingRegistry, alias: str) -> Binding | None:
    """The stored `identity.json` for `alias` that matches the **configured**
    endpoint, or None.

    §3's stale-binding rule leaves old directories in place for inspection, so
    an alias accumulates one identity per endpoint it has ever pointed at.
    Selecting by recency and *then* testing staleness gets this exactly
    backwards: after a repoint the stale directory is the newest one, so it
    wins the tiebreak, fails the staleness test, and disqualifies the identity
    that was just issued against the current endpoint -- so the next start
    registers again, and the one after that, orphaning a backlog every time
    (measured: three producer_ids and two registrations against the new
    endpoint over two restarts). The same bug refuses to reattach when an alias
    is pointed *back* at an endpoint it used before, where a perfectly good
    identity and its undelivered msgs are sitting on disk.

    So: filter on `base_path` first, then take the newest of what is left. If
    nothing matches, log loudly via `check_stale` (§3: never silent) and
    return None so the caller registers fresh.
    """
    candidates = [b for b in registry.load_all().values() if b.alias == alias]
    if not candidates:
        return None
    configured = registry.base_path_for(alias)
    usable = [b for b in candidates if b.base_path == configured]
    if not usable:
        # One loud line about the identity that would otherwise have been
        # reused, rather than one per leftover directory.
        registry.check_stale(alias, max(candidates, key=_identity_age).base_path)
        return None
    return max(usable, key=_identity_age)


def _identity_age(binding: Binding):
    """Sort key for "most recently registered". `registered_at` is
    second-precision (§3), so ties are real -- two `register()` calls in the
    same second on a repoint-and-back are enough -- and a tiebreak on the
    `producer_id` *string* would then pick by alphabet, i.e. at random. The
    identity file's mtime is the finer-grained fact that is actually available;
    the producer_id stays as the last resort so the ordering is total and the
    choice is at least deterministic."""
    try:
        mtime = (binding.store_dir / "identity.json").stat().st_mtime
    except OSError:
        mtime = 0.0
    return (binding.registered_at, mtime, binding.producer_id)


def _binding_from(registry: BindingRegistry, alias: str, binding_cfg,
                  issued: dict) -> Binding:
    return Binding(
        producer_id=issued["producer_id"],
        api_key=issued["api_key"],
        alias=alias,
        base_path=binding_cfg.base_path,
        service_name=binding_cfg.service_name,
        registered_at=issued.get("registered_at", ""),
        store_dir=registry.dir_for(alias, issued["producer_id"]),
        max_msg_bytes=int(issued["max_msg_bytes"]),
        heartbeat_s=_opt_float(issued.get("heartbeat_s")),
    )


def _opt_float(value) -> float | None:
    """`None` for anything unusable. The register response is the server's, so
    a missing or malformed field must not take down a registration that has
    otherwise succeeded -- the drift check simply has nothing to check."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


#: Hosts whose traffic never leaves the machine. A cleartext api_key on
#: loopback is not on a wire anyone can tap, and the entire test suite and
#: every shipped example run here -- warning on them would train the warning
#: away, which is worse than not having it.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def _warn_on_cleartext_transport(base_path: str, alias: str) -> None:
    """§1: "The transport is HTTPS." Nothing enforced it, so `http://` worked
    silently and put the `Bearer` api_key in cleartext on every POST and every
    SSE connect.

    A warning rather than a hard failure, because a hard failure would break
    the examples, the whole test suite, and any legitimate loopback or
    sidecar deployment -- and because this library is not in a position to
    know whether an operator has a TLS-terminating proxy in front of it.
    Loopback is exempted outright (see `_LOCAL_HOSTS`) so the line only ever
    fires on traffic that genuinely leaves the machine.

    For an internal CA, point `SSL_CERT_FILE` at the bundle: httpx reads it
    through the standard library's default SSL context, so no config key here
    would add anything a deployment cannot already set.
    """
    try:
        parsed = urlparse(base_path)
    except ValueError:
        return                       # a malformed base_path fails at connect
                                     # time with a far better message than
                                     # anything this advisory check could add.
    if parsed.scheme == "https":
        return
    if (parsed.hostname or "") in _LOCAL_HOSTS:
        return
    _log.warning(
        "cleartext_transport",
        server_alias=alias,
        scheme=parsed.scheme or "(none)",
        host=parsed.hostname or "(none)",
        action=("§1 specifies HTTPS: this binding sends the producer's api_key "
                "in cleartext on every POST and SSE connect. Use https:// in "
                "base_path, or terminate TLS in front of this endpoint. For an "
                "internal CA, set SSL_CERT_FILE to the bundle"),
    )


def _idle_timeout_for(binding: Binding, settings, alias: str) -> float:
    """§4.2's idle-read timer, derived from the server's own keepalive.

    The timer has to outlast several heartbeats or it tears down healthy
    streams, and that is a *relationship* between two numbers -- the server's
    `heartbeat_s` and the client's tolerance -- not a value either side can get
    right alone. Configured as an absolute on the client, it could not be right
    for two servers at once, and an operator raising `heartbeat_s` on one of
    them would silently put every producer into a permanent reconnect cycle.

    So the client configures the *multiple* (`idle_heartbeats`) and the timer
    is computed from the `heartbeat_s` that `/register` already returns (§6.1).
    Drift is then structurally impossible rather than merely detected: the same
    client YAML adapts to each server it binds to, which is the same
    data-driven principle the alias model rests on (§5.1).

    `idle_timeout_s` remains the fallback for the one case with nothing to
    derive from -- an identity persisted before `heartbeat_s` was recorded, or
    a server that does not report it.

    Note there is deliberately no "these two look mismatched" warning. How many
    heartbeats to tolerate is the operator's call, not this library's, and with
    the multiple configured there is no second number to disagree with it. A
    server's `heartbeat_s` moves only by a config edit plus a restart, and the
    next `/register` picks it up -- so there is no runtime condition left for a
    warning to detect.
    """
    server_hb = binding.heartbeat_s
    if not server_hb or server_hb <= 0:
        _log.debug(
            "idle_timeout_not_derived",
            producer_id=binding.producer_id, server_alias=alias,
            idle_timeout_s=settings.idle_timeout_s,
            reason="server did not report heartbeat_s",
        )
        return settings.idle_timeout_s

    idle = server_hb * settings.idle_heartbeats
    _log.debug(
        "idle_timeout_derived",
        producer_id=binding.producer_id, server_alias=alias,
        server_heartbeat_s=server_hb,
        idle_heartbeats=settings.idle_heartbeats,
        idle_timeout_s=idle,
    )
    return idle


def re_register(producer_id: str, producer_description: dict, *,
                _transport: httpx.BaseTransport | None = None) -> dict:
    """§8.1's `unauthorized` -> "re-register", as an explicit call.

    Args:
        producer_id: the producer whose api_key the server no longer accepts.
        producer_description: as `register()`. Sent to `/register` again --
            this is a new enrollment, not a rename.
        _transport: as `register()`. Defaults to the retired producer's, so an
            app that injected a pool keeps using it.

    Returns:
        A **new** Producer dict, with a new `producer_id` and a new `api_key`.

    Raises:
        NotRegisteredError: no such producer.
        ConfigError / RegistrationError: as `register()`.

    **Why this is not automatic.** `/register` mints a *new* `producer_id`, and
    a producer_id owns a store directory (§3). Re-registering behind the app's
    back would therefore orphan every msg still in the old directory's
    `pending/`, `sent/` and `nacked/` -- silently converting an at-least-once
    channel into a lossy one at exactly the moment the app is least able to
    notice. So the library stops (`on_unauthorized` returns False, the reader
    halts, `producer_status()['unauthorized']` goes true) and waits to be told.

    **What this does to the old binding.** Its threads are stopped and it is
    dropped from the process; its directory is left on disk, exactly as §3's
    stale-binding rule leaves a repointed alias's directory, because it may
    hold undelivered msgs. Nothing is migrated -- those msgs were accepted
    under an identity the server has revoked, and only the app knows whether
    replaying them under a new one is correct. No `/close` is POSTed: the key
    that call would authenticate with is the revoked one.

    TODO: no supported way to hand the old directory's `pending/` records to
    the new producer. Doing it properly needs re-envelop-and-renumber under the
    new producer_id (msg_id is per producer, §3.1) and an app-visible decision
    about duplicates; until then an operator moves the files or drops them.
    """
    old = _require(producer_id)
    transport = old.transport if _transport is None else _transport
    alias = old.alias
    with _STATE_LOCK:
        _producers.pop(old.producer_id, None)
    # Shut the gate first: an in-flight send must not land in a store nobody
    # is sweeping any more. The deadline is now, so it does not wait -- the
    # threads are about to be joined anyway.
    old.begin_close(time.monotonic())
    old.shutdown(join_s=_RETIRE_JOIN_S)
    _log.warning("producer_retired", **old._who(),
                 unresolved=_outstanding(old),
                 store_dir=str(old.store_dir),
                 key_prefix=redact_key(old.api_key),
                 action="re-registering; the old store directory is left on "
                        "disk and its msgs are NOT migrated (§3)")
    return _bind(alias, producer_description, transport, force_new=True)


def producer_status(producer_id: str) -> dict:
    """What state a producer is in, without reading the logs (§8.1, §2.4).

    Args:
        producer_id: from `register()`.

    Returns:
        `{producer_id, server_alias, base_path, unauthorized, closing,
        pending, sent, nacked}`. `unauthorized` is §8.1's "PENDING, halted":
        the api_key was refused, on the SSE channel or on a send, and nothing
        will ship until `re_register()` is called. `pending`/`sent` are §2.4's
        outstanding set; `nacked` is what is waiting on `resolve()`.

    Raises:
        NotRegisteredError: no such producer.

    A snapshot, like `metrics()`. It exists because a halted producer was
    otherwise observable only in a log line, which no app can branch on.
    """
    producer = _require(producer_id)
    return {
        "producer_id": producer.producer_id,
        "server_alias": producer.alias,
        "base_path": producer.base_path,
        "unauthorized": producer.unauthorized,
        "closing": producer.closing,
        "pending": producer.store.file_count("pending"),
        "sent": producer.store.file_count("sent"),
        "nacked": producer.store.file_count("nacked"),
    }


def register_url(base_url: str, service_name: str) -> str:
    """§5's path: `{base_path}/talium/{service_name}/v1/register`."""
    return f"{base_url.rstrip('/')}/talium/{service_name}/v1/register"


def close_url(base_url: str, service_name: str) -> str:
    """§5's path: `{base_path}/talium/{service_name}/v1/close`."""
    return f"{base_url.rstrip('/')}/talium/{service_name}/v1/close"


def _post_register(binding_cfg, descriptor: dict, transport) -> dict:
    """POST `/register` and return the issued identity + the server's limits.

    Raises:
        RegistrationError: anything other than a 200 with a usable body.
            Terminal by design (§10.1) -- there is no retry loop here, because
            retrying a refused enrollment never helps.

    TODO: §10.1's `enrollment_key` mode needs the client to present
    `X-Talium-Enroll-Key`. There is no client-side config key for the secret
    yet, so only `mode: open` (v1's default) is reachable from here.
    """
    service_name = binding_cfg.service_name or binding_cfg.alias
    url = register_url(binding_cfg.base_path, service_name)
    try:
        with httpx.Client(transport=_borrowed(transport),
                          timeout=httpx.Timeout(_REGISTER_TIMEOUT_S),
                          # A 3xx would hand the descriptor to whatever host it
                          # names, and the reply would mint an identity this
                          # client then stores against the configured endpoint.
                          follow_redirects=False) as client:
            response = client.post(url, json=descriptor)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # Terminal, but the *reason* names the endpoint, not the descriptor.
        raise RegistrationError(
            f"register failed against {url}: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise RegistrationError(
            f"register was refused with http {response.status_code} "
            f"({_reason_of(response)})")
    try:
        body = response.json()
    except ValueError as exc:
        raise RegistrationError("register returned a non-JSON body") from exc
    missing = [k for k in ("producer_id", "api_key", "max_msg_bytes")
               if not body.get(k)]
    if missing:
        raise RegistrationError(
            f"register response is missing {', '.join(missing)}")
    return body


def _borrowed(transport):
    """Wrap a caller's transport so this module's short-lived `httpx.Client`s
    cannot close it.

    `with httpx.Client(transport=t)` closes `t` on the way out, and the same
    transport is the one `register()` stored on the producer for `/event` and
    `/notify` -- so handing it over directly kills the caller's connection pool
    after the first `register()`, and again after `close()`. `transport.py` and
    `sse.py` already take this precaution; this is the third caller, not a
    fourth mechanism. `None` means "httpx builds its own", which is ours to
    close, so it passes straight through.
    """
    return None if transport is None else _CallerOwnedTransport(transport)


def _reason_of(response: httpx.Response) -> str:
    """The body's `reason`, bounded. Never the whole body -- it is a peer's
    text and it ends up in an exception message."""
    try:
        body = response.json()
    except ValueError:
        return "no reason"
    reason = body.get("reason") if isinstance(body, dict) else None
    return str(reason)[:200] if reason else "no reason"


# -- §6.1 item 2 ----------------------------------------------------------


def on_notify(producer_id: str, notify_callback_fn: Callable,
              notify_on_ack: bool = False) -> None:
    """Register the outcome callback and open the SSE channel (§6.1 item 2).

    Args:
        producer_id: from `register()`.
        notify_callback_fn: called as `notify_callback_fn(msg_id, notify_ev)`
            with a `NotifyEv` (§8.2), **on the SSE reader thread**. It must be
            thread-safe and quick: a slow callback stalls every notification
            behind it (§6.4). An exception in it is caught and logged by type
            and location -- never by message, which is app data (§10.4).
        notify_on_ack: off by default, because on an `ack` the record is
            already purged and the app has no decision to make (§2.2). Set it
            for a producer that gates on confirmed delivery, e.g. marking a
            source file done. Deliberately not a YAML knob -- config could
            silently break such a producer (§6.1).

    Raises:
        NotRegisteredError: no such producer.
        NoCallbackError: `notify_callback_fn` is not callable. **Required
            before the channel opens** (§4.1): on restart `sent/` holds the
            previous run's records and the server may push their outcomes the
            instant the stream opens, so a reader without a handler would drop
            exactly the notices the rule protects.

    Idempotent in the way that matters: calling it again swaps the callback and
    leaves the one reader running. Two readers would mean two copies of every
    outcome.
    """
    producer = _require(producer_id)
    if not callable(notify_callback_fn):
        raise NoCallbackError(
            "notify_callback_fn must be callable and must be registered "
            "before the SSE channel opens (design §4.1)")
    producer.set_callback(notify_callback_fn, bool(notify_on_ack))
    with _STATE_LOCK:
        if producer.sse is None:
            producer.sse = SseReader(
                notify_url(producer.base_path, producer.service_name),
                producer.api_key,
                on_event=producer.handle_notice,
                # §4.3's sweep: the cleaner performs it at the top of its next
                # pass, not on the reader thread.
                on_epoch=producer.cleaner.on_epoch,
                idle_timeout_s=producer.idle_timeout_s,
                producer_id=producer.producer_id,
                server_alias=producer.alias,
                on_unauthorized=producer.on_unauthorized,
                _transport=producer.transport)
        reader = producer.sse
    reader.start()


# -- §6.1 item 4 ----------------------------------------------------------


def set_info(producer_id: str, info: dict) -> None:
    """Set the producer-level `info` bag (§6.1 item 4, §7).

    Replaces it wholesale, may be called at any time, and affects subsequent
    sends only. The dict is copied, so mutating the caller's copy afterwards
    changes nothing.

    Raises:
        NotRegisteredError: no such producer.
    """
    _require(producer_id).set_info(info)


# -- §6.1 item 5 ----------------------------------------------------------


def send(producer_id: str, event: dict, info: dict | None = None, *,
         event_id: str | None = None, dedup: bool = False) -> SendResult:
    """Send one msg. Synchronous to a definitive answer or a timeout (§2.2).

    Args:
        producer_id: from `register()`.
        event: the payload. Opaque to this library, and never logged (§10.4).
        info: per-msg bag, **shallow**-merged over the producer-level one with
            per-msg keys winning (§7).
        event_id: optional app-supplied key (§7). Not the transport's key --
            `msg_id` is.
        dedup: reserved for §9.1's lib-side dedup cache; v1 never acts on it.
            Requires `event_id`.

    Returns:
        `models.SendResult(err_code, reason, msg_id)` -- §8.1's code plus a
        free-form, human-only `reason`. **Code must never branch on `reason`.**
        `msg_id` is `None` for the two codes that mean nothing was stored
        (`slot_full`, `too_large`), and the durable id otherwise.

    Raises:
        NotRegisteredError: `send()` before `register()`.
        ProducerClosingError: `send()` after `close()` (§2.4 step 1).
        InvalidEnvelopeError: `dedup=True` with no `event_id` (§7), or an
            `event` that is not a dict.

    Never raises for a runtime condition (§8.3). A dead network, a refused
    connection, a full server queue -- all of them come back as a code, and the
    record is already durable by then, so the at-least-once promise holds
    whether or not this one attempt landed. Exactly one POST is made; retry is
    the cleaner loop's (§4.4).

    Note: the record is in `pending/` from `put_pending` until the POST comes
    back, and a concurrent cleaner pass can pick it up in that window and send
    it a second time. That is at-least-once behaving as specified -- §1 gives
    no ordering guarantee, §9.1 makes the app idempotent, and v1 has no
    lib-side dedup -- and closing it would need a "claimed" state the durable
    store deliberately does not have (the folder IS the state, §3). Both
    attempts are bounded by the same window.
    """
    producer = _require(producer_id)
    # §2.4 step 1, and it is a *gate*, not a flag read: everything from here to
    # the return is inside it, so `close()` either waits this send out or
    # refuses it. See `_Producer.begin_close`.
    producer.begin_send()
    try:
        return _send_locked(producer, event, info, event_id, dedup)
    finally:
        producer.end_send()


def _send_locked(producer: _Producer, event: dict, info: dict | None,
                 event_id: str | None, dedup: bool) -> SendResult:
    """`send()`'s body, run with the producer's send gate held."""
    if dedup and not event_id:
        raise InvalidEnvelopeError(
            "dedup=True requires an event_id (design §7)")
    if not isinstance(event, dict):
        raise InvalidEnvelopeError(
            f"event must be a dict, got {type(event).__name__} (design §7)")

    msg_id = producer.allocator.next()
    env = Envelope(
        event_id=event_id, dedup=bool(dedup), producer_id=producer.producer_id,
        msg_id=msg_id, service_name=producer.service_name,
        created_at=_utc_now_iso(), producer_desc=producer.producer_desc,
        info=producer.merged_info(info), payload=event)

    # §8.1: checked client-side, BEFORE storing, against the limit register()
    # returned. `post_once` re-serialises the envelope rather than taking these
    # bytes, because the size that matters here is the one the *server* will
    # measure and that has to be computed the same way on both paths.
    size_bytes = len(env.model_dump_json().encode("utf-8"))
    if size_bytes > producer.max_msg_bytes:
        _log.warning("msg_too_large", **producer._who(), msg_id=msg_id,
                     size_bytes=size_bytes,
                     max_msg_bytes=producer.max_msg_bytes)
        return SendResult(
            err_code=str(Code.TOO_LARGE), msg_id=None,
            reason=(f"envelope is {size_bytes} bytes; the server's "
                    f"max_msg_bytes is {producer.max_msg_bytes}"))

    if not _wait_for_slot(producer):
        # §5.1's only outright rejection: the durable store is at max_pending
        # and stayed there for slot_wait_s. NOT stored -- the caller backs off
        # or drops.
        pending = producer.store.count("pending")
        _metrics.counter("slot_full_total")
        _log.warning("slot_full", **producer._who(), pending_count=pending,
                     max_pending=producer.settings.max_pending,
                     slot_wait_s=producer.settings.slot_wait_s)
        return SendResult(
            err_code=str(Code.SLOT_FULL), msg_id=None,
            reason=(f"durable store is at max_pending="
                    f"{producer.settings.max_pending} after "
                    f"{producer.settings.slot_wait_s}s"))

    rec = MsgRecord(msg_id=msg_id, envelope=env, created_at=env.created_at)
    producer.store.put_pending(rec)
    _metrics.counter("msgs_stored_total")
    # size_bytes, never the payload (§10.4).
    _log.info("msg_stored", **producer._who(), msg_id=msg_id,
              size_bytes=size_bytes)

    if not producer.window.acquire():
        # §5.1: a full window NEVER fails a send. Stored, `stored_throttled`,
        # and the cleaner ships it when a slot frees. No POST is attempted --
        # that is the whole point of the throttle.
        return SendResult(err_code=str(Code.STORED_THROTTLED), msg_id=msg_id,
                          reason="max_in_flight reached; queued for the "
                                 "cleaner loop")

    outcome = producer._send_one(rec)                 # exactly one POST (§2.2)
    return _apply(producer, msg_id, outcome)


def _wait_for_slot(producer: _Producer) -> bool:
    """Wait up to `slot_wait_s` for room under `max_pending` (§5.1).

    A real wait, not a formality: an outage that filled the store can drain
    while the caller is here (an ACK purges a record), and rejecting on the
    first look would fail sends the store had room for. `total()`, not
    `count("pending")`, because §5.1 says this limit "bounds the durable
    store" and every record in it costs the same disk. (§4.4's *alarm* watches
    `pending/` alone -- that one is about an outage's backlog, not the disk.)
    """
    limit = producer.settings.max_pending
    deadline = time.monotonic() + max(float(producer.settings.slot_wait_s), 0.0)
    while True:
        if producer.store.total() < limit:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_TICK_S, remaining))


def _apply(producer: _Producer, msg_id: int, outcome: PostOutcome) -> SendResult:
    """One POST's §8.1 code -> the state move it implies, and the result.

    A window slot is held on entry. Every branch either keeps it (the msg is
    genuinely in flight, i.e. `SENT`) or hands it back exactly once -- a leaked
    slot silently shrinks the window until nothing ships, and a double release
    makes it stop bounding anything (§5.1).
    """
    code, reason = outcome.code, outcome.reason
    store = producer.store

    if code is Code.OK:
        try:
            store.mark_sent(msg_id)                   # slot stays taken
        except (FileNotFoundError, OSError) as exc:
            # An outcome for this msg beat the transition (the server can push
            # an ACK before this call returns). Nothing is in flight for us to
            # account for, so the slot goes back.
            producer.window.release()
            _log.info("send_record_gone", **producer._who(), msg_id=msg_id,
                      exc_type=type(exc).__name__)
    elif code is Code.ALREADY_PROCESSED:
        # §8.1: purged inline, terminal. Reserved for §9.1; v1's server never
        # emits it, but honouring it here keeps it from becoming a msg that
        # retries forever against a server that already has it.
        producer.window.release()
        _purge_quietly(producer, msg_id)
    elif code is Code.REJECTED:
        # §8.1: 400 -> NACKED. The app's decision, via resolve(): fix and
        # resend, or purge. Left PENDING it would be retried forever against a
        # server that will never take it.
        _nack_now(producer, msg_id, reason or str(code))
    elif code is Code.TOO_LARGE:
        # The server's 413 backstopping a stale client-side limit (§8.1). The
        # table says `too_large` means NOT stored, and the caller still holds
        # the event, so the record is purged rather than parked -- keeping that
        # promise true for every `too_large` this function can return.
        producer.window.release()
        _purge_quietly(producer, msg_id)
        _log.warning("msg_too_large_server", **producer._who(), msg_id=msg_id,
                     max_msg_bytes=producer.max_msg_bytes,
                     action="the registered max_msg_bytes is stale; "
                            "re-register or split at the producer")
    else:
        # stored_timeout / stored_unreachable / server_busy / unauthorized:
        # nothing landed, the record stays PENDING and the loop retries. The
        # promise already holds -- it is durable.
        producer.window.release()
        if code is Code.UNAUTHORIZED:
            producer.unauthorized = True
            _log.warning("producer_unauthorized", **producer._who(),
                         msg_id=msg_id, key_prefix=redact_key(producer.api_key),
                         action="re-register: the api_key was not accepted")
    return SendResult(err_code=str(code), reason=reason, msg_id=msg_id)


def _purge_quietly(producer: _Producer, msg_id: int) -> str | None:
    """`store.purge`, with §8.3 honoured: a failing disk is a runtime
    condition, and `send()`/`resolve()` are documented never to raise for one.
    An unguarded `purge()` puts an `OSError` straight out of `send()` -- past
    the `err_code` the caller was promised, and out of a call whose msg is
    already durable."""
    try:
        return producer.store.purge(msg_id)
    except OSError as exc:
        _log.warning("purge_failed", **producer._who(), msg_id=msg_id,
                     exc_type=type(exc).__name__)
        return None


def _nack_now(producer: _Producer, msg_id: int, reason: str) -> None:
    """PENDING -> NACKED, via SENT. The record is in `pending/` here.

    Still two hops even though `mark_nacked` now accepts a `pending/` record
    (that widening is for the SSE outcome path, which has no window slot of its
    own to reason about -- see `_Producer.handle_notice`). Here the two hops
    are what makes the window slot accountable: this call holds one, `SENT` is
    the only state that keeps it, and splitting the move lets each failure
    point say whether the msg ended up in flight or not. Both hops are one
    atomic rename, so a crash between them leaves the msg `SENT` and
    `sent_timeout_s` picks it up -- never a lost record. Mirrors
    `cleaner._nack`, including which of the two hops owns the slot.
    """
    try:
        producer.store.mark_sent(msg_id)
    except (FileNotFoundError, OSError) as exc:
        producer.window.release()
        _log.info("send_record_gone", **producer._who(), msg_id=msg_id,
                  exc_type=type(exc).__name__)
        return
    try:
        producer.store.mark_nacked(msg_id, reason)
    except (FileNotFoundError, OSError) as exc:
        # The first hop landed: the record really is in `sent/`, so the slot
        # stays taken and the cleaner's demotion releases it.
        _log.warning("send_nack_incomplete", **producer._who(), msg_id=msg_id,
                     exc_type=type(exc).__name__)
        return
    producer.window.release()


# -- §6.1 item 6 ----------------------------------------------------------


def resolve(producer_id: str, msg_id: int,
            action: Action = Action.RETAIN) -> None:
    """Resolve a NACKed msg (§6.1 item 6, §2.2 step 4).

    Args:
        producer_id: from `register()`.
        msg_id: the id from the `NotifyEv` (or from the `SendResult`).
        action: `PURGE` deletes the record -- terminal. `RETAIN` returns it to
            `PENDING`, durably, and the cleaner loop resends it. **`RETAIN` is
            the default**, and it is also what "no `resolve()` call at all"
            means (§2.2 step 5): an unresolved NACK is a retain, and §2.3 folds
            it into `PENDING` on the next start.

    Raises:
        NotRegisteredError: no such producer.

    Tolerant of a msg_id it cannot find: an ACK may have purged the record
    first, and a resolve that raised on a race the app cannot see would be a
    programming error the app did not make.

    Does **not** POST. RETAIN's resend belongs to the cleaner loop, like every
    other retry (§2.2, §4.4); `flush()` is there for an app that wants it now.

    Still legal after `close()`, deliberately, unlike `send()` and `flush()`:
    it writes no msg and makes no request, and §2.2 step 5 leaves the decision
    open until the app makes it. A `PURGE` after the drain is the app saying
    "do not resend this next start", which §2.3 would otherwise decide for it.
    """
    producer = _require(producer_id)
    act = Action(action)
    if act is Action.PURGE:
        _purge_quietly(producer, msg_id)
    else:
        try:
            producer.store.restore_to_pending(msg_id)
        except (FileNotFoundError, OSError) as exc:
            _log.info("resolve_record_gone", **producer._who(), msg_id=msg_id,
                      action=str(act), exc_type=type(exc).__name__)
            return
    _metrics.counter("msgs_resolved_total", action=str(act))
    _log.info("msg_resolved", **producer._who(), msg_id=msg_id,
              code=str(Code.NACK), action=str(act))


# -- §6.1 item 8 ----------------------------------------------------------


def flush(producer_id: str) -> None:
    """Ship everything in `pending/` now (§6.1 item 8).

    One cleaner pass, run synchronously on the caller's thread: it sends while
    `in_flight < max_in_flight`, so a backlog larger than the window drains
    over several passes rather than ignoring the limit that protects the
    server (§5.1).

    Raises:
        NotRegisteredError: no such producer.
        ProducerClosingError: after `close()`. Same rule as `send()` and for
            the same reason (§2.4 step 1): `close()` has already stopped the
            cleaner and POSTed `/close`, so a flush here would put msgs on the
            wire for a producer whose threads are gone and whose outcomes
            nothing is listening for -- and `close()` has already reported
            those msgs as `unresolved`.
    """
    producer = _require(producer_id)
    if producer.closing:
        raise ProducerClosingError(
            f"producer {producer_id!r} is draining: close() stops accepting "
            "sends, and flush() is a send (design §2.4)")
    producer.cleaner.sweep_once()


# -- §6.1 item 7 ----------------------------------------------------------


def close(producer_id: str, timeout_s: float | None = None) -> CloseResult:
    """Client-driven drain (§6.1 item 7, §2.4).

    ```
    1. stop accepting send()      -> ProducerClosingError
    2. flush: ship everything in pending/
    3. wait until pending/ and sent/ are empty, or timeout
    4. POST close                 -> informational only
    5. return { status, unresolved }
    ```

    Args:
        producer_id: from `register()`.
        timeout_s: drain deadline. `None` -> `close_timeout_s` (§5.1).

    Returns:
        `CloseResult(status, unresolved)`. `status` is `"drained"` exactly when
        `unresolved == 0`.

    Raises:
        NotRegisteredError: no such producer.

    **The client drains, not the server** (§2.4): the server holds no durable
    state, so it cannot know what is outstanding. The client does -- the
    outstanding set *is* `pending/` + `sent/`, and that is what `unresolved`
    counts. `nacked/` is deliberately not in it: a NACKed msg is waiting on the
    *app's* PURGE/RETAIN decision, which no amount of draining can supply, and
    §2.3 folds whatever is left into `PENDING` on the next start.

    Msgs still outstanding at the timeout stay durable. Nothing is dropped and
    nothing is lost -- next start, §2.3 resends `SENT` immediately rather than
    waiting out `sent_timeout_s`.

    **`timeout_s` bounds the whole call, not the gaps between sweeps.** The
    deadline is pushed down into the flush (`Cleaner.sweep_once(deadline)`) and
    into the wait for sends already in flight, because a drain that only looked
    at the clock *between* passes overshot by the entire backlog: 8 pending
    msgs against a 0.4 s server took 3.31 s under `close(timeout_s=0.2)`, and
    at the defaults (`max_in_flight=64`, `req_timeout_s=10`) a `close(5)` could
    block for ~640 s. One POST already on the wire still finishes, so the
    honest bound is `timeout_s + req_timeout_s`.

    Idempotent: a second call re-reports the state, re-POSTs nothing. The
    first-caller test and the flag are set atomically, so two threads closing
    at once do not both drain and both POST `/close`.
    """
    producer = _require(producer_id)
    if timeout_s is None:
        timeout_s = producer.settings.close_timeout_s
    deadline = time.monotonic() + max(float(timeout_s), 0.0)

    # Step 1, atomically, and it waits out any `send()` already past the gate:
    # counting an empty store while a send is between `put_pending()` and its
    # POST is how `close()` came to report `{drained, 0}` with a msg still
    # outstanding -- and `shutdown()` then stopped the cleaner that would have
    # shipped it, so the record sat until the next process start.
    already_closing = producer.begin_close(deadline)

    if not already_closing:
        # The background loop is stopped and the drain drives `sweep_once()`
        # directly: a pass on the loop's own thread can be cut short by the
        # stop flag, and this one must not be -- it is the flush.
        producer.cleaner.stop(timeout_s=max(deadline - time.monotonic(), 0.0))
        while True:                                   # steps 2 + 3
            producer.cleaner.sweep_once(deadline=deadline)
            if _outstanding(producer) == 0:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(_TICK_S, remaining))

    unresolved = _outstanding(producer)
    status = "drained" if unresolved == 0 else "timeout"
    if not already_closing:
        _post_close(producer)                         # step 4, informational
        producer.shutdown(join_s=max(deadline - time.monotonic(), 0.1))
    _log.info("producer_closed", **producer._who(), status=status,
              unresolved=unresolved,
              nacked_count=producer.store.count("nacked"))
    return CloseResult(status=status, unresolved=unresolved)


def _outstanding(producer: _Producer) -> int:
    """§2.4's drain target: `pending/` + `sent/`.

    `file_count`, not `count`: a record too corrupt to parse is still a msg on
    disk that never reached the server, and `count()` hides it. Hiding it here
    would let `close()` answer `drained` with an undelivered msg in the store
    -- precisely the claim `status` exists to make. `nacked/` is excluded on
    purpose: it waits on the app's `resolve()`, which draining cannot supply,
    and §2.3 folds it into `PENDING` next start.
    """
    return (producer.store.file_count("pending")
            + producer.store.file_count("sent"))


def _post_close(producer: _Producer) -> None:
    """§2.4 step 4. Informational: the server holds no durable state, so this
    only lets it fire `on_close`, drop the SSE channel and touch `last_seen`.
    Never raises and never changes the returned status -- the drain already
    happened locally, and a server that cannot be reached does not make a
    drained producer undrained."""
    url = close_url(producer.base_path, producer.service_name)
    try:
        with httpx.Client(transport=_borrowed(producer.transport),
                          timeout=httpx.Timeout(_REGISTER_TIMEOUT_S),
                          follow_redirects=False) as client:
            client.post(url, json={"producer_id": producer.producer_id},
                        headers={"authorization": f"Bearer {producer.api_key}"})
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        _log.info("close_post_failed", **producer._who(),
                  exc_type=type(exc).__name__)


# -- §6.1 item 9 ----------------------------------------------------------


def poll_notify(producer_id: str) -> list[NotifyEv]:
    """Drain queued notifications instead of taking them on the SSE thread
    (§6.1 item 9, §6.4). For apps that own their threading.

    Returns:
        Every `NotifyEv` queued since the last call, oldest first, and clears
        the queue.

    Raises:
        NotRegisteredError: no such producer.

    The queue holds exactly what the callback is handed -- so an `ack` only
    appears when `notify_on_ack=True` (§2.2). It is bounded; if nothing ever
    polls, the oldest notices are dropped rather than growing the process.
    A callback is still required before the channel opens (§4.1), so an app
    that only polls registers a no-op one.
    """
    return _require(producer_id).drain_notices()


# -- §10.5 ----------------------------------------------------------------


def metrics() -> dict:
    """Point-in-time snapshot of every counter and gauge (§10.5).

    A snapshot, not a live view -- see `common.msging.metrics.metrics`.
    Exposure (endpoint, scraper, file) is explicitly out of scope.
    """
    return _metrics.metrics()


# -- test seams -----------------------------------------------------------
#
# Private, and named so. They exist because the facade's state is process-wide
# and its work happens on threads a test cannot otherwise reach into.


def _reset_for_tests() -> None:
    """Drop every producer and the loaded config, stopping the threads.

    Does not POST `/close` and does not touch the durable store: a test that
    calls this and re-runs `clnt_init()`/`register()` is simulating a process
    restart, which is exactly what §2.3's reconciliation needs.
    """
    global _config, _registry
    with _STATE_LOCK:
        producers = list(_producers.values())
        _producers.clear()
        _config = None
        _registry = None
    for producer in producers:
        producer.closing = True
        producer.shutdown(join_s=2.0)


def _producer_for_tests(producer_id: str) -> _Producer:
    return _require(producer_id)


def _store_for_tests(producer_id: str) -> MsgStore:
    return _require(producer_id).store


def _sse_for_tests(producer_id: str) -> SseReader | None:
    return _require(producer_id).sse


def _deliver_for_tests(producer_id: str, code: str, msg_id: int,
                       reason: str = "", info: dict | None = None) -> None:
    """Feed one `NotifyEv` through the real outcome path, as the SSE reader
    would. Same code, same ordering, no socket."""
    _require(producer_id).handle_notice(NotifyEv(
        msg_id=msg_id, producer_id=producer_id, code=code, reason=reason,
        info=info or {}, ts=_utc_now_iso()))
