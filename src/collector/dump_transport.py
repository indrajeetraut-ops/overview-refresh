"""A fake in-process Ingest for exercising the engine without a live server -- the engine's
counterpart to normalizer/cli_normalize.py, keeping this module independently
inspectable/testable per the design's module-boundary philosophy.

Rewritten for the msging fire-and-forget + SSE model (event-messaging-design.md §2.2, §4).
`_FakeIngest` is one `httpx.MockTransport` answering all four routes the client drives:
  * `/register` -> issues an identity + the server's limits (§6.1),
  * `/event`    -> validates the Envelope, hands it to a caller-supplied sink, 202s, and
                   queues its `msg_id` for acknowledgement,
  * `/notify`   -> a live SSE stream: the `server_epoch` frame first (§4.3), then an
                   `event: notify` ACK frame per received msg as they arrive (§8.2),
  * `/close`    -> 200 and ends the stream.

Because the ACK rides back over SSE exactly as a real server sends it, the engine's
checkpoint-on-ACK path (engine.py) runs for real -- a source is marked done only once its
msgs are acked, so a re-run genuinely skips it. `make_dump_transport` is the file-writing
case: one JSON `Envelope` per line, appended as msgs are received.
"""
from __future__ import annotations

import gzip
import json
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

import httpx

from common.msging.models import Envelope, NotifyEv

_MAX_MSG_BYTES = 32 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _body(request: httpx.Request) -> bytes:
    raw = request.content
    if request.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw


class _FakeIngest:
    """A single-producer fake Ingest. ACKs every accepted msg over the SSE channel."""

    def __init__(self, on_envelope: Callable[[Envelope], None], *, producer_id: str = "collector-fake") -> None:
        self._on_envelope = on_envelope
        self._producer_id = producer_id
        self._epoch = "fake-epoch-1"  # constant: a changing epoch would trigger spurious §4.3 sweeps
        self._acks: "queue.Queue[int]" = queue.Queue()
        self._closed = threading.Event()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if name == "register":
            return httpx.Response(200, json={
                "producer_id": self._producer_id,
                "api_key": "fake-key",
                "service_name": "ingest",
                "registered_at": _now_iso(),
                "max_msg_bytes": _MAX_MSG_BYTES,
                "heartbeat_s": 10,
                "retry_after_s": 5,
            })
        if name == "event":
            envelope = Envelope.model_validate_json(_body(request))
            self._on_envelope(envelope)
            self._acks.put(envelope.msg_id)
            return httpx.Response(202, json={"code": "ok", "msg_id": envelope.msg_id})
        if name == "notify":
            return httpx.Response(
                200,
                content=self._notify_stream(),
                headers={"content-type": "text/event-stream"},
            )
        if name == "close":
            self._closed.set()
            return httpx.Response(200, json={"code": "ok"})
        return httpx.Response(404, json={"code": "rejected", "reason": "no route"})

    def _notify_stream(self) -> Iterator[bytes]:
        # §4.3: the epoch frame is always first. Same epoch every reconnect -> no spurious sweep.
        yield f'event: epoch\ndata: {{"server_epoch": "{self._epoch}"}}\n\n'.encode()
        while True:
            try:
                msg_id = self._acks.get(timeout=0.05)
            except queue.Empty:
                if self._closed.is_set():
                    return
                yield b": hb\n\n"  # keepalive so the reader's idle timer never trips
                continue
            ev = NotifyEv(msg_id=msg_id, producer_id=self._producer_id, code="ack", ts=_now_iso())
            yield f"event: notify\ndata: {json.dumps(ev.model_dump())}\n\n".encode()


def make_recording_transport(on_envelope: Callable[[Envelope], None]) -> httpx.MockTransport:
    """A fake Ingest that hands every received Envelope to `on_envelope` and ACKs it. Shared by
    `--dump-to` here and tests/collector/test_engine.py, so both drive the real client + the
    real checkpoint-on-ACK path against the same in-process server."""
    return _FakeIngest(on_envelope).transport()


def make_dump_transport(out_path: str | Path) -> httpx.MockTransport:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def sink(envelope: Envelope) -> None:
        with lock:  # /event runs on the caller's send thread; serialize the appends
            with out_path.open("a", encoding="utf-8") as f:
                f.write(envelope.model_dump_json() + "\n")

    return make_recording_transport(sink)
