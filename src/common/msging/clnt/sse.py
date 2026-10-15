"""The client's SSE outcome reader (design §4).

Outcomes travel server -> client on a long-lived SSE stream, separate from the
POST that delivered the msg (§4). This module is the client half of that: one
background **thread** running blocking httpx (§6.4 -- the client half is
threads, never asyncio), reconnecting forever, handing each frame to the app's
callback.

The frames are exactly what `srv/notify.py` emits, and this parser is written
against that peer rather than against a generic SSE spec reading:

    event: epoch     data: {"server_epoch": "..."}   first frame on every stream
    event: notify    data: {<notify_ev, §8.2>}
    : hb                                             keepalive every heartbeat_s

**The callback is required before the channel opens (§4.1).** `start()` raises
`NoCallbackError` without one, and raises *before* a thread or a socket exists.
The reason is not tidiness: on restart `sent/` holds records from the previous
run and the server may push their outcomes the instant the stream opens, so a
reader that connected without a handler would drop precisely the notices the
rule protects. `on_epoch` is required for the same reason one step out -- with
no epoch handler a server restart is undetectable, `sent/` is never swept
(§4.3), and at-least-once quietly stops holding.

**The idle timer is per-read, and that is deliberate.** §4.2 defines death as
"no bytes at all -- data or heartbeat -- for `idle_timeout_s`", which is a
per-read idle timer and not a total deadline: an SSE stream is *supposed* to
live for days. So `idle_timeout_s` is passed to httpx as `read`, where its
per-operation semantics are exactly right, and the deliberate difference from
`transport.py` (which needs one wall-clock deadline for one POST and has to
build it by hand) is recorded here so the next reader does not "fix" it.

What a per-read timer does *not* bound is a peer that dribbles bytes forever
without ever completing a frame -- every dribble rearms the timer. That is what
`MAX_EVENT_BYTES` is for. A stream is unbounded by nature; a single frame is
not.

**Reconnect is jittered and capped (§4.3).** Every producer reconnects,
re-epochs and resends into a server that booted seconds ago, so a fixed
backoff would land the whole herd on the same instant. Full jitter --
`uniform(0, min(cap, base * 2**attempt))` -- spreads it. The other half of
§4.3's herd control, capping resends at `max_in_flight`, is not here: this
module reports the epoch change, `clnt/window.py` bounds the resend, and the
cleaner loop (§4.4) is what joins them.

**The callback is arbitrary app code running on the reader thread.** It may
raise, and an exception in it must not kill the reader -- one bad notice would
otherwise cost every later outcome. Its exception *message* is never logged:
§10.4 forbids logging payloads, and a handler's message is app data that has
already been observed carrying an api_key and an SSN into a support bundle.
The type and the failing source location are logged instead; those are enough
to find the bug and cannot carry user data.

Counting: this module owns §10.5's `sse_reconnects_total` and
`epoch_sweeps_total` -- it is the only layer that sees a link drop or an epoch
change. `epoch_swept_msgs_total` belongs to whoever performs the sweep, since
only it knows how many msgs moved; the `on_epoch` callee must not re-count
`epoch_sweeps_total`.

Never logs the api_key (only `redact_key`'s prefix), and never logs a notice's
`reason` or `info` -- both are free-form and app-authored (§10.4). `msg_id` and
`code` are what debugging needs, the same line `srv/notify.py` draws.

Two §10.4 field lists were reconciled *to* this module rather than the other
way, and §10.4 now records both: `downtime_s` rides on `sse_connected`, not on
`sse_lost`, because when a link drops the outage has only just begun and there
is nothing to report yet (`sse_lost` carries `uptime_s` and `attempt`, which
are what is knowable then); and `epoch_sweep` carries `server_epoch` rather
than a `swept` count, because only the `on_epoch` callee knows how many msgs
moved -- that number is its `epoch_swept_msgs_total` (§10.5), not this
module's.

Nothing here imports `srv/` (§6.4, enforced by
`tests/common/msging/test_boundaries.py`).
"""
from __future__ import annotations

import json
import os
import random
import socket
import threading
import time
import zlib
from typing import Callable, Iterator

import httpx
from pydantic import ValidationError

from common.msging import metrics
from common.msging.clnt.transport import (
    _CallerOwnedTransport, _reject_retrying_transport)
from common.msging.errors import NoCallbackError
from common.msging.log import get_logger, redact_key
from common.msging.models import NotifyEv

_log = get_logger("msging.clnt.sse")

