"""Bridges an async Starlette request stream to the sync file-like interface
tarfile requires — without buffering the archive in memory or on disk (doc §5).

A bounded queue.Queue hands off chunks from the event loop to a worker thread
running the (sync) unpack; queue.put/get block, giving natural backpressure
with no new dependency beyond stdlib + Starlette's bundled run_in_threadpool.
"""
from __future__ import annotations

import asyncio
import queue
from typing import Callable, TypeVar

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from .errors import UploadTimeoutError

_QUEUE_DEPTH = 8  # bounds in-flight chunks independent of archive size
_EOF = object()

T = TypeVar("T")


class _ChunkQueueReader:
    """Sync file-like object backed by a queue.Queue. .read(size) blocks (in
    the worker thread, off the event loop) until enough bytes, EOF, or a
    propagated exception."""

    def __init__(self, q: queue.Queue) -> None:
        self._q = q
        self._buf = b""

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunks = [self._buf]
            self._buf = b""
            while (item := self._drain_one()) is not None:
                chunks.append(item)
            return b"".join(chunks)

        while len(self._buf) < size:
            item = self._drain_one()
            if item is None:
                break
            self._buf += item
        result, self._buf = self._buf[:size], self._buf[size:]
        return result

    def _drain_one(self) -> bytes | None:
        item = self._q.get()
        if item is _EOF:
            return None
        if isinstance(item, Exception):
            raise item
        return item


async def _pump(request: Request, q: queue.Queue) -> None:
    """Reads request.stream() chunks onto q (blocking put = backpressure).
    Always terminates the queue with _EOF or a propagated exception so a
    reader parked on q.get() is never left hanging."""
    try:
        async for chunk in request.stream():
            if chunk:
                await run_in_threadpool(q.put, chunk)
    except Exception as exc:  # client disconnect, framework-level read error
        await run_in_threadpool(q.put, exc)
        return
    await run_in_threadpool(q.put, _EOF)


def _drain(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


def _force_unblock(q: queue.Queue, item: Exception) -> None:
    """Best-effort: get a reader stuck in q.get() to raise `item` instead of
    hanging, even if the queue happens to be full."""
    try:
        q.put_nowait(item)
    except queue.Full:
        _drain(q)
        try:
            q.put_nowait(item)
        except queue.Full:
            pass


async def bridge_and_run(
    request: Request,
    consumer: Callable[[_ChunkQueueReader], T],
    timeout_s: float,
) -> T:
    """Runs consumer(reader) in a worker thread while concurrently pumping the
    request body into it, both under a wall-clock deadline (doc §4's 499
    case). Raises UploadTimeoutError on deadline; propagates any exception
    consumer raises."""
    q: queue.Queue = queue.Queue(maxsize=_QUEUE_DEPTH)
    reader = _ChunkQueueReader(q)
    pump_task = asyncio.ensure_future(_pump(request, q))
    consumer_task = asyncio.ensure_future(run_in_threadpool(consumer, reader))
    try:
        await asyncio.wait_for(asyncio.gather(pump_task, consumer_task), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        if not pump_task.done():
            pump_task.cancel()
        _force_unblock(q, UploadTimeoutError())
        raise UploadTimeoutError() from exc
    except Exception:
        if not pump_task.done():
            pump_task.cancel()
        # Unblock a writer thread possibly mid-q.put() from a run_in_threadpool
        # call issued before cancellation, so it can return its pool slot even
        # though nobody reads further.
        _drain(q)
        if consumer_task.done() and consumer_task.exception() is not None:
            raise consumer_task.exception()
        raise
    return consumer_task.result()
