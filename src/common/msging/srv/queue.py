"""Inbound queue + worker pool -- the server's async seam (design §6.2).

    POST -> enqueue -> 202          # request handler returns immediately
    worker -> ev_callback_fn(msg)   # app processes later

The app handler never runs inside the request handler: running it inline would
make the outcome known at POST time and leave the SSE channel nothing to carry
(§6.2). The queue is in memory and may be lost on restart -- that is covered by
`server_epoch` on reconnect (§4.3) and by `sent_timeout_s` as the backstop
(§4.4), so nothing here is durable by design.

The queue is capped at `queue_max` (§5.2) and never grows: a full queue makes
`offer()` return False, and the route turns that into `503 server_busy` +
`Retry-After` (§8.1). A stopped queue rejects the same way -- accepting there
would answer 202 for work nobody will ever process. Acceptance *is* enqueueing,
so this module owns both the `msg_accepted`/`queue_full` log lines and the
`msgs_accepted_total` / `queue_depth` / `queue_full_total` metrics (§10.4,
§10.5) -- the route must not count them a second time.

`queue_max` counts what is *queued*. A msg a worker has picked up is in flight
and no longer queued, so accepted-but-unprocessed peaks at `queue_max +
workers` -- design §6.2 states this outright; `depth()` reports the queued
count and nothing else.

A sync handler runs directly on the event loop rather than being offloaded to a
thread: the same rule as the client's notify callback (§6.4) applies -- a
handler must be quick, because a slow one stalls every msg behind it. An app
that needs to block should hand the work to its own executor.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Awaitable, Callable

from .. import metrics
from ..codes import Code
from ..config import ServerConfig
from ..log import get_logger
from ..models import Envelope

Handler = Callable[[Envelope], Awaitable[None] | None]

_log = get_logger("msging.srv.queue")

#: Grace period for stop() to drain accepted-but-unprocessed work before the
#: workers are cancelled outright.
DEFAULT_DRAIN_TIMEOUT_S = 10.0

#: Fallback worker count when neither `start()` nor config supplies one (§5.2).
DEFAULT_WORKERS = 4


def _raised_at(exc: BaseException) -> str:
    """`file:line` of the frame that raised -- the location, never the message.

    §10.4 is normative: never log the payload. A handler's exception message is
    app-authored and routinely interpolates the very thing that broke -- the
    payload, an api_key, a pydantic ValidationError echoing the input -- so
    `str(exc)` can never be logged. The raising frame is what debugging
    actually needs, and it carries no user data.
    """
    tb = exc.__traceback__
    if tb is None:
        return "unknown"
    while tb.tb_next is not None:
        tb = tb.tb_next
    return f"{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}"


class InboundQueue:
    """Bounded in-memory queue drained by a pool of asyncio worker tasks."""

    def __init__(self, maxsize: int, workers: int = DEFAULT_WORKERS) -> None:
        self._q: asyncio.Queue[Envelope] = asyncio.Queue(maxsize=maxsize)
        self._handler: Handler | None = None
        self._workers: list[asyncio.Task] = []
        self._default_workers = workers
        # A queue that has been stopped must reject, not silently swallow.
        self._closed = False

    @classmethod
    def from_config(cls, cfg: ServerConfig) -> "InboundQueue":
        """`queue_max` is the queued-and-not-yet-picked-up cap and `workers`
        the handler concurrency (§5.2)."""
        return cls(maxsize=cfg.queue_max, workers=cfg.workers)

    def offer(self, env: Envelope) -> bool:
        """Accept `env` for later processing. False when the queue is full or
        the queue has been stopped.

        Non-blocking and callable from the request handler: a False return is
        the caller's cue to answer `503 server_busy` with `Retry-After`.

        Rejecting after `stop()` is the point of the closed state. Accepting
        there would answer `202 ok`, the client would mark the msg SENT, and no
        outcome would ever arrive -- recovered only by the slow `sent_timeout_s`
        backstop (§4.4). A 503 instead keeps the msg PENDING and the client
        retries after `Retry-After` (§8.1). The rejection counts as
        `queue_full_total`: §10.5's name list is closed, and that counter is
        the server's tally of 503-server_busy rejections.
        """
        if self._closed:
            metrics.counter("queue_full_total")
            # Loud by design (§10.4): 503s are going out.
            _log.warning("queue_closed", queue_depth=self._q.qsize(),
                         code=Code.SERVER_BUSY, producer_id=env.producer_id,
                         msg_id=env.msg_id, service_name=env.service_name)
            return False
        try:
            self._q.put_nowait(env)
        except asyncio.QueueFull:
            depth = self._q.qsize()
            metrics.counter("queue_full_total")
            metrics.gauge("queue_depth", depth)
            # Loud by design (§10.4): 503s are going out.
            _log.warning("queue_full", queue_depth=depth, code=Code.SERVER_BUSY,
                         producer_id=env.producer_id, msg_id=env.msg_id,
                         service_name=env.service_name)
            return False

        depth = self._q.qsize()
        metrics.counter("msgs_accepted_total")
        metrics.gauge("queue_depth", depth)
        _log.info("msg_accepted", producer_id=env.producer_id, msg_id=env.msg_id,
                  service_name=env.service_name, queue_depth=depth)
        return True

    def start(self, handler: Handler, workers: int | None = None) -> None:
        """Start `workers` tasks draining the queue into `handler`.

        `workers` defaults to the count this queue was built with (§5.2's
        `workers`, via `from_config`). Requires a running event loop (it creates
        tasks). `handler` may be sync or async; anything awaitable it returns is
        awaited. Restart after `stop()` is supported and reopens the queue.
        """
        if workers is None:
            workers = self._default_workers
        if self._workers:
            raise RuntimeError("InboundQueue.start() called twice; stop() first")
        if workers < 1:
            raise ValueError(f"workers must be >= 1, got {workers}")
        self._closed = False
        self._handler = handler
        self._workers = [
            asyncio.create_task(self._run(i), name=f"msging-worker-{i}")
            for i in range(workers)
        ]
        _log.info("queue_started", workers=workers, queue_max=self._q.maxsize)

    async def stop(self, timeout_s: float = DEFAULT_DRAIN_TIMEOUT_S) -> None:
        """Close the queue to new work, drain what was accepted, then cancel
        the workers.

        Idempotent, and safe on a queue that was never started. Work still
        queued when `timeout_s` expires is dropped with the workers -- the
        client resends it once `sent_timeout_s` elapses (§4.4).

        The drain runs under try/finally: a `stop()` that is itself cancelled
        (a cancelled ASGI lifespan) must still reap its workers rather than
        leak the whole pool.
        """
        self._closed = True
        if not self._workers:
            return
        workers, self._workers = self._workers, []
        try:
            try:
                await asyncio.wait_for(self._q.join(), timeout_s)
            except asyncio.TimeoutError:
                dropped = self._discard_queued()
                # Loud: msgs are being dropped, and their producers will resend.
                # The count only -- never the msgs themselves (§10.4).
                _log.warning("queue_drain_timeout", dropped=dropped,
                             queue_depth=self._q.qsize(), timeout_s=timeout_s)
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            self._handler = None
            metrics.gauge("queue_depth", self._q.qsize())
            _log.info("queue_stopped", queue_depth=self._q.qsize())

    def _discard_queued(self) -> int:
        """Drop every still-queued Envelope and return how many. Undrained work
        left in place would pin the `queue_depth` gauge non-zero for the life of
        the process and hold Envelopes nobody will ever process."""
        dropped = 0
        while True:
            try:
                self._q.get_nowait()
            except asyncio.QueueEmpty:
                break
            dropped += 1
            self._q.task_done()
        return dropped

    def depth(self) -> int:
        """Accepted-but-unprocessed count.

        Queued only: a msg a worker has picked up is in flight and no longer
        queued, so accepted-but-unprocessed peaks at `queue_max + workers`.
        That is design §6.2's stated behaviour, not a gap.
        """
        return self._q.qsize()

    async def _run(self, worker_id: int) -> None:
        # TODO(msging-task-11): no supervision on BaseException. A handler that
        # raises one (MemoryError, a bare `raise BaseException`) kills its
        # worker silently and the pool shrinks with no log line and no restart.
        # Add a supervisor that logs and respawns the worker.
        while True:
            env = await self._q.get()
            try:
                await self._invoke(env)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                       # noqa: BLE001
                # One bad msg must never kill its worker: the app's handler is
                # arbitrary code and its failures are data, not control flow.
                # Type and location only -- `str(exc)` is app-authored text and
                # may carry the payload or an api_key (§10.4, see _raised_at).
                _log.error("handler_error", worker_id=worker_id,
                           producer_id=env.producer_id, msg_id=env.msg_id,
                           service_name=env.service_name,
                           error=type(exc).__name__, error_at=_raised_at(exc))
            finally:
                self._q.task_done()
                metrics.gauge("queue_depth", self._q.qsize())

    async def _invoke(self, env: Envelope) -> None:
        result = self._handler(env)
        if inspect.isawaitable(result):
            await result