#: The field `srv/notify.py` puts in the epoch frame. Not `epoch`: reading the
#: wrong key leaves every restart undetected, and reading *any* key would let a
#: stray frame trigger a full sweep of `sent/`.
EPOCH_FIELD = "server_epoch"

EVENT_EPOCH = "epoch"
EVENT_NOTIFY = "notify"

#: Ceiling on one SSE event -- an unterminated line, or `data:` lines
#: accumulating without a dispatching blank line. Generous for a notify_ev
#: (§8.2 is a handful of scalars plus a small `info` bag) and small enough that
#: a peer which never sends a newline cannot turn this process's memory into
#: its own. Breaching it drops the connection; there is no way to resynchronise
#: mid-frame, and a reconnect costs nothing but a jittered sleep.
#:
#: The peer has the matching bound: `server.max_notice_bytes` keeps one
#: `notify_ev` under this. Without it an app returning an oversized `info` bag
#: makes every delivery attempt of that one notice drop the link -- a permanent
#: reconnect loop for that producer, escaped only by `sent_timeout_s`. Raising
#: this number without raising that one is safe; the reverse is not.
MAX_EVENT_BYTES = 65536

#: Most bytes handed back from one decompression step. Bounds the *expansion*
#: of a `content-encoding` the reader did not ask for; see `_Decoder`.
_DECODE_CHUNK = 16384

#: Slice size when replaying a body httpx already buffered (a MockTransport
#: answering with bytes rather than a socket).
_REPLAY_CHUNK = 16384

#: Reconnect curve (§4.3). Full jitter between 0 and the doubling curve.
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 30.0

#: Doubling past this contributes nothing but overflow risk -- 2**32 * base is
#: already far beyond any sane cap.
_MAX_SHIFT = 32

#: How long `stop()` waits for the reader thread before giving up and logging.
_JOIN_TIMEOUT_S = 10.0

#: Connect phase budget. Separate from `idle_timeout_s`, which is the *stream*
#: idle timer: a connect that hangs is a dead endpoint and should be retried on
#: the backoff curve long before a live stream would be declared idle.
_CONNECT_TIMEOUT_S = 10.0


def notify_url(base_url: str, service_name: str) -> str:
    """§5's path: `{base_path}/talium/{service_name}/v1/notify`."""
    return f"{base_url.rstrip('/')}/talium/{service_name}/v1/notify"


def _backoff_delay(attempt: int, base_s: float, cap_s: float) -> float:
    """Full-jitter exponential backoff (§4.3).

    `uniform(0, min(cap, base * 2**attempt))`. The jitter is the point, not a
    refinement: after a server restart every producer's link drops in the same
    instant, and a deterministic curve reconnects all of them together --
    exactly the thundering herd §4.3 exists to break up. Randomising the whole
    interval (rather than a fraction of it) spreads the reconnects across the
    entire window and lets some come back immediately.
    """
    ceiling = min(cap_s, base_s * float(2 ** min(max(attempt, 0), _MAX_SHIFT)))
    return random.uniform(0.0, max(ceiling, 0.0))


class _FrameTooLarge(Exception):
    """One SSE event exceeded `MAX_EVENT_BYTES`. Not resynchronisable."""


class _StreamError(Exception):
    """The peer's framing or encoding is unusable. Drop and reconnect."""


