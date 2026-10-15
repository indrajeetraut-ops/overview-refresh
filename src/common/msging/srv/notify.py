"""SSE notification registry -- the server's outbound half (design §4).

Outcomes travel server -> client on a long-lived SSE stream, separate from the
POST that delivered the msg (§4). This module owns that stream:

    event: epoch     first frame on every stream, the process boot id (§4.3)
    event: notify    one notify_ev (§8.2)
    : hb             keepalive every heartbeat_s (§4.2)

**One ring, always.** A notice is never handed to a subscriber directly; it is
always appended to the producer's bounded ring and the live stream (if any)
drains it. That is what makes `publish()` non-blocking and O(1): it is called
from a worker that has just finished a msg, and a subscriber that stopped
reading -- a half-open TCP link (§4.2) -- must cost the server nothing. The
ring is the same structure §6.2 already requires for "SSE down"; using it in
both cases means there is exactly one place a notice can be lost, and exactly
one place drops are counted.

**Overflow drops the oldest.** §6.2 says "Overflow or expiry drops" without
saying which end, so this is a choice, recorded here: `deque(maxlen=...)`
evicts the head, keeping the newest notices. The newest outcome is the most
likely to still be actionable, and every drop -- either end -- is recovered by
the client's `sent_timeout_s` resend (§4.4), so the choice costs correctness
nothing.

**One stream per producer, last connect wins.** A producer binding has one
notify channel (§4.1). When a client reconnects over a link the server has not
yet noticed is dead, the stale generator is superseded and ends; without that
the two streams would split the ring between them and half the outcomes would
go to a socket nobody is reading. The stream_id is claimed by `subscribe()`
itself, not by the generator body: a body only runs on the first `__anext__`,
which would make the winner whichever stream the server iterated first rather
than whichever connected last.

**Deliver, then remove.** The drain peeks at the ring head, yields the frame,
and only pops/counts/logs once that yield resumes. A consumer cancelled while
the generator is suspended at the yield -- exactly what Starlette does on
client disconnect -- therefore leaves the notice in the ring for the reconnect.
The cost is an at-most-one duplicate if the socket did in fact carry the frame,
which §4.4/§9.1 already make safe (the client purges by msg_id and a redundant
outcome is a no-op). The alternative -- pop first -- loses the notice
*silently*: absent from the ring, absent from `notices_dropped_total`, and the
client sits in SENT until `sent_timeout_s`. That would be a second, uncounted
loss site, which is precisely what the one-ring rule above exists to prevent.

**One hub per PROCESS, not per service.** `server_epoch` is a process boot id
(§4.3, §6.3): a client that sees it change sweeps all of `sent/` back to
`pending/`. Two hubs in one process hand out two epochs, so each service's
clients would wrongly resend on the other's reconnect. `srv/api.py` owns that
seam: the hub is built once inside `_Machinery`, which is itself built at most
once per process, and every `srv_init()` shares it. No guard lives here on
purpose -- `NotifyHub` is also the unit under test and is constructed freely by
`tests/common/msging/test_notify.py`, so a construction counter would only
force the wiring to grow a bypass. The rule is enforced where the wiring is.

Concurrency: this is asyncio-side code (§6.4) and every method must be called
from the event loop thread -- `publish()` and `drop()` touch an `asyncio.Event`,
whose `set()` is not thread-safe. Cross-thread does not merely race, it fails
*silently*: `Event.set()` -> `Future.set_result` -> `loop.call_soon` from a
foreign thread skips `_write_to_self()`, so a parked stream is not woken until
its next heartbeat. Both methods therefore check and raise; a thread that needs
to publish must go through `loop.call_soon_threadsafe`.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from typing import AsyncIterator

from .. import metrics
from ..config import ServerConfig
from ..log import get_logger
from ..models import NotifyEv

_log = get_logger("msging.srv.notify")

#: SSE comment. Conformant parsers ignore it; its job is to put bytes on the
#: wire so the client's idle_timeout_s can tell a quiet server from a dead
#: link (§4.2).
HEARTBEAT_FRAME = ": hb\n\n"

#: Drop reasons -- the `reason` label on `notices_dropped_total` (§10.5) and
#: the `reason` field on the `notice_dropped` log line (§10.4).
REASON_OVERFLOW = "overflow"
REASON_EXPIRED = "expired"
REASON_DROPPED = "dropped"
#: A notice that never reached a ring because there was no live event loop to
#: hand it to -- see `srv/api.py`'s `send_notice()`.
REASON_NO_LOOP = "no_loop"
#: A notice still over `server.max_notice_bytes` after its app-authored fields
#: were shed -- see `srv/api.py`'s `_bound_notice()`.
REASON_TOO_LARGE = "too_large"


def _epoch_frame(epoch: str) -> str:
    return f"event: epoch\ndata: {json.dumps({'server_epoch': epoch}, separators=(',', ':'))}\n\n"


def _notify_frame(ev: NotifyEv) -> str:
    return f"event: notify\ndata: {ev.model_dump_json()}\n\n"


class _Producer:
    """Per-producer state: the ring, its waiter, and who currently owns it."""

    __slots__ = ("notices", "wake", "stream_id", "subscribers", "touched")

    def __init__(self, maxlen: int) -> None:
        # (enqueued_monotonic, ev). maxlen gives drop-oldest for free, but the
        # eviction it performs is silent -- publish() checks for a full ring
        # first so the drop can be counted and logged.
        self.notices: deque[tuple[float, NotifyEv]] = deque(maxlen=maxlen)
        self.wake = asyncio.Event()
        # Bumped on every subscribe() and on drop(); a generator whose token no
        # longer matches has been superseded and must end.
        self.stream_id = 0
        self.subscribers = 0
        self.touched = time.monotonic()


class NotifyHub:
    """Per-process SSE registry: one boot epoch, one bounded ring per producer.

    Args:
        cfg: server config (§5.2). Reads `heartbeat_s`, `notify_buffer` and
            `notify_ttl_s`.
        service_name: the service this hub logs under (§10.4's server field
            list). One hub may serve several `srv_init()` services in a
            process, so this is the process's primary service name, not a
            per-stream value; `None` when the host has not named one.
    """

    def __init__(self, cfg: ServerConfig, service_name: str | None = None) -> None:
        self._cfg = cfg
        self._service_name = service_name
        # §4.3: the boot id, generated once per process. Not per service and
        # not per stream -- services in one process die together (§6.3).
        self._epoch = uuid.uuid4().hex
        self._producers: dict[str, _Producer] = {}
        self._open = 0
        # Bumped by close_all(); a stream that opened in an earlier generation
        # must not decrement the connection count close_all() already zeroed.
        self._gen = 0
        self._last_sweep = time.monotonic()
        # Bound on first use from the loop thread; see _require_loop_thread().
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def epoch(self) -> str:
        """The `server_epoch` (§4.3). Constant for the life of the process; a
        change is precisely what tells a client the server restarted and that
        `sent/` must be swept back to `pending/`."""
        return self._epoch

    def subscribe(self, producer_id: str) -> AsyncIterator[str]:
        """Open a stream of SSE frames for `producer_id`.

        The first frame is always the epoch event (§4.3) -- a client cannot
        decide whether to sweep until it has seen it, so it precedes even a
        backlog that has been waiting in the ring. Then buffered notices flush
        oldest-first, and thereafter the stream blocks, emitting `: hb` every
        `heartbeat_s` of silence (§4.2).

        The returned iterator ends (raises StopAsyncIteration) when the producer
        is dropped or a newer stream supersedes this one. Supersession is
        claimed here, synchronously, so "last connect wins" means the last call
        to `subscribe()` and not the first stream anyone happened to iterate.

        Not an `async def` on purpose: an async generator's body does not run
        until its first `__anext__`, which would defer that claim.
        """
        st = self._state(producer_id)
        st.stream_id += 1
        now = time.monotonic()
        # A connect is activity, so it protects this producer from the sweep
        # below -- and the sweep must run here too: subscribe/disconnect-only
        # traffic never reaches publish(), which was the only other sweeper.
        st.touched = now
        self._sweep_idle(now)
        return self._stream(producer_id, st, st.stream_id)

    async def _stream(self, producer_id: str, st: _Producer,
                      token: int) -> AsyncIterator[str]:
        # The counters live here, not in subscribe(): a stream the caller never
        # iterates never runs this body and so never runs the finally either --
        # incrementing in subscribe() would pin `subscribers` forever.
        gen = self._gen
        st.subscribers += 1
        self._open += 1
        metrics.gauge("sse_connections", self._open)
        _log.info("sse_opened", producer_id=producer_id,
                  service_name=self._service_name, server_epoch=self._epoch,
                  buffered=len(st.notices))
        try:
            if st.stream_id == token:
                yield _epoch_frame(self._epoch)
            while st.stream_id == token:
                self._expire(st, producer_id)
                if st.notices:
                    _, ev = st.notices[0]
                    yield _notify_frame(ev)
                    # Only now is it delivered. A reader cancelled at the yield
                    # above leaves the notice in the ring for the reconnect
                    # (§4.4/§9.1 make the redundant resend safe); popping first
                    # would lose it silently and uncounted.
                    if st.notices and st.notices[0][1] is ev:
                        st.notices.popleft()
                        metrics.counter("notices_sent_total", code=ev.code)
                        # Never the reason or the info bag: both are
                        # app-authored and may echo user data (§10.4, never log
                        # the payload).
                        _log.info("notice_sent", producer_id=producer_id,
                                  service_name=self._service_name,
                                  msg_id=ev.msg_id, code=ev.code,
                                  server_epoch=self._epoch)
                    # else: overflow or drop() took the head while we were
                    # suspended; it is already counted as a drop, so just
                    # re-drain rather than popping the wrong entry.
                    continue
                # Safe without a re-check after clear(): nothing between the
                # `if st.notices` above and here awaits, and publish() is
                # loop-thread-only (enforced below), so no notice can land in
                # the gap.
                st.wake.clear()
                try:
                    await asyncio.wait_for(st.wake.wait(), self._cfg.heartbeat_s)
                except asyncio.TimeoutError:
                    yield HEARTBEAT_FRAME
        finally:
            st.subscribers -= 1
            if gen == self._gen:
                self._open -= 1
                metrics.gauge("sse_connections", self._open)
            # else: close_all() already zeroed the count for this generation;
            # decrementing again would drive the gauge negative as the
            # superseded generators wind down one by one.
            _log.info("sse_closed", producer_id=producer_id,
                      service_name=self._service_name,
                      server_epoch=self._epoch, buffered=len(st.notices))
            # A disconnect is the moment this producer becomes reclaimable.
            self._sweep_idle(time.monotonic())

    def publish(self, ev: NotifyEv) -> None:
        """Queue `ev` for its producer, waking a live stream if there is one.

        Non-blocking and never fails: the ring absorbs the notice whether or not
        anyone is connected (§6.2). A full ring drops its oldest entry, counted
        as `notices_dropped_total{reason="overflow"}`; the client's
        `sent_timeout_s` is what recovers a dropped outcome (§4.4).

        Raises:
            RuntimeError: called from outside the event loop thread (§6.4).
                Not a failure mode of the notice -- a caller bug, and the only
                thing this method ever raises.
        """
        self._require_loop_thread("publish")
        st = self._state(ev.producer_id)
        now = time.monotonic()
        self._expire(st, ev.producer_id, now=now)

        maxlen = st.notices.maxlen
        if maxlen == 0:
            # notify_buffer=0 means hold nothing; the notice itself is the drop.
            self._count_drop(ev.producer_id, ev, REASON_OVERFLOW)
            return
        if maxlen is not None and len(st.notices) >= maxlen:
            self._count_drop(ev.producer_id, st.notices[0][1], REASON_OVERFLOW)

        st.notices.append((now, ev))
        st.touched = now
        st.wake.set()
        self._sweep_idle(now)

    def drop(self, producer_id: str) -> None:
        """Forget a producer: discard its ring and end its live stream.

        For a producer that has closed (§2.4) or been deregistered -- holding
        outcomes for a binding that will never reconnect only wastes memory.
        Discarded notices count as `notices_dropped_total{reason="dropped"}`.
        A no-op for a producer the hub has never seen.

        Raises:
            RuntimeError: called from outside the event loop thread (§6.4).
        """
        self._require_loop_thread("drop")
        st = self._producers.pop(producer_id, None)
        if st is None:
            return
        while st.notices:
            _, ev = st.notices.popleft()
            self._count_drop(producer_id, ev, REASON_DROPPED)
        # Supersede any live stream and wake it so it notices immediately.
        st.stream_id += 1
        st.wake.set()

    def close_all(self) -> None:
        """Drop every producer: end every live stream, discard every ring.

        §6.2 makes `srv_stop()` a drain of "worker pool, SSE registry"; this is
        the registry half. Without it a host that embeds the library in a
        longer-lived process (§6.3) keeps every per-producer ring, every
        superseded generator and a non-zero `sse_connections` across a
        stop/start cycle -- the hub is per-process state, and a stop that only
        drains the pool leaves half the process still running.

        Every discarded notice is counted as
        `notices_dropped_total{reason="dropped"}`, the same as a single
        `drop()`: a stop loses outcomes, and the loss must be visible.
        `sse_connections` is zeroed here rather than left to the generators'
        `finally`, because after a stop nobody is iterating them and the gauge
        would otherwise stay pinned at its last live value forever.

        Idempotent. Safe on a hub that never saw a producer.

        Raises:
            RuntimeError: called from outside the event loop thread (§6.4).
        """
        self._require_loop_thread("close_all")
        for producer_id in list(self._producers):
            self.drop(producer_id)
        self._gen += 1
        self._open = 0
        metrics.gauge("sse_connections", 0)

    def count_drop(self, ev: NotifyEv, reason: str) -> None:
        """Record a notice lost before it ever reached a ring.

        Metrics and logging only -- no asyncio state -- so unlike every other
        method here it is safe from any thread, which is what its one caller
        (`srv/api.py`'s cross-thread `send_notice()`) needs. It exists so that
        `notices_dropped_total` stays the single place a lost outcome is
        counted; see the one-ring note in the module docstring.
        """
        self._count_drop(ev.producer_id, ev, reason)

    def buffered(self, producer_id: str) -> int:
        """Notices held for `producer_id` right now.

        Expires aged entries first, so the count is what a reconnect would
        actually be handed -- not what is nominally still in the deque.
        """
        st = self._producers.get(producer_id)
        if st is None:
            return 0
        self._expire(st, producer_id)
        return len(st.notices)

    def pending_notices(self) -> int:
        """Notices held across every producer that still has a live stream.

        What `srv_shutdown()`'s drain window waits on (§6.2): a ring belonging
        to a producer nobody is connected to cannot flush however long we wait,
        so counting it would spend the whole `shutdown_drain_s` every time a
        disconnected producer had a backlog. Expiry is *not* forced here --
        the drain must not be the thing that ages notices out from under a
        stream that is about to receive them.
        """
        return sum(len(st.notices) for st in self._producers.values()
                   if st.subscribers)

    def tracked(self) -> int:
        """How many producers the hub currently holds state for.

        Observability seam: each ring is bounded by `notify_buffer`, but the
        number of producer_ids a server sees is not, so this is the number that
        actually has to stay bounded.
        """
        return len(self._producers)

    # ------------------------------------------------------------ internals

    def _require_loop_thread(self, method: str) -> None:
        """Reject a call made from any thread but the loop's (§6.4).

        Cross-thread is not merely racy, it is silent: `Event.set()` ->
        `Future.set_result` -> `loop.call_soon` from a foreign thread skips
        `_write_to_self()`, so a parked stream is not woken until its next
        heartbeat and nothing reports the stall. A loud failure at the call
        site is the only way the caller learns to hand off properly.

        The loop is bound on first use rather than at construction so the hub
        can be built before the server starts.
        """
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._loop is None and running is not None:
            self._loop = running
        if running is None or (self._loop is not None and running is not self._loop):
            raise RuntimeError(
                f"NotifyHub.{method}() must be called from the event loop thread "
                "(§6.4). From another thread use "
                "loop.call_soon_threadsafe(hub." + method + ", ...)."
            )

    def _state(self, producer_id: str) -> _Producer:
        st = self._producers.get(producer_id)
        if st is None:
            st = _Producer(self._cfg.notify_buffer)
            self._producers[producer_id] = st
        return st

    def _expire(self, st: _Producer, producer_id: str,
                now: float | None = None) -> None:
        """Drop ring entries older than `notify_ttl_s` (§6.2).

        Lazy rather than timer-driven: the ring is ordered by arrival, so the
        aged entries are always a prefix, and every path that can observe a
        notice (drain, publish, buffered) expires first. A stale outcome is
        worse than none -- the client has long since resent (§4.4).
        """
        ttl = self._cfg.notify_ttl_s
        if now is None:
            now = time.monotonic()
        while st.notices and now - st.notices[0][0] >= ttl:
            _, ev = st.notices.popleft()
            self._count_drop(producer_id, ev, REASON_EXPIRED)

    def _count_drop(self, producer_id: str, ev: NotifyEv, reason: str) -> None:
        metrics.counter("notices_dropped_total", reason=reason)
        # Loud by design (§10.4): a dropped notice means a client will sit in
        # SENT until sent_timeout_s demotes it.
        _log.warning("notice_dropped", producer_id=producer_id, msg_id=ev.msg_id,
                     service_name=self._service_name, code=ev.code,
                     reason=reason, server_epoch=self._epoch)

    def _sweep_idle(self, now: float) -> None:
        """Reclaim state for producers that are idle, empty and unsubscribed.

        Without this, a server that publishes for many short-lived producer_ids
        accumulates one `_Producer` per id for the life of the process. Throttled
        to one pass per `notify_ttl_s` so a hot publish path stays O(1)
        amortized; memory is therefore bounded by the producers seen within one
        TTL window, not by the process's whole history.
        """
        ttl = self._cfg.notify_ttl_s
        if now - self._last_sweep < ttl:
            return
        self._last_sweep = now
        for producer_id, st in list(self._producers.items()):
            if st.subscribers:
                continue
            self._expire(st, producer_id, now=now)
            if not st.notices and now - st.touched >= ttl:
                del self._producers[producer_id]
