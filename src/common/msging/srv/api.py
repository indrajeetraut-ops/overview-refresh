"""Process-level server machinery: the state the routes run against (§6.2).

`router.py` owns HTTP; this module owns everything HTTP is a skin over -- the
producer store, `Auth`, the `NotifyHub`, the `InboundQueue`, and the two app
callbacks. §6.2 is explicit that `srv_start()`/`srv_stop()` "boot and drain the
shared machinery ... **once per process**", so all of it lives here as module
state rather than being handed out per `srv_init()` call.

**One hub per process, not per service.** `server_epoch` is a process boot id
(§4.3, §6.3): a client that sees it change sweeps all of `sent/` back to
`pending/` and resends. Two hubs in one process would hand out two epochs, so
each service's clients would wrongly resend everything the first time the
*other* service's stream reconnected. `_Machinery` is therefore built at most
once per process and every `srv_init()` shares it -- which also settles the
apparent §4.3/§6.2 disagreement over whether `srv_init()` or `srv_start()`
mints the epoch: the hub does, on first construction, and both orders give the
same single value.

**Everything else is shared for the same reason.** One queue, because
`queue_max` is a bound on the *process's* memory and two services sharing a
worker pool is exactly §6.3's "services in one process die together". One
`Auth`/store, because a producer_id is issued by the process, not by a path
prefix. The only per-service thing in this library is the mount prefix.

Concurrency (§6.4): the routes are `async def`, so every call into the hub and
the queue happens on the event loop thread, which is what `NotifyHub` requires.
`send_notice()` is the one exception -- it is app-facing and an app is allowed
to be threaded, so it bridges; see its docstring.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import signal
import threading
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

from ..config import MsgingConfig, load_config
from ..errors import ConfigError
from ..log import get_logger
from ..models import Envelope, NotifyEv
from .auth import Auth
from .notify import REASON_NO_LOOP, REASON_TOO_LARGE, NotifyHub
from .producer_store import make_store
# _raised_at, not a second copy of it: "location, never the message" is one
# rule (§10.4) and must have one implementation. Package-internal by design.
from .queue import InboundQueue, _raised_at

_log = get_logger("msging.srv.api")

#: Where `srv_init()` reads config from when the host does not pass one.
CONFIG_ENV = "TALIUM_MSGING_CONFIG"

#: §10.1's two modes. A third value is a config typo, not a feature.
REGISTRATION_MODES = ("open", "enrollment_key")

#: Signals `srv_start()` chains `srv_shutdown()` onto, when the host has a
#: handler of its own for them (§6.3). SIGINT and SIGTERM are the two an ASGI
#: server installs and the two a container runtime sends.
SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM)

EventHandler = Callable[[Envelope], Awaitable[None] | None]
CloseHandler = Callable[[str], Awaitable[None] | None]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _RegisterRate:
    """A fixed-window request counter per source, for `registration.rate_per_min`.

    A window, not a token bucket: `/register` is a once-per-producer-lifetime
    call, so the shape that matters is "how many in the last minute", and a
    counter that resets is both cheaper and easier to reason about than a
    refilling bucket.

    The source key is whatever the HTTP layer can see. Behind a reverse proxy
    that is the proxy, which makes this a global cap rather than a per-caller
    one -- and that is still the right bound, because the failure it exists to
    stop (a loop of registrations) is the same shape either way. It is not a
    security control; `enrollment_key` (§10.1) is.

    Bounded by construction: the whole map is dropped at each window roll, so
    the memory is the number of distinct sources seen within one minute rather
    than the process's whole history -- the cap must not itself be the leak.
    """

    __slots__ = ("_window", "_started", "_counts", "_lock")

    def __init__(self, window_s: float = 60.0) -> None:
        self._window = window_s
        self._started = time.monotonic()
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def allow(self, source: str, limit: int) -> bool:
        """True if `source` may register now. `limit` <= 0 means unlimited."""
        if limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            if now - self._started >= self._window:
                self._started = now
                self._counts = {}
            seen = self._counts.get(source, 0)
            if seen >= limit:
                return False
            self._counts[source] = seen + 1
            return True


class _Machinery:
    """The per-process server state. Built once; see the module docstring."""

    def __init__(self, cfg: MsgingConfig, service_name: str | None = None) -> None:
        _check_registration(cfg)
        self.cfg = cfg
        self.store = make_store(cfg.producer_store)
        self.auth = Auth(self.store, cache_max=cfg.server.auth_cache_max,
                         max_producers=cfg.server.registration.max_producers)
        self.register_rate = _RegisterRate()
        # The hub mints server_epoch here, once. `service_name` is the
        # process's primary service (the first one mounted) and is a log field
        # only -- the epoch and the rings are not scoped by it.
        self.hub = NotifyHub(cfg.server, service_name)
        self.queue = InboundQueue.from_config(cfg.server)
        self.services: list[str] = []
        self.ev_handler: EventHandler | None = None
        self.close_handler: CloseHandler | None = None
        # Bound at srv_start(); send_notice() bridges onto it from other threads.
        self.loop: asyncio.AbstractEventLoop | None = None
        # Distinguishes "never started" from "started and since stopped", which
        # send_notice() needs to report the right thing -- both look like
        # `loop is None`, but only one of them is a wiring mistake.
        self.ever_started = False
        # Queues retired by _testing.set_queue_max_for_tests(); srv_stop() reaps
        # them.
        self.retired: list[InboundQueue] = []
        self._warned_no_handler = False
        # §6.2's shutdown phase. A plain bool, read by the routes on the loop
        # thread and set by `srv_shutdown()` from anywhere (including a signal
        # handler), which is why it must not be an asyncio primitive.
        self.shutting_down = False
        # The task doing the drain, so a second srv_shutdown() joins the first
        # rather than starting a second drain over the same machinery.
        self.shutdown_task: asyncio.Task | None = None
        # (signum, previous handler, ours) installed by srv_start(), restored by
        # srv_stop(). Empty when the host drives shutdown itself.
        self.signal_handlers: list[tuple[int, object, object]] = []


_state: _Machinery | None = None

#: Handlers registered before anything has built the machinery, held until
#: something does.
#:
#: §6.2's documented wiring order is `on_event(handle)` first, then
#: `srv_init()`/`make_app()` -- and `srv_init(config=...)` is the *only*
#: carrier of an explicit config. So `on_event()` must not be what constructs
#: the machinery: building it out of `_default_config()` consumed the "first
#: call" the host's own config was meant to arrive on, and `init_service` then
#: refused that config as a conflict with defaults nobody chose. The error even
#: said the config was "already set", which was true and useless -- the host
#: had never set one. A host that reordered its calls to dodge the exception
#: silently ran on the default `max_msg_bytes`, the default `queue_max` and a
#: `~/.talium` producer store.
#:
#: Parking the handler here instead costs one indirection and makes the
#: documented order work with a config object. The conflict check is untouched:
#: two genuinely different configs still raise.
_pending_ev_handler: EventHandler | None = None
_pending_close_handler: CloseHandler | None = None


def _build(cfg: MsgingConfig, service_name: str | None = None) -> _Machinery:
    """Construct the process machinery and adopt any parked handlers.

    Every construction site goes through this -- `machinery()`, `init_service()`
    and the `_testing` reset -- so a handler registered before the machinery
    existed can never be silently dropped, and a leftover parked handler can
    never survive into a second machinery.
    """
    global _pending_ev_handler, _pending_close_handler
    st = _Machinery(cfg, service_name)
    st.ev_handler = _pending_ev_handler
    st.close_handler = _pending_close_handler
    _pending_ev_handler = None
    _pending_close_handler = None
    return st


def _check_registration(cfg: MsgingConfig) -> None:
    """§10.1: `mode` is `open | enrollment_key`, and `enrollment_key` requires
    `key`. Checked at wiring time on purpose -- the failure mode of a silently
    ignored `enrollment_key` stanza is a server that issues api_keys to anyone
    who can reach the port, which is precisely what the mode exists to stop."""
    reg = cfg.server.registration
    if reg.mode not in REGISTRATION_MODES:
        raise ConfigError(
            f"server.registration.mode must be one of {REGISTRATION_MODES}, "
            f"got {reg.mode!r} (§10.1)"
        )
    if reg.mode == "enrollment_key" and not reg.key:
        raise ConfigError(
            "server.registration.mode=enrollment_key requires "
            "server.registration.key (§10.1)"
        )


def _default_config() -> MsgingConfig:
    path = os.environ.get(CONFIG_ENV)
    return load_config(path) if path else MsgingConfig()


def machinery() -> _Machinery:
    """The process's server state, built on first use.

    Internal to the `srv` package -- routes and lifecycle call it; app code
    goes through the public functions below.
    """
    global _state
    if _state is None:
        _state = _build(_default_config())
    return _state


def init_service(service_name: str, config: MsgingConfig | None = None) -> _Machinery:
    """Register `service_name` against the process machinery (§6.2 step 1).

    `config` may be supplied on the first call only; the machinery is
    process-wide, so a second, different config would silently apply to nothing.
    Raises RuntimeError rather than letting that pass.

    "First call" means the first call that *builds* the machinery, and
    registering a handler is deliberately not one of them -- see
    `_pending_ev_handler`. §6.2's own order (`on_event`, then this) therefore
    carries a config, and only two genuinely different configs conflict.
    """
    global _state
    if _state is None:
        _state = _build(config or _default_config(), service_name)
    elif config is not None and config is not _state.cfg:
        raise RuntimeError(
            "msging server config is process-wide and already set; pass it on "
            "the first srv_init() call only (§6.2)"
        )
    if service_name in _state.services:
        raise RuntimeError(
            f"service {service_name!r} is already mounted in this process; "
            "including its router twice would duplicate every route"
        )
    _state.services.append(service_name)
    return _state


# ------------------------------------------------------------- lifecycle


async def srv_start() -> None:
    """Boot the shared machinery: bind the loop and start the worker pool.

    Idempotence is deliberately *not* offered -- `InboundQueue.start()` raises
    on a second call, and a host that starts twice has two lifespans wired to
    one process, which is a bug worth surfacing.
    """
    st = machinery()
    st.loop = asyncio.get_running_loop()
    st.ever_started = True
    st.shutting_down = False
    st.shutdown_task = None
    _install_signal_handlers(st)
    # Bound to *this* machinery rather than to the module-level lookup: if the
    # process state is ever replaced while a pool is running, the orphaned
    # workers must keep dispatching into the state they were started for
    # instead of silently routing another machinery's handler.
    st.queue.start(lambda env: _dispatch(st, env))
    _log.info("srv_started", services=list(st.services),
              server_epoch=st.hub.epoch, workers=st.cfg.server.workers,
              queue_max=st.cfg.server.queue_max)


async def srv_stop() -> None:
    """Drain the shared machinery -- worker pool *and* SSE registry (§6.2).

    Both halves, because both are per-process state: a stop that only reaped
    the pool left every per-producer ring, every superseded /notify generator
    and the `sse_connections` gauge alive, which under §6.3's embed-in-a-
    longer-lived-process case is a leak that grows with every stop/start cycle.

    Safe on a process that never started, and the machinery survives: the hub
    is not rebuilt, so `server_epoch` stays the process boot id it was (§4.3)
    across a `srv_stop()`/`srv_start()` cycle. A new epoch would tell every
    client the *server* restarted and make each of them sweep `sent/` back to
    `pending/` and resend, which is not what a host reloading its own lifespan
    means.
    """
    st = _state
    if st is None:
        return
    _restore_signal_handlers(st)
    # A shutdown already in flight (a signal arrived, §6.3) is joined rather
    # than raced: it is draining the same pool and closing the same streams, and
    # two drains over one machinery would have the second one reaping workers
    # the first is still awaiting.
    task = st.shutdown_task
    if task is not None and not task.done():
        # wait(), not await: `_drain` absorbs its own failures and this must not
        # turn one into a lifespan that never finished draining the pool.
        await asyncio.wait([task])
    for queue in st.retired:
        await queue.stop()
    st.retired.clear()
    await st.queue.stop()
    st.hub.close_all()
    st.loop = None
    st.shutdown_task = None
    _log.info("srv_stopped", services=list(st.services),
              server_epoch=st.hub.epoch)


# ------------------------------------------------------------- shutdown


def srv_shutdown() -> None:
    """Begin shutdown: stop accepting, drain the workers, end every stream.

    **This is the call that makes an ASGI shutdown possible at all** (§6.2,
    §6.3). `srv_stop()` runs from the lifespan, and ASGI runs lifespan shutdown
    only *after* the server has drained its connections -- but `/notify` is an
    endless stream by design (§4.1), so it never drains, so `srv_stop()` never
    runs. A server with one producer connected could not be shut down: SIGTERM
    and SIGINT both hung until the deploy's SIGKILL, and every msg in the
    in-memory queue died with it. The stream has to be ended *before* the drain,
    which means from the shutdown signal rather than from the lifespan.

    Sync, idempotent, and safe from any thread and from a signal handler -- the
    handler is where it is meant to be called. It returns immediately; the drain
    it starts runs on the event loop, in the order §6.2 requires:

        1. stop accepting -- /register, /event and /notify answer 503
           server_busy + Retry-After (§8.1). The client keeps the msg PENDING
           and retries, which is exactly what a rolling restart wants.
        2. drain the worker pool, so in-flight msgs finish and their outcomes
           reach the rings.
        3. wait up to `server.shutdown_drain_s` for the live streams to flush
           those rings, so a producer that is still connected actually receives
           the outcomes rather than waiting out `sent_timeout_s`.
        4. close every stream. The connections now drain, the ASGI server runs
           lifespan shutdown, and `srv_stop()` completes the stop.

    A host that installs its own signal handling calls this from it, then lets
    its lifespan call `srv_stop()` as usual. A host that does not gets it wired
    automatically: `srv_start()` chains this onto whatever SIGINT/SIGTERM
    handler is already installed (uvicorn's, normally), unless
    `server.shutdown_signals` is false.

    A no-op before `srv_start()` or after `srv_stop()` -- there is no loop to
    drain on, and nothing to end.
    """
    st = _state
    if st is None or st.shutting_down:
        return
    loop = st.loop
    if loop is None:
        return
    # Set before the hand-off, not inside it: the routes read this flag, and a
    # request accepted between the signal and the loop picking up the callback
    # would be accepted into a pool that is about to stop.
    st.shutting_down = True
    _log.info("srv_shutdown_begin", services=list(st.services),
              server_epoch=st.hub.epoch)
    try:
        loop.call_soon_threadsafe(_start_shutdown, st)
    except RuntimeError:
        # Loop already closed. Nothing left to drain and nothing to end; the
        # flag stays set so anything still routing sees a shutting-down server.
        _log.warning("srv_shutdown_no_loop", services=list(st.services))


def _start_shutdown(st: _Machinery) -> None:
    """Create the drain task. Runs on the loop thread, via `call_soon_threadsafe`."""
    if st.shutdown_task is None or st.shutdown_task.done():
        st.shutdown_task = asyncio.create_task(_drain(st), name="msging-shutdown")


async def _drain(st: _Machinery) -> None:
    """Steps 2-4 of `srv_shutdown()`. Never raises -- it runs unsupervised."""
    try:
        await st.queue.stop()
        await _flush_notices(st, st.cfg.server.shutdown_drain_s)
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                    # noqa: BLE001
        _log.error("srv_shutdown_error", error=type(exc).__name__,
                   error_at=_raised_at(exc))
    finally:
        # In the finally, because the streams have to end even if the drain
        # above failed: they are what is holding the ASGI shutdown open.
        st.hub.close_all()
        _log.info("srv_shutdown_complete", services=list(st.services),
                  server_epoch=st.hub.epoch)


async def _flush_notices(st: _Machinery, deadline_s: float) -> None:
    """Give live streams up to `deadline_s` to empty their rings.

    Polled rather than event-driven: `NotifyHub` deliberately has no "ring
    empty" signal -- publish/drain is one-way by design (see notify.py's one-ring
    note) -- and adding one for a path that runs once per process would put a
    callback on the hot publish path to save a few wake-ups at shutdown.

    Returns as soon as nothing is buffered, so the common case (no backlog)
    costs one poll and not `shutdown_drain_s`.
    """
    if deadline_s <= 0:
        return
    end = time.monotonic() + deadline_s
    while True:
        if not st.hub.pending_notices():
            return
        if time.monotonic() >= end:
            _log.warning("srv_shutdown_drain_timeout",
                         buffered=st.hub.pending_notices(),
                         timeout_s=deadline_s)
            return
        await asyncio.sleep(0.02)


def _install_signal_handlers(st: _Machinery) -> None:
    """Chain `srv_shutdown()` onto the host's SIGINT/SIGTERM handlers (§6.3).

    **Chained, never replacing.** The previous handler is called immediately
    afterwards, so uvicorn still sets `should_exit`, a host's own handler still
    runs, and Ctrl-C under a plain interpreter still raises KeyboardInterrupt.
    The library only inserts "end my streams first" ahead of whatever was
    already going to happen.

    **Only when a callable handler is already installed**, which is the whole
    justification for a library touching signals at all. A handler exists
    exactly when something intends a *graceful* shutdown -- and a graceful
    shutdown is precisely what `/notify` deadlocks. With `SIG_DFL` there is no
    deadlock to fix: the process dies on the signal. Installing there would
    replace "terminate" with "trip a flag and keep running", i.e. it would
    create the hang rather than fix it.

    `signal.signal`, not `loop.add_signal_handler`: the latter installs its own
    C-level handler and would clobber uvicorn's, so the server would never learn
    the signal arrived. Main thread only -- `signal.signal` raises anywhere else,
    and a server on a worker thread has a host that owns its own shutdown.
    """
    if not st.cfg.server.shutdown_signals:
        return
    if threading.current_thread() is not threading.main_thread():
        return
    for signum in SHUTDOWN_SIGNALS:
        try:
            previous = signal.getsignal(signum)
        except (ValueError, OSError):                           # pragma: no cover
            continue
        if not callable(previous):
            continue

        def _handler(sig: int, frame, _prev=previous) -> None:
            srv_shutdown()
            _prev(sig, frame)

        try:
            signal.signal(signum, _handler)
        except (ValueError, OSError):                           # pragma: no cover
            continue
        st.signal_handlers.append((signum, previous, _handler))


def _restore_signal_handlers(st: _Machinery) -> None:
    """Put back what `_install_signal_handlers` displaced.

    Only where the handler is still, by identity, the one we installed: a host
    that set its own after `srv_start()` meant it, and overwriting that on the
    way out would leave the process with a handler nobody asked for.
    """
    for signum, previous, installed in st.signal_handlers:
        try:
            if signal.getsignal(signum) is installed:
                signal.signal(signum, previous)
        except (ValueError, OSError):                           # pragma: no cover
            pass
    st.signal_handlers.clear()


# ------------------------------------------------------------- callbacks


def on_event(fn: EventHandler) -> EventHandler:
    """Register the app handler (§6.2 step 3). Returns `fn`, so it also works
    as a decorator. May be called before or after `srv_start()`: the pool runs
    a trampoline that reads this slot per msg.

    **Does not build the machinery.** Called before `srv_init()` -- which is
    §6.2's documented order -- the handler is parked until whatever builds the
    machinery adopts it, so `srv_init(config=...)` is still the first call and
    the host's config still applies. See `_pending_ev_handler`.
    """
    global _pending_ev_handler
    if _state is None:
        _pending_ev_handler = fn
    else:
        _state.ev_handler = fn
    return fn


def on_close(fn: CloseHandler) -> CloseHandler:
    """Register the optional close hook (§6.2 step 4, §2.4). Parked rather than
    machinery-building before `srv_init()`, exactly like `on_event()`."""
    global _pending_close_handler
    if _state is None:
        _pending_close_handler = fn
    else:
        _state.close_handler = fn
    return fn


async def _dispatch(st: _Machinery, env: Envelope) -> None:
    """The worker pool's handler. Indirection is the point: `on_event()` may
    land after `srv_start()`, and the pool must pick it up without a restart.

    `st` is bound at `srv_start()` rather than looked up per msg, so a pool
    always dispatches into the machinery it was started for.

    A msg that arrives with no handler registered is dropped, loudly and once
    -- once because the alternative is a warning per msg, which buries the
    problem it is reporting.
    """
    fn = st.ev_handler
    if fn is None:
        if not st._warned_no_handler:
            st._warned_no_handler = True
            _log.warning("no_event_handler", service_name=env.service_name,
                         producer_id=env.producer_id, msg_id=env.msg_id)
        return
    result = fn(env)
    if inspect.isawaitable(result):
        await result


def send_notice(producer_id: str, msg_id: int, code: str,
                reason: str | None = None, info: dict | None = None,
                event_id: str | None = None) -> None:
    """Send the outcome for one msg over its producer's SSE channel (§6.2
    step 5, §8.2). SSE down -> held in the per-producer ring and delivered on
    reconnect; overflow or expiry drops, and `sent_timeout_s` resends (§4.4).

    Args:
        producer_id: the binding the msg came from.
        msg_id: the transport key being answered (§7).
        code: a §8/`Code` value -- `ack`, `nack`, or an app extension.
        reason: free-form, human-only. Code must never branch on it (§8).
        info: app bag echoed to the client verbatim -- a row id, a session id.
            Bounded: one serialised notify_ev must fit `max_notice_bytes`, or
            the client's frame parser drops the link on every delivery attempt.
            An oversized bag is shed loudly rather than wedging that producer's
            outcome channel; see `_bound_notice`.
        event_id: echoed back when the msg carried one (§8.2). Not in §6.2's
            signature; without it the client cannot correlate by its own id.

    **Thread-safety: this is the one entry point that may be called off the
    event loop thread.** `NotifyHub.publish()` raises there, on purpose --
    `asyncio.Event.set()` from a foreign thread fails *silently*, leaving a
    parked stream unwoken until its next heartbeat. But `send_notice()` is
    app-facing, and §6.2's handler contract says nothing about which thread the
    app answers from: a handler that offloads to an executor and reports the
    outcome from there is normal, correct app code. So this wrapper does the
    handoff `notify.py` prescribes -- `loop.call_soon_threadsafe` -- rather
    than making every threaded app rediscover it. The strict rule stays where
    it belongs, on the primitive.

    Fire-and-forget on the loop thread: `publish()` never fails. The
    cross-thread hand-off is *not* equally infallible, and this is the one
    place that has to say so -- a loop that has been closed makes
    `call_soon_threadsafe` raise `RuntimeError`, and a loop that is bound but
    no longer *running* accepts the callback and never runs it. The second is
    the dangerous one: it looks like a success and loses the outcome in
    silence. Both are therefore detected and counted as
    `notices_dropped_total{reason="no_loop"}` rather than left as an uncounted
    loss site -- notify.py's one-ring rule exists precisely to keep the number
    of places a notice can vanish at one.

    Raises:
        RuntimeError: called before `srv_start()`, or after `srv_stop()`. There
            is no loop to publish on, and silently dropping the outcome would
            strand the client in SENT until `sent_timeout_s`. The message
            distinguishes the two -- one is a wiring mistake, the other is a
            shutdown race.
    """
    st = machinery()
    loop = st.loop
    if loop is None:
        raise RuntimeError(
            ("send_notice() after srv_stop(): the event loop is no longer "
             "bound, so there is nothing to deliver on (§6.2)")
            if st.ever_started else
            ("send_notice() before srv_start(): no event loop to deliver on "
             "(§6.2)")
        )
    ev = NotifyEv(msg_id=msg_id, producer_id=producer_id, event_id=event_id,
                  code=str(code), reason=reason or "", info=info or {},
                  ts=utc_now_iso())
    if not _bound_notice(st, ev):
        return
    try:
        running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        st.hub.publish(ev)
        return
    if loop.is_closed() or not loop.is_running():
        # Bound but not turning. call_soon_threadsafe() would either raise or
        # queue a callback nobody will ever run; either way the notice is lost.
        st.hub.count_drop(ev, REASON_NO_LOOP)
        return
    try:
        loop.call_soon_threadsafe(st.hub.publish, ev)
    except RuntimeError:
        # Closed between the check above and here.
        st.hub.count_drop(ev, REASON_NO_LOOP)


#: How much of `reason` survives when a notice has to be shed, in characters.
#: Human-only text (§8), so a prefix still reads; long enough to be useful, and
#: short enough that -- even all-multibyte and JSON-escaped -- it can never be
#: the field that is over budget.
_SHED_REASON_CHARS = 512


def _bound_notice(st: _Machinery, ev: NotifyEv) -> bool:
    """Keep one notify_ev under `server.max_notice_bytes`. True to let it go.

    The client's SSE parser caps one event at 64 KiB and a frame over that is
    not resynchronisable -- it drops the link. Nothing bounded `info`, so an app
    handing back an oversized bag put that producer's outcome channel into a
    *permanent* reconnect loop: every delivery attempt of the same notice tore
    the stream down again, and the only escape was `sent_timeout_s`. One app bug
    became one wedged client. The bound belongs at the source.

    Shed rather than dropped, because `code` is what resolves the msg: throwing
    the whole outcome away to save the bag strands the msg the outcome answers,
    which is the more expensive of the two failures. `info` goes first and
    `reason` is truncated -- both are app-authored decoration (§8). `code` and
    `event_id` are the client's correlation keys and are never touched, so a
    notice still over budget after shedding is dropped and counted as
    `notices_dropped_total{reason="too_large"}` rather than lost silently.

    Loud either way: nothing here is reachable without an app bug, and the log
    line carries sizes only -- never the bag, the reason or the code (§10.4).
    """
    limit = st.cfg.server.max_notice_bytes
    size = len(ev.model_dump_json().encode())
    if size <= limit:
        return True
    shed_info, shed_reason = bool(ev.info), len(ev.reason) > _SHED_REASON_CHARS
    ev.info = {}
    ev.reason = ev.reason[:_SHED_REASON_CHARS]
    shed_size = len(ev.model_dump_json().encode())
    _log.warning("notice_oversized", producer_id=ev.producer_id,
                 msg_id=ev.msg_id, size_bytes=size, max_bytes=limit,
                 shed_size_bytes=shed_size, shed_info=shed_info,
                 shed_reason=shed_reason)
    if shed_size > limit:
        st.hub.count_drop(ev, REASON_TOO_LARGE)
        return False
    return True


async def fire_close(st: _Machinery, producer_id: str) -> None:
    """Run the app's `on_close` hook, absorbing whatever it throws.

    `/close` is a courtesy call (§2.4) and the client has already drained; a
    hook that raises must not turn that into a 5xx. Type and location only --
    an app-authored message may carry the payload or a key (§10.4).
    """
    fn = st.close_handler
    if fn is None:
        return
    try:
        result = fn(producer_id)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:                                    # noqa: BLE001
        _log.error("close_handler_error", producer_id=producer_id,
                   error=type(exc).__name__, error_at=_raised_at(exc))