class _SseParser:
    """Bytes in, `(event_type, data)` out -- incremental and bounded.

    Written against `srv/notify.py`'s frames but tolerant in the two directions
    §10.2 requires: unknown *fields* are ignored, and unknown *event types* are
    handed up to be ignored there, so the protocol can grow a frame without a
    version bump.

    Bounded in both directions a peer can grow: an unterminated line, and a
    parade of short `data:` lines that never dispatch. Both raise
    `_FrameTooLarge`, which the caller turns into a reconnect.
    """

    def __init__(self, max_event_bytes: int = MAX_EVENT_BYTES) -> None:
        self._buf = bytearray()
        self._max = max_event_bytes
        self._event = ""
        self._data: list[str] = []
        self._data_bytes = 0

    def feed(self, chunk: bytes) -> list[tuple[str, str]]:
        """Consume `chunk`; return every event it completed.

        Raises:
            _FrameTooLarge: the pending line or the accumulated `data:` for one
                event passed `max_event_bytes`.
        """
        out: list[tuple[str, str]] = []
        self._buf += chunk
        while True:
            line = self._next_line()
            if line is None:
                break
            event = self._line(line)
            if event is not None:
                out.append(event)
        if len(self._buf) > self._max:
            # No newline in sight and the buffer is already over budget: this
            # frame can never be parsed, and waiting only costs more memory.
            raise _FrameTooLarge(
                f"no frame boundary within {self._max} bytes")
        return out

    def _next_line(self) -> bytes | None:
        """The next complete line, or None if the buffer holds only a partial.

        SSE allows CRLF, LF and CR line endings. A trailing lone `\\r` is
        ambiguous -- it may be the first half of a CRLF split across two socket
        reads -- so it waits for one more byte rather than dispatching a frame
        early.
        """
        buf = self._buf
        positions = [p for p in (buf.find(b"\n"), buf.find(b"\r")) if p != -1]
        if not positions:
            return None
        i = min(positions)
        if buf[i:i + 1] == b"\r":
            if i == len(buf) - 1:
                return None
            skip = 2 if buf[i + 1:i + 2] == b"\n" else 1
        else:
            skip = 1
        line = bytes(buf[:i])
        del buf[:i + skip]
        return line

    def _line(self, line: bytes) -> tuple[str, str] | None:
        if not line:
            return self._dispatch()
        if line.startswith(b":"):
            # A comment -- `: hb` (§4.2). It carries no data; its whole job is
            # to be bytes on the wire, which the read loop already noticed.
            #
            # Note this branch is intent, not arithmetic: a `:`-leading line
            # partitions to an empty field name below, which is never `event`
            # or `data`, so deleting it changes no output for any input. It
            # stays because the *next* edit to the field handling (a default
            # field name, a lenient partition) would silently make a comment
            # injectable, and because a comment must not reach `_dispatch` --
            # a keepalive is not a frame boundary.
            return None
        field, _, value = line.partition(b":")
        # Exactly one leading space is part of the framing, not the value.
        if value.startswith(b" "):
            value = value[1:]
        name = field.decode("utf-8", "replace")
        if name == "event":
            self._event = value.decode("utf-8", "replace")
        elif name == "data":
            self._data_bytes += len(value) + 1
            if self._data_bytes > self._max:
                raise _FrameTooLarge(
                    f"event data passed {self._max} bytes without a frame end")
            self._data.append(value.decode("utf-8", "replace"))
        # `id` and `retry` are spec fields this client does not use (there is
        # no Last-Event-ID resume here -- §4.3's epoch plus §4.4's resend is
        # the recovery story), and anything else is a field we have never heard
        # of. Both are ignored, per §10.2.
        return None

    def _dispatch(self) -> tuple[str, str] | None:
        event, data = self._event, self._data
        self._event, self._data, self._data_bytes = "", [], 0
        if not data:
            # A blank line with no data resets the event type and dispatches
            # nothing, per SSE. `event: notify` alone is not an event.
            return None
        return event or "message", "\n".join(data)


class _Decoder:
    """Undoes `content-encoding` with a hard ceiling on each step's output.

    The reader asks for `identity`, but a proxy can compress anyway, and
    letting httpx decode for us is not an option: `iter_bytes`/`iter_lines`
    hand a whole 64 KiB raw chunk to the content decoder before yielding
    anything, so the wire expands by its full compression ratio before any cap
    is consulted. The POST path measured 200 MB out of a 194 KB body against an
    8 KiB cap that way. `iter_raw` plus `zlib`'s `max_length` is the only shape
    where the bound actually binds.

    Yields at most `_DECODE_CHUNK` bytes per step and loops on
    `unconsumed_tail`, so a compressed stream is decoded incrementally rather
    than materialised.
    """

    def __init__(self, encoding: str) -> None:
        enc = (encoding or "").strip().lower()
        if enc in ("", "identity"):
            self._obj = None
        elif enc == "gzip":
            self._obj = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif enc == "deflate":
            self._obj = zlib.decompressobj(zlib.MAX_WBITS)
        else:
            # br/zstd: no bounded incremental decoder in the stdlib, and a
            # compressed event stream is already a misconfiguration. Dropping
            # the link is loud and safe; guessing is neither.
            raise _StreamError(f"unsupported content-encoding: {enc[:32]!r}")

    def feed(self, raw: bytes) -> Iterator[bytes]:
        if self._obj is None:
            yield raw
            return
        try:
            out = self._obj.decompress(raw, _DECODE_CHUNK)
            yield out
            while self._obj.unconsumed_tail:
                yield self._obj.decompress(self._obj.unconsumed_tail,
                                           _DECODE_CHUNK)
        except zlib.error as exc:
            raise _StreamError(f"malformed {type(exc).__name__} in stream") from exc


#: This module's half of `transport.py`'s shared retry rejection. §4.3 leans on
#: the reader's *own* jittered backoff to spread a reconnect storm, and a
#: transport that retries underneath it collapses N attempts into one
#: un-jittered burst and stretches the connect phase to N+1 timeouts.
_RETRY_WHY_SSE = ("SseReader owns its own jittered reconnect (design §4.3) and "
                  "transport-level retries would be un-jittered attempts "
                  "underneath it")


class SseReader:
    """The producer's SSE outcome channel, read on a background thread (§4).

    Args:
        url: the full `/notify` URL -- see `notify_url`.
        api_key: presented as `Authorization: Bearer ...`. Never logged; only
            `redact_key`'s prefix ever reaches a log line (§10.4).
        on_event: called with one `NotifyEv` (§8.2) per `event: notify` frame,
            on the reader thread, in stream order. **Required** (§4.1).
        on_epoch: called with the new `server_epoch` whenever it *changes* --
            including the first one seen, since a client that has just started
            cannot know the server did not restart while it was down. The
            callee's job is §4.3's sweep of `sent/` back to `pending/`.
            **Required**: without it a restart is undetectable.
        idle_timeout_s: §4.2's idle-read timer. No bytes at all -- data or
            heartbeat -- for this long means the link is dead (a half-open TCP
            connection is otherwise indistinguishable from a quiet server), and
            the reader reconnects on the backoff curve. Per *read*, not a total
            deadline: the stream is meant to live for days.
        producer_id: names the thread (`msging-sse-{producer_id}`) and rides on
            the log lines (§10.4). Optional -- the reader works without it.
        server_alias: §10.4's `server_alias` field on `sse_connected` /
            `sse_lost`.
        backoff_base_s / backoff_cap_s: the reconnect curve (§4.3).
        connect_timeout_s: budget for the connect phase alone.
        on_unauthorized: called with the status when `/notify` answers `401`
            or `403`. Optional. Return truthy to keep reconnecting on the
            backoff curve, falsy to stop the reader for good. This exists
            because §8.1's answer to `unauthorized` is *re-register*, which
            this class cannot do -- it holds an api_key, not a binding -- and
            a reader that retries a revoked key forever is a permanent load
            with no possible outcome. The owner of the binding (the client
            facade) supplies the policy; with no hook the behaviour is
            unchanged, i.e. retry, because going quiet by default would turn
            one bad proxy response into a silently dead outcome stream.
        _transport: injection seam. Tests pass an `httpx.MockTransport`; a
            caller wanting connection reuse passes one long-lived
            `httpx.HTTPTransport`, which is *not* closed here (only a transport
            this class created is). It must not have transport-level retries.

    Raises:
        ValueError: `_transport` has transport-level retries enabled (§8.3).

    Thread-safety: `start()`, `stop()` and `connected` are safe from any
    thread, including from inside a callback (a `stop()` from the reader thread
    does not try to join itself). The callbacks always run on the reader
    thread, one at a time, so they need no locking against each other -- but
    they *are* concurrent with the caller's own threads.

    TODO: the epoch is remembered in memory only. Across a client restart the
    first epoch always looks new, so `sent/` is swept once on every start --
    correct but not free. Persisting the last epoch alongside the msg store
    would make that sweep conditional.
    """

    def __init__(self, url: str, api_key: str,
                 on_event: Callable[[NotifyEv], None] | None,
                 on_epoch: Callable[[str], None] | None,
                 idle_timeout_s: float, *,
                 producer_id: str | None = None,
                 server_alias: str | None = None,
                 backoff_base_s: float = BACKOFF_BASE_S,
                 backoff_cap_s: float = BACKOFF_CAP_S,
                 connect_timeout_s: float = _CONNECT_TIMEOUT_S,
                 on_unauthorized: Callable[[int], bool] | None = None,
                 _transport: httpx.BaseTransport | None = None) -> None:
        if _transport is not None:
            _reject_retrying_transport(_transport, _RETRY_WHY_SSE)
        self._url = url
        self._api_key = api_key
        self._on_event = on_event
        self._on_epoch = on_epoch
        self._idle_timeout_s = max(float(idle_timeout_s), 0.001)
        self._producer_id = producer_id
        self._server_alias = server_alias
        self._backoff_base_s = float(backoff_base_s)
        self._backoff_cap_s = float(backoff_cap_s)
        self._connect_timeout_s = float(connect_timeout_s)
        self._on_unauthorized = on_unauthorized
        self._transport = _transport
        # Set when `on_unauthorized` declines to keep retrying; ends the loop
        # after the outage is logged, so the reason is never lost.
        self._halted = False

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._stop.set()                      # not running yet
        self._client: httpx.Client | None = None
        self._response: httpx.Response | None = None
        self._connected = False
        self._epoch: str | None = None
        # When the current stream opened (None while none is open), when the
        # current outage started (None while connected), and how long the last
        # outage lasted -- captured at connect so `sse_connected` can report it
        # from `_handle_epoch`, which is where the epoch is known.
        self._opened_at: float | None = None
        self._lost_at: float | None = None
        self._downtime_s = 0.0

    # ------------------------------------------------------------- public

    @property
    def connected(self) -> bool:
        """Whether a stream is currently open. A snapshot -- it can be stale
        the moment it returns, so use it for status, never check-then-act."""
        return self._connected

    @property
    def epoch(self) -> str | None:
        """The last `server_epoch` seen, or None before the first frame."""
        return self._epoch

    def start(self) -> None:
        """Open the channel on a background thread. Idempotent.

        Raises:
            NoCallbackError: `on_event` or `on_epoch` is missing or not
                callable (§4.1). Raised before any thread or socket exists, so
                a rejected reader cannot have already dropped a notice.
        """
        self._require_callbacks()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            # A halt is a property of one run, not of the object: a caller that
            # re-registered and restarted this reader is entitled to a fresh
            # verdict on the new key.
            self._halted = False
            # A fresh Event per run rather than clear(): a previous thread that
            # outlived its join must not be resurrected by this one's start.
            stop = threading.Event()
            self._stop = stop
            thread = threading.Thread(
                target=self._run, args=(stop,),
                name=f"msging-sse-{self._producer_id or 'anon'}", daemon=True)
            self._thread = thread
            thread.start()

    def stop(self, timeout_s: float = _JOIN_TIMEOUT_S) -> None:
        """Close the channel and join the reader thread. Idempotent.

        Must not hang: the reader spends most of its life parked in a blocking
        read that can be up to `idle_timeout_s` long (45 s by default), or
        asleep on the backoff curve, so this sets the flag *and* tears the live
        connection down under the read rather than waiting for either to
        expire. See `_abort` for why closing the client is neither enough on
        its own nor safe to add on top of what is.

        The client is therefore normally closed by the reader thread itself, on
        its way out. Only a join that *timed out* closes it from here: at that
        point the thread has already failed to unwind, and leaving its socket
        open until `idle_timeout_s` on top of that would cost a file descriptor
        as well as a thread.

        Safe from inside a callback: that runs on the reader thread, which
        cannot join itself, so the join is skipped and the loop exits at its
        next flag check.
        """
        with self._lock:
            thread, self._thread = self._thread, None
            self._stop.set()
            client, self._client = self._client, None
            response, self._response = self._response, None
        _abort(client, response)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout_s)
            if thread.is_alive():
                _log.warning("sse_stop_timeout", producer_id=self._producer_id,
                             server_alias=self._server_alias,
                             timeout_s=timeout_s)
                if client is not None:
                    try:
                        client.close()
                    except Exception:                  # noqa: BLE001
                        pass
        self._connected = False

    # ---------------------------------------------------------- internals

    def _require_callbacks(self) -> None:
        for name, cb in (("on_event", self._on_event),
                         ("on_epoch", self._on_epoch)):
            if not callable(cb):
                raise NoCallbackError(
                    f"{name} must be registered before the SSE channel opens "
                    "(design §4.1): the server may push outcomes for the "
                    "previous run the instant the stream opens, and a missing "
                    "callback would drop them silently"
                )

    def _run(self, stop: threading.Event) -> None:
        """The thread body. Nothing joins this for a result, so its death has
        to be recorded here or it is invisible."""
        try:
            self._loop(stop)
        except BaseException as exc:                   # noqa: BLE001
            # `_safe_call` and the per-attempt handler both catch `Exception`.
            # A `SystemExit` or `KeyboardInterrupt` out of app code is neither,
            # and it unwinds this thread. That is allowed to be fatal; it must
            # not be *silent* -- otherwise `connected` sits False forever, no
            # reconnect is ever attempted, and there is nothing to say why the
            # outcomes stopped. Type and location only (§10.4).
            _log.error("sse_reader_died", producer_id=self._producer_id,
                       server_alias=self._server_alias,
                       exc_type=type(exc).__name__, at=_origin(exc))
            raise
        finally:
            self._connected = False

    def _loop(self, stop: threading.Event) -> None:
        """Connect, read, reconnect. Ends only when `stop` is set."""
        attempt = 0
        while not stop.is_set():
            # None until a stream actually opens. Connect and header-read time
            # is *not* uptime: counting it made a blackhole peer -- accepts,
            # never answers -- look like a healthy stream every cycle, so
            # `attempt` reset on each pass and the curve below never climbed
            # toward its cap. Measured at 11 connects in 8 s, ~0.8 s apart,
            # which is precisely the un-spread storm §4.3 exists to break up.
            self._opened_at = None
            try:
                reason = self._connect_once(stop)
            except Exception as exc:                   # noqa: BLE001
                # Every network condition is a reconnect, never a raise: this
                # is a thread nobody joins for a result, and §8.3 keeps
                # exceptions for programming errors.
                reason = f"{type(exc).__name__}"
            finally:
                self._connected = False
            if stop.is_set():
                break
            opened_at = self._opened_at
            uptime_s = 0.0 if opened_at is None else time.monotonic() - opened_at
            # Once per *outage*, not once per attempt: a server down for an
            # hour would otherwise fill the log with one line per retry.
            # `_lost_at` clears when a stream re-establishes -- in
            # `_connect_once`, not when an epoch frame parses, or a peer whose
            # epoch frame is malformed or absent logs `sse_lost` once for the
            # whole process lifetime and every later `downtime_s` is cumulative
            # since the first-ever outage.
            if self._lost_at is None:
                self._lost_at = time.monotonic()
                _log.warning("sse_lost", producer_id=self._producer_id,
                             server_alias=self._server_alias, reason=reason,
                             uptime_s=round(uptime_s, 3), attempt=attempt)
            if self._halted:
                # The owner has been told the key is no good and does not want
                # another attempt (§8.1: re-register). Loud, because outcomes
                # stop here and nothing else in the client will say why.
                _log.error("sse_halted", producer_id=self._producer_id,
                           server_alias=self._server_alias, reason=reason,
                           key_prefix=redact_key(self._api_key))
                break
            metrics.counter("sse_reconnects_total")
            # A stream that lived longer than one base interval was healthy;
            # anything shorter is a flapping peer, and resetting the curve for
            # it would turn accept-then-close into a hot reconnect loop.
            if uptime_s >= self._backoff_base_s:
                attempt = 0
            delay = _backoff_delay(attempt, self._backoff_base_s,
                                   self._backoff_cap_s)
            attempt = min(attempt + 1, _MAX_SHIFT)
            # wait(), not sleep(): stop() must interrupt a long backoff.
            stop.wait(delay)

    def _connect_once(self, stop: threading.Event) -> str:
        """One connect + one stream. Returns why it ended."""
        client = httpx.Client(
            # A transport we built is ours to close; a caller's is wrapped so
            # that closing this attempt's client -- which happens on every
            # path, including from stop() on another thread -- cannot take
            # their long-lived pool down with it.
            transport=(httpx.HTTPTransport(retries=0) if self._transport is None
                       else _CallerOwnedTransport(self._transport)),
            timeout=httpx.Timeout(connect=self._connect_timeout_s,
                                  # §4.2's idle timer, in the one place httpx's
                                  # per-operation semantics are what is wanted.
                                  read=self._idle_timeout_s,
                                  write=self._connect_timeout_s,
                                  pool=self._connect_timeout_s),
            # A 3xx would hand the bearer token to whatever host it names.
            follow_redirects=False,
        )
        if not self._register(client, None, stop):
            client.close()
            return "stopped"
        try:
            with client.stream("GET", self._url, headers=self._headers()) as response:
                if response.status_code != 200:
                    if response.status_code in (401, 403):
                        self._halted = not self._ask_unauthorized(
                            response.status_code)
                    return f"http {response.status_code}"
                if not self._register(client, response, stop):
                    return "stopped"
                # The stream is open: this is where uptime starts, and where an
                # outage ends. `downtime_s` is measured to here rather than to
                # the epoch frame, so that a peer that never sends a usable one
                # still closes its outage.
                self._opened_at = time.monotonic()
                self._downtime_s = (0.0 if self._lost_at is None else
                                    round(time.monotonic() - self._lost_at, 3))
                self._lost_at = None
                self._connected = True
                self._pump(response, stop)
                return "eof"
        finally:
            with self._lock:
                if self._client is client:
                    self._client = None
                    self._response = None
            client.close()

    def _register(self, client: httpx.Client, response: httpx.Response | None,
                  stop: threading.Event) -> bool:
        """Publish what `stop()` has to tear down, unless stopping already.

        Under the same lock `stop()` uses, so a connection opened in the window
        between the flag being set and the loop noticing cannot be missed and
        left holding a socket for `idle_timeout_s`.
        """
        with self._lock:
            if stop.is_set():
                return False
            self._client = client
            self._response = response
            return True

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._api_key}",
            "accept": "text/event-stream",
            "cache-control": "no-cache",
            # Nothing about an event stream benefits from compression, and not
            # asking for it is the cheap half of the bound `_Decoder` enforces.
            "accept-encoding": "identity",
        }

    def _pump(self, response: httpx.Response, stop: threading.Event) -> None:
        """Read the stream to its end, dispatching every frame it completes.

        `iter_raw`, never `iter_bytes`/`iter_lines`: those decode a whole raw
        chunk before yielding anything, which puts the expansion of a
        compressed body outside any cap this module could apply (see
        `_Decoder`). Each `next()` on it is one socket read, which is where
        §4.2's idle timer lives.
        """
        parser = _SseParser()
        if response.is_stream_consumed:
            # A transport that answers with bytes rather than a socket (e.g.
            # `httpx.MockTransport`) has httpx buffer *and decode* the body at
            # construction: there is no stream left to pull and nothing left to
            # bound, so it is replayed as identity.
            decoder = _Decoder("")
            chunks: Iterator[bytes] = _slices(response.content, _REPLAY_CHUNK)
        else:
            decoder = _Decoder(response.headers.get("content-encoding", ""))
            chunks = response.iter_raw()
        for raw in chunks:
            if stop.is_set():
                return
            for block in decoder.feed(raw):
                for event, data in parser.feed(block):
                    self._dispatch(event, data)
                    if stop.is_set():
                        return

    def _dispatch(self, event: str, data: str) -> None:
        if event == EVENT_EPOCH:
            self._handle_epoch(data)
        elif event == EVENT_NOTIFY:
            self._handle_notify(data)
        # Anything else is a frame this version has never heard of. §10.2 makes
        # ignoring it the contract, not a fallback.

    def _handle_epoch(self, data: str) -> None:
        epoch = _epoch_of(data)
        if epoch is None:
            # Never guess: a spurious epoch would sweep all of `sent/`.
            _log.warning("sse_frame_invalid", producer_id=self._producer_id,
                         server_alias=self._server_alias, kind=EVENT_EPOCH)
            return
        changed = epoch != self._epoch
        self._epoch = epoch
        _log.info("sse_connected", producer_id=self._producer_id,
                  server_alias=self._server_alias, server_epoch=epoch,
                  epoch_changed=changed, downtime_s=self._downtime_s,
                  key_prefix=redact_key(self._api_key))
        if not changed:
            # Same process as last time: nothing was lost, nothing to sweep.
            # Sweeping anyway would resend all of `sent/` on every reconnect.
            return
        metrics.counter("epoch_sweeps_total")
        _log.warning("epoch_sweep", producer_id=self._producer_id,
                     server_alias=self._server_alias, server_epoch=epoch)
        self._safe_call(self._on_epoch, epoch, "on_epoch")

    def _handle_notify(self, data: str) -> None:
        try:
            ev = NotifyEv.model_validate_json(data)
        except (ValidationError, ValueError):
            # One malformed frame must not end the stream: the next one may
            # well be the ACK a msg is waiting on. Never echo `data` -- it is
            # the payload §10.4 forbids logging.
            _log.warning("sse_frame_invalid", producer_id=self._producer_id,
                         server_alias=self._server_alias, kind=EVENT_NOTIFY,
                         size_bytes=len(data))
            return
        # msg_id and code only: `reason` is server-authored free text and
        # `info` is the app's bag, and both can echo real user data (§10.4).
        _log.debug("notice_received", producer_id=ev.producer_id,
                   server_alias=self._server_alias, msg_id=ev.msg_id,
                   code=ev.code)
        self._safe_call(self._on_event, ev, "on_event")

    def _ask_unauthorized(self, status: int) -> bool:
        """Whether to keep reconnecting after a `401`/`403`.

        No hook -> True, which is this class's historical behaviour and the
        safe default: an auth proxy that answers 401 once must not be able to
        silence a producer's outcome stream permanently. A hook that raises is
        treated the same way, and only its type and location are logged -- it
        is app code, and its message is app data (§10.4).
        """
        if self._on_unauthorized is None:
            return True
        try:
            return bool(self._on_unauthorized(status))
        except Exception as exc:                       # noqa: BLE001
            _log.warning("sse_callback_error", producer_id=self._producer_id,
                         server_alias=self._server_alias,
                         callback="on_unauthorized",
                         exc_type=type(exc).__name__, at=_origin(exc))
            return True

    def _safe_call(self, callback, arg, name: str) -> None:
        """Run app code without letting it take the reader down.

        The exception *message* is never logged. §10.4 forbids logging
        payloads, and a handler's message is app data -- one shipped an api_key
        and an SSN into a support bundle. The type and the failing source line
        locate the bug and cannot carry user data.
        """
        try:
            callback(arg)
        except Exception as exc:                       # noqa: BLE001
            _log.warning("sse_callback_error", producer_id=self._producer_id,
                         server_alias=self._server_alias, callback=name,
                         exc_type=type(exc).__name__, at=_origin(exc))


def _abort(client: httpx.Client | None, response: httpx.Response | None) -> None:
    """Tear down a live connection from another thread, hard.

    `client.close()` alone is not enough, and it is also **not safe to do on
    top of the thing that is**. Both halves of that were measured, on macOS,
    against a real server:

        shutdown(SHUT_RDWR) only ......... 0 missed wakeups in 900
        close() only ..................... 899 missed in 900
        shutdown() then close() .......... 18 missed in 900

    Closing a socket's fd does not wake a thread already blocked reading it on
    a BSD-derived stack -- the reader stays parked until its own read timeout.
    `shutdown(SHUT_RDWR)` does wake it, reliably: it delivers EOF, the read
    returns at once and the whole stream unwinds. But `client.close()` issued
    immediately afterwards closes that same fd, and roughly 2% of the time it
    gets there before the woken reader has come back out of its read -- taking
    the wakeup with it and leaving the thread parked for the full
    `idle_timeout_s` (45 s at the defaults), which is a thread and a socket
    outliving the `close()` that told the app the producer was done. That is
    the ~1-in-200 `sse_stop_timeout` this function used to produce.

    So the two are alternatives, not a sequence. A socket we successfully shut
    down is left for the reader thread to close on its way out --
    `_connect_once`'s `finally` closes the client on every path, on the thread
    that owns it. `client.close()` is the fallback for when there is nothing
    blocked on a socket to wake: a `MockTransport` (buffered body, no socket),
    or a connect that never produced a response, where closing the client is
    the only lever there is.

    httpcore publishes the socket as `response.extensions["network_stream"]`,
    which is the only handle on it httpx exposes.

    Every step is best-effort: this races the reader finishing normally, and a
    connection already halfway shut is not an error worth propagating to a
    caller whose only intent was "stop".
    """
    if _shutdown_socket(response):
        return
    if client is not None:
        try:
            client.close()
        except Exception:                              # noqa: BLE001
            pass


def _shutdown_socket(response: httpx.Response | None) -> bool:
    """`shutdown(SHUT_RDWR)` the socket behind `response`. True if it landed.

    False means there was no socket to wake -- no response, no
    `network_stream`, or a shutdown the kernel refused because the connection
    is already gone -- and the caller falls back to closing the client.
    """
    if response is None:
        return False
    try:
        stream = response.extensions.get("network_stream")
        sock = stream.get_extra_info("socket") if stream is not None else None
        if sock is None:
            return False
        sock.shutdown(socket.SHUT_RDWR)
        return True
    except Exception:                                  # noqa: BLE001
        return False


def _slices(body: bytes, size: int) -> Iterator[bytes]:
    for i in range(0, len(body), size):
        yield body[i:i + size]


def _epoch_of(data: str) -> str | None:
    """`server_epoch` out of an epoch frame, or None if it is not usable."""
    try:
        body = json.loads(data)
    except (ValueError, RecursionError, MemoryError):
        return None
    if not isinstance(body, dict):
        return None
    epoch = body.get(EPOCH_FIELD)
    if isinstance(epoch, str) and epoch:
        return epoch
    return None


def _origin(exc: BaseException) -> str:
    """`file.py:lineno:function` of where `exc` was raised. Never its message."""
    tb = exc.__traceback__
    if tb is None:
        return "?"
    while tb.tb_next is not None:
        tb = tb.tb_next
    code = tb.tb_frame.f_code
    return f"{os.path.basename(code.co_filename)}:{tb.tb_lineno}:{code.co_name}"
