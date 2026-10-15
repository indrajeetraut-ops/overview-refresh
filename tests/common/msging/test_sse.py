"""The client's SSE outcome reader (design §4).

Every test drives the real `SseReader` -- a background thread running blocking
httpx (§6.4) -- against either an `httpx.MockTransport` or, where the point is
timing or bytes on the wire, a real socket.

The frames here are the ones `srv/notify.py` actually emits, not a paraphrase:

    event: epoch\\ndata: {"server_epoch": "..."}\\n\\n
    event: notify\\ndata: {<notify_ev §8.2>}\\n\\n
    : hb\\n\\n

Waiting is done with Events and semaphores, never `sleep`: a mutation that
stops the reader firing a callback must make a test *hang and fail*, not race
a margin.

Three groups need a real socket, because a `MockTransport` short-circuits the
network and cannot express them:

  - the idle-read timer (§4.2) -- only a genuinely silent peer shows it firing;
  - the per-frame cap -- only a peer that streams without ever sending a
    newline shows the buffer is bounded;
  - the decompression cap -- a `content-encoding: gzip` stream must not be
    allowed to inflate without limit.
"""

import io
import json
import socket
import threading
import time
import tracemalloc
import zlib
from contextlib import contextmanager

import httpx
import pytest
import structlog

from common.msging.clnt.sse import (
    MAX_EVENT_BYTES,
    SseReader,
    _backoff_delay,
    _DECODE_CHUNK,
    _Decoder,
    _SseParser,
    notify_url,
)
from common.msging.errors import NoCallbackError
from common.msging.log import configure_logging
from common.msging.metrics import metrics, reset_metrics

API_KEY = "tk_live_" + "s" * 40
SECRET = "secret-marker-must-never-be-logged"
TS = "2026-07-25T10:15:31Z"
URL = "http://h/talium/ingest/v1/notify"
SSE_HEADERS = {"content-type": "text/event-stream"}


def setup_function():
    reset_metrics()


# -- frames --------------------------------------------------------------


def _epoch(epoch: str = "e1") -> str:
    return f'event: epoch\ndata: {{"server_epoch": "{epoch}"}}\n\n'


def _notify(**over) -> str:
    ev = {"msg_id": 42, "producer_id": "p", "code": "ack", "ts": TS}
    ev.update(over)
    return f"event: notify\ndata: {json.dumps(ev)}\n\n"


HB = ": hb\n\n"


# -- doubles -------------------------------------------------------------


class _Sink:
    """A callback that records and lets a test *wait* for the Nth call."""

    def __init__(self, raises=None):
        self.items = []
        self._raises = raises
        self._cv = threading.Condition()

    def __call__(self, item):
        with self._cv:
            self.items.append(item)
            self._cv.notify_all()
        if self._raises is not None:
            raise self._raises

    def wait(self, n: int, timeout: float = 10.0) -> list:
        with self._cv:
            ok = self._cv.wait_for(lambda: len(self.items) >= n, timeout)
        assert ok, f"expected {n} callback(s), got {self.items!r}"
        return list(self.items)


def _open_stream(*frames):
    """Emit `frames`, then hold the stream open until the peer is released.

    A buffered mock body ends the instant it is read, which would put the
    reader straight into its reconnect loop; most tests want one live stream.
    """
    def make(peer, request):
        def body():
            for frame in frames:
                yield frame.encode() if isinstance(frame, str) else frame
            peer.hold.wait(10.0)
        return httpx.Response(200, content=body(), headers=SSE_HEADERS)
    return make


def _closed_stream(*frames):
    """Emit `frames` and end -- i.e. the server dropped the link."""
    def make(peer, request):
        body = "".join(frames).encode()
        return httpx.Response(200, content=body, headers=SSE_HEADERS)
    return make


def _status(code: int):
    def make(peer, request):
        return httpx.Response(code, json={"code": "unauthorized"})
    return make


def _refused(peer, request):
    raise httpx.ConnectError("refused", request=request)


class _Peer:
    """A `MockTransport` that serves one scripted response per connect.

    The last script repeats, so a reconnect test can say "this once, then this
    forever". `wait_connects` is how a test observes a reconnect without
    sleeping.
    """

    def __init__(self, *scripts):
        self.hold = threading.Event()
        self.requests: list[httpx.Request] = []
        self._scripts = list(scripts) or [_open_stream()]
        self._n = 0
        self._sem = threading.Semaphore(0)
        self.closes = 0
        self.transport = _RecordingTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        script = self._scripts[min(self._n, len(self._scripts) - 1)]
        self._n += 1
        self._sem.release()
        return script(self, request)

    @property
    def connects(self) -> int:
        return len(self.requests)

    def wait_connects(self, k: int, timeout: float = 10.0) -> None:
        for i in range(k):
            assert self._sem.acquire(timeout=timeout), (
                f"peer saw {self.connects} connect(s), waited for {k}")

    def release(self) -> None:
        self.hold.set()


class _RecordingTransport(httpx.MockTransport):
    """A mock that remembers whether anyone closed it."""

    def __init__(self, peer):
        super().__init__(peer)
        self._peer = peer

    def close(self) -> None:
        self._peer.closes += 1


@contextmanager
def _reader(peer=None, on_event=None, on_epoch=None, **kw):
    kw.setdefault("idle_timeout_s", 5.0)
    kw.setdefault("backoff_base_s", 0.001)
    kw.setdefault("backoff_cap_s", 0.01)
    reader = SseReader(URL, API_KEY,
                       on_event=on_event if on_event is not None else _Sink(),
                       on_epoch=on_epoch if on_epoch is not None else _Sink(),
                       _transport=peer.transport if peer is not None else None,
                       **kw)
    reader.start()
    try:
        yield reader
    finally:
        if peer is not None:
            peer.release()
        reader.stop()


# -- §4.1 the callback is required before the channel opens --------------


def test_start_without_on_event_raises():
    reader = SseReader(URL, API_KEY, on_event=None, on_epoch=lambda e: None,
                       idle_timeout_s=1.0, _transport=_Peer().transport)
    with pytest.raises(NoCallbackError):
        reader.start()


def test_start_without_on_event_spawns_no_thread():
    """§4.1 is about not *losing* outcomes: a reader that raised but still
    opened the stream would drop exactly the notices the rule protects."""
    before = _sse_threads()
    reader = SseReader(URL, API_KEY, on_event=None, on_epoch=lambda e: None,
                       idle_timeout_s=1.0, _transport=_Peer().transport)
    with pytest.raises(NoCallbackError):
        reader.start()
    assert _sse_threads() == before
    assert reader.connected is False


def test_start_without_on_epoch_raises():
    """Same rule, other half: without an epoch handler a server restart is
    undetectable and `sent/` is never swept (§4.3), so the promise silently
    stops holding."""
    reader = SseReader(URL, API_KEY, on_event=lambda e: None, on_epoch=None,
                       idle_timeout_s=1.0, _transport=_Peer().transport)
    with pytest.raises(NoCallbackError):
        reader.start()


def test_a_non_callable_callback_raises():
    reader = SseReader(URL, API_KEY, on_event="not a function",
                       on_epoch=lambda e: None, idle_timeout_s=1.0,
                       _transport=_Peer().transport)
    with pytest.raises(NoCallbackError):
        reader.start()


def _sse_threads() -> list[str]:
    return sorted(t.name for t in threading.enumerate()
                  if t.name.startswith("msging-sse"))


def _named_threads(name: str) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == name]


def _eventually(pred, message: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.005)
    raise AssertionError(message)


# -- §4.3 the epoch frame ------------------------------------------------


def test_the_epoch_frame_fires_on_epoch():
    epochs = _Sink()
    peer = _Peer(_open_stream(_epoch("abc")))
    with _reader(peer, on_epoch=epochs):
        assert epochs.wait(1) == ["abc"]


def test_the_epoch_field_is_server_epoch_not_epoch():
    """`srv/notify.py` sends `{"server_epoch": ...}`. Reading some other key
    would leave every restart undetected -- and reading *any* key would let a
    stray frame trigger a spurious sweep of all of `sent/`."""
    epochs = _Sink()
    frame = 'event: epoch\ndata: {"epoch": "wrong-key"}\n\n'
    peer = _Peer(_open_stream(frame, _epoch("right")))
    with _reader(peer, on_epoch=epochs):
        assert epochs.wait(1) == ["right"]


def test_an_unchanged_epoch_does_not_fire_again():
    """A sweep costs a full resend of `sent/` (§4.3). Firing on every frame,
    or on every reconnect, turns a quiet server into a resend storm.

    The notify frames are the synchronisation: each one trails its stream's
    epoch frame, so receiving both proves both epoch frames were parsed."""
    epochs, events = _Sink(), _Sink()
    peer = _Peer(_closed_stream(_epoch("same"), HB, _notify(msg_id=1)),
                 _open_stream(_epoch("same"), HB, _notify(msg_id=2)))
    with _reader(peer, on_event=events, on_epoch=epochs):
        assert [e.msg_id for e in events.wait(2)] == [1, 2]
        assert epochs.items == ["same"]


def test_a_changed_epoch_fires_again():
    epochs = _Sink()
    peer = _Peer(_closed_stream(_epoch("boot-1")),
                 _open_stream(_epoch("boot-2")))
    with _reader(peer, on_epoch=epochs):
        assert epochs.wait(2) == ["boot-1", "boot-2"]


def test_an_epoch_change_counts_epoch_sweeps_total():
    epochs = _Sink()
    peer = _Peer(_closed_stream(_epoch("boot-1")),
                 _open_stream(_epoch("boot-2")))
    with _reader(peer, on_epoch=epochs):
        epochs.wait(2)
    assert _count("epoch_sweeps_total") == 2


def test_an_unchanged_epoch_counts_no_sweep():
    epochs, events = _Sink(), _Sink()
    peer = _Peer(_closed_stream(_epoch("same"), _notify(msg_id=1)),
                 _open_stream(_epoch("same"), _notify(msg_id=2)))
    with _reader(peer, on_event=events, on_epoch=epochs):
        events.wait(2)
    assert _count("epoch_sweeps_total") == 1


def test_a_malformed_epoch_frame_is_ignored_and_the_stream_survives():
    epochs, events = _Sink(), _Sink()
    peer = _Peer(_open_stream('event: epoch\ndata: not json\n\n',
                              'event: epoch\ndata: {"server_epoch": 7}\n\n',
                              'event: epoch\ndata: {"server_epoch": ""}\n\n',
                              _notify()))
    with _reader(peer, on_event=events, on_epoch=epochs):
        events.wait(1)
        assert epochs.items == []


def test_an_untyped_frame_after_an_epoch_frame_is_not_an_epoch():
    """The same reset, end to end: `srv/notify.py` will not send this, but a
    proxy that reorders or a future frame that drops its `event:` line would,
    and reading it as an epoch resends the whole of `sent/`."""
    epochs, events = _Sink(), _Sink()
    peer = _Peer(_open_stream(_epoch("e1"),
                              'data: {"server_epoch": "e2"}\n\n',
                              _notify(msg_id=1)))
    with _reader(peer, on_event=events, on_epoch=epochs):
        events.wait(1)
    assert epochs.items == ["e1"]


def test_an_epoch_frame_whose_body_is_not_an_object_is_ignored():
    """`data: []` parses as JSON and is not an epoch. Reaching into it
    unguarded raises out of the parser batch and drops the link -- which for a
    peer that sends this every stream is a permanent reconnect loop.

    The second script is a live but silent stream, so the notice behind the bad
    frame arrives only if the bad frame was survived rather than reconnected
    past.
    """
    epochs, events = _Sink(), _Sink()
    bad = ('event: epoch\ndata: []\n\n'
           'event: epoch\ndata: "e"\n\n'
           'event: epoch\ndata: 7\n\n'
           'event: epoch\ndata: null\n\n')
    peer = _Peer(_open_stream(bad + _notify(msg_id=1)), _open_stream())
    with _reader(peer, on_event=events, on_epoch=epochs):
        assert events.wait(1)[0].msg_id == 1
    assert epochs.items == []


def test_an_event_type_that_merely_contains_epoch_is_not_an_epoch():
    """The match on the event type is exact. A substring test would let
    `event: pre-epoch` sweep all of `sent/`, and §10.2's rule for a frame this
    version does not know is to ignore it -- not to guess from its name."""
    epochs, events = _Sink(), _Sink()
    decoy = json.dumps({"msg_id": 5, "producer_id": "p", "code": "ack",
                        "ts": TS})
    peer = _Peer(_open_stream('event: pre-epoch\ndata: {"server_epoch": "x"}\n\n',
                              'event: epoch_v2\ndata: {"server_epoch": "y"}\n\n',
                              f'event: pre-notify\ndata: {decoy}\n\n',
                              f'event: notify_v2\ndata: {decoy}\n\n',
                              _notify(msg_id=1)))
    with _reader(peer, on_event=events, on_epoch=epochs):
        events.wait(1)
    assert epochs.items == []
    assert [e.msg_id for e in events.items] == [1]


def test_the_epoch_is_exposed_for_the_cleaner_loop():
    epochs = _Sink()
    peer = _Peer(_open_stream(_epoch("boot-9")))
    with _reader(peer, on_epoch=epochs) as reader:
        epochs.wait(1)
        assert reader.epoch == "boot-9"


# -- §8.2 the notify frame -----------------------------------------------


def test_a_notify_frame_fires_on_event():
    events = _Sink()
    frame = _notify(msg_id=42, code="ack", event_id="sess-abc", reason="stored",
                    info={"row": 7})
    peer = _Peer(_open_stream(_epoch(), frame))
    with _reader(peer, on_event=events):
        ev = events.wait(1)[0]
    assert ev.msg_id == 42 and ev.code == "ack"
    assert ev.producer_id == "p" and ev.event_id == "sess-abc"
    assert ev.reason == "stored" and ev.info == {"row": 7} and ev.ts == TS


def test_a_nack_is_delivered_like_any_other_code():
    """§8.2 makes `code` extensible; the reader must not filter on it."""
    events = _Sink()
    peer = _Peer(_open_stream(_epoch(), _notify(code="nack", msg_id=3),
                              _notify(code="something_new", msg_id=4)))
    with _reader(peer, on_event=events):
        got = events.wait(2)
    assert [e.code for e in got] == ["nack", "something_new"]


def test_every_notify_frame_is_delivered_in_order():
    events = _Sink()
    frames = [_notify(msg_id=i) for i in range(1, 21)]
    peer = _Peer(_open_stream(_epoch(), *frames))
    with _reader(peer, on_event=events):
        got = events.wait(20)
    assert [e.msg_id for e in got] == list(range(1, 21))


def test_a_malformed_notify_frame_is_ignored_and_the_stream_survives():
    events = _Sink()
    peer = _Peer(_open_stream(_epoch(),
                              'event: notify\ndata: {"msg_id":\n\n',
                              'event: notify\ndata: {"msg_id": 1}\n\n',
                              'event: notify\ndata: []\n\n',
                              _notify(msg_id=99)))
    with _reader(peer, on_event=events):
        got = events.wait(1)
    assert [e.msg_id for e in got] == [99]


def test_an_unknown_event_type_is_ignored():
    """§10.2: receivers must ignore what they do not know, or the protocol can
    never grow a frame.

    The decoy bodies are deliberately *valid* notify_evs: a reader that
    dispatched on the data rather than on the event type would sail past a
    malformed decoy and only be caught by one that parses.
    """
    events, epochs = _Sink(), _Sink()
    decoy = json.dumps({"msg_id": 5, "producer_id": "p", "code": "ack",
                        "ts": TS})
    untyped = json.dumps({"msg_id": 6, "producer_id": "p", "code": "ack",
                          "ts": TS})
    peer = _Peer(_open_stream(_epoch(),
                              f'event: rebalance\ndata: {decoy}\n\n',
                              f'data: {untyped}\n\n',
                              _notify(msg_id=7)))
    with _reader(peer, on_event=events, on_epoch=epochs):
        got = events.wait(1)
    assert [e.msg_id for e in got] == [7]


def test_an_unknown_event_type_never_reaches_on_epoch_either():
    epochs, events = _Sink(), _Sink()
    peer = _Peer(_open_stream('event: rebalance\ndata: {"server_epoch": "x"}\n\n',
                              _notify(msg_id=1)))
    with _reader(peer, on_event=events, on_epoch=epochs):
        events.wait(1)
    assert epochs.items == []


# -- §4.2 comments are keepalive, not data -------------------------------


def test_comment_frames_are_ignored():
    events, epochs = _Sink(), _Sink()
    peer = _Peer(_open_stream(HB, HB, ": something else\n\n"))
    with _reader(peer, on_event=events, on_epoch=epochs) as reader:
        # Nothing may have been dispatched; the link is nonetheless alive.
        _eventually(lambda: reader.connected, "the link never came up")
        assert events.items == [] and epochs.items == []


def test_heartbeats_between_frames_do_not_corrupt_parsing():
    events, epochs = _Sink(), _Sink()
    peer = _Peer(_open_stream(HB, _epoch("e9"), HB, _notify(msg_id=11), HB,
                              _notify(msg_id=12)))
    with _reader(peer, on_event=events, on_epoch=epochs):
        got = events.wait(2)
        assert epochs.wait(1) == ["e9"]
    assert [e.msg_id for e in got] == [11, 12]


# -- wire-format details the parser has to get right ---------------------


def test_frames_split_across_chunk_boundaries_are_reassembled():
    """The transport chunks by socket read, not by frame."""
    events, epochs = _Sink(), _Sink()
    blob = (_epoch("e1") + _notify(msg_id=1) + HB + _notify(msg_id=2)).encode()
    byte_at_a_time = [blob[i:i + 1] for i in range(len(blob))]

    def make(peer, request):
        def body():
            yield from byte_at_a_time
            peer.hold.wait(10.0)
        return httpx.Response(200, content=body(), headers=SSE_HEADERS)

    peer = _Peer(make)
    with _reader(peer, on_event=events, on_epoch=epochs):
        got = events.wait(2)
        assert epochs.wait(1) == ["e1"]
    assert [e.msg_id for e in got] == [1, 2]


def test_crlf_line_endings_parse():
    events = _Sink()
    frame = ('event: notify\r\n'
             'data: ' + json.dumps({"msg_id": 8, "producer_id": "p",
                                    "code": "ack", "ts": TS}) + '\r\n\r\n')
    peer = _Peer(_open_stream(frame))
    with _reader(peer, on_event=events):
        assert events.wait(1)[0].msg_id == 8


def test_a_crlf_split_across_two_reads_does_not_dispatch_early():
    """A trailing lone `\\r` is ambiguous -- it may be the first half of a CRLF
    that landed in the next socket read. Treating it as a complete line makes
    the `\\n` that follows look like the blank line that ends a frame, so the
    frame dispatches early and the rest of its `data:` splits into a second,
    bogus event.
    """
    parser = _SseParser()
    frame = 'event: notify\r\ndata: {"a":\r\ndata:  1}\r\n\r\n'.encode()
    out = []
    for i in range(len(frame)):
        out += parser.feed(frame[i:i + 1])
    assert out == [("notify", '{"a":\n 1}')]


def test_a_comment_body_is_never_read_as_fields():
    """`: hb` is inert, and so is a comment whose body is shaped like one --
    a comment must not be able to inject data or an event type."""
    parser = _SseParser()
    assert parser.feed(b":event: notify\n:data: injected\n\n") == []
    assert parser.feed(b": hb\ndata: real\n\n") == [("message", "real")]


def test_a_comment_inside_a_frame_neither_ends_nor_discards_it():
    """A keepalive is not a frame boundary. Treating it as one would truncate
    whatever frame was mid-flight and emit the remainder as a second event."""
    parser = _SseParser()
    out = parser.feed(b"event: notify\ndata: x\n: hb\ndata: y\n\n")
    assert out == [("notify", "x\ny")]


def test_multi_line_data_is_concatenated_with_newlines():
    parser = _SseParser()
    out = parser.feed(b"event: notify\ndata: {\"a\":\ndata:  1}\n\n")
    assert out == [("notify", '{"a":\n 1}')]


def test_one_leading_space_is_stripped_from_a_field_value():
    parser = _SseParser()
    assert parser.feed(b"event: notify\ndata: x\n\n") == [("notify", "x")]
    assert parser.feed(b"event:notify\ndata:x\n\n") == [("notify", "x")]


def test_id_and_retry_fields_are_ignored():
    parser = _SseParser()
    out = parser.feed(b"id: 7\nretry: 3000\nevent: notify\ndata: x\n\n")
    assert out == [("notify", "x")]


def test_the_event_type_does_not_leak_into_the_next_frame():
    """Dispatch resets the event type as well as the data. If it did not, an
    untyped `data:` frame following an `event: epoch` frame would itself read
    as an epoch -- a spurious full resend of `sent/`, which is the exact hazard
    the module exists to avoid."""
    parser = _SseParser()
    out = parser.feed(b'event: epoch\ndata: {"server_epoch": "e1"}\n\n'
                      b'data: {"server_epoch": "e2"}\n\n')
    assert out == [("epoch", '{"server_epoch": "e1"}'),
                   ("message", '{"server_epoch": "e2"}')]


def test_a_frame_with_no_data_dispatches_nothing():
    parser = _SseParser()
    assert parser.feed(b"event: notify\n\n") == []


def test_a_comment_never_dispatches():
    parser = _SseParser()
    assert parser.feed(b": hb\n\n: hb\n\n") == []


def test_the_parser_bounds_an_endless_line():
    """An SSE *stream* is unbounded; a single frame is not. A peer that never
    sends a newline must not be able to grow this buffer without limit."""
    parser = _SseParser()
    with pytest.raises(Exception) as exc:
        for _ in range(64):
            parser.feed(b"x" * 8192)
    assert "frame" in str(exc.value).lower()


def test_the_parser_bounds_accumulated_data_lines():
    """Same cap, reached the other way: many short `data:` lines."""
    parser = _SseParser()
    with pytest.raises(Exception):
        for _ in range(MAX_EVENT_BYTES // 8 + 16):
            parser.feed(b"data: xxxxxxxx\n")


# -- §4.2 the idle-read timer (real sockets) -----------------------------


class _RawSse:
    """A real socket speaking HTTP/1.1, one scripted connection at a time.

    `MockTransport` short-circuits the network, so anything about *timing on
    the wire* -- which is the whole of §4.2 -- has to go through a socket.
    """

    def __init__(self, script):
        self._script = script
        self.stop = threading.Event()
        self._sem = threading.Semaphore(0)
        self.connects = 0
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._acceptor = threading.Thread(target=self._accept, daemon=True)
        self._workers: list[threading.Thread] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/talium/ingest/v1/notify"

    def _accept(self):
        while not self.stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.connects += 1
            self._sem.release()
            worker = threading.Thread(target=self._serve,
                                      args=(conn, self.connects), daemon=True)
            worker.start()
            self._workers.append(worker)

    def _serve(self, conn, n):
        try:
            conn.recv(1 << 16)
            self._script(conn, self.stop, n)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def wait_connects(self, k: int, timeout: float = 15.0) -> None:
        for _ in range(k):
            assert self._sem.acquire(timeout=timeout), (
                f"peer saw {self.connects} connect(s), waited for {k}")

    def __enter__(self):
        self._acceptor.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self._sock.close()
        for worker in self._workers:
            # Daemon threads: a worker still blocked in sendall against a
            # socket the reader already dropped must not hold up the suite.
            worker.join(0.5)
        return False


SSE_HEAD = (b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Cache-Control: no-cache\r\n"
            b"Connection: close\r\n\r\n")


def _silent_after_epoch(conn, stop, n):
    conn.sendall(SSE_HEAD + _epoch(f"boot-{n}").encode())
    stop.wait(30.0)


def test_a_silent_peer_trips_the_idle_timer_and_the_reader_reconnects():
    """§4.2: no bytes at all for `idle_timeout_s` means the link is dead --
    a half-open TCP connection is otherwise indistinguishable from a server
    with nothing to say, and the reader would wait for outcomes forever."""
    with _RawSse(_silent_after_epoch) as srv:
        reader = SseReader(srv.url, API_KEY, on_event=_Sink(),
                           on_epoch=_Sink(), idle_timeout_s=0.3,
                           backoff_base_s=0.01, backoff_cap_s=0.05)
        reader.start()
        try:
            srv.wait_connects(3)
        finally:
            reader.stop()
    assert _count("sse_reconnects_total") >= 2


def _heartbeating(conn, stop, n):
    conn.sendall(SSE_HEAD + _epoch("boot").encode())
    while not stop.wait(0.05):
        conn.sendall(HB.encode())


def test_heartbeats_keep_the_link_alive():
    """The other half of §4.2: `: hb` is exactly what stops the idle timer
    firing on a healthy but quiet server."""
    with _RawSse(_heartbeating) as srv:
        epochs = _Sink()
        reader = SseReader(srv.url, API_KEY, on_event=_Sink(), on_epoch=epochs,
                           idle_timeout_s=0.5, backoff_base_s=0.01,
                           backoff_cap_s=0.05)
        reader.start()
        try:
            epochs.wait(1)
            time.sleep(1.5)          # 3x the idle timeout, heartbeats only
            assert srv.connects == 1, "a heartbeaten link was declared dead"
            assert reader.connected is True
        finally:
            reader.stop()


def test_stop_does_not_wait_for_the_idle_timeout():
    """The reader parks in a blocking read for up to `idle_timeout_s`.
    `stop()` has to break it out, not wait it out."""
    with _RawSse(_silent_after_epoch) as srv:
        epochs = _Sink()
        reader = SseReader(srv.url, API_KEY, on_event=_Sink(), on_epoch=epochs,
                           idle_timeout_s=30.0, backoff_base_s=0.01,
                           backoff_cap_s=0.05)
        reader.start()
        epochs.wait(1)
        started = time.monotonic()
        reader.stop()
        elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"stop() took {elapsed:.2f}s under a 30s idle timeout"
    assert _sse_threads() == []


# -- bounded reads (real sockets) ----------------------------------------


def _endless_line(conn, stop, n):
    conn.sendall(SSE_HEAD)
    blob = b"data: " + b"x" * 65536
    conn.sendall(blob)
    while not stop.is_set():
        try:
            conn.sendall(b"x" * 65536)          # never a newline
        except OSError:
            return


def test_a_peer_that_never_sends_a_newline_is_cut_off():
    """An unterminated frame must not become unbounded memory. The stream is
    dropped and retried; the buffer is not allowed to grow to the peer's
    ambition."""
    with _RawSse(_endless_line) as srv:
        reader = SseReader(srv.url, API_KEY, on_event=_Sink(),
                           on_epoch=_Sink(), idle_timeout_s=5.0,
                           backoff_base_s=0.01, backoff_cap_s=0.05)
        tracemalloc.start()
        reader.start()
        try:
            srv.wait_connects(2)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            reader.stop()
    assert peak < 8 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB for a 64 KiB cap"


def _gzip_bomb() -> bytes:
    """~50 MB of 'x' in a few hundred KB, built without ever holding 50 MB."""
    obj = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    out = bytearray()
    for _ in range(50):
        out += obj.compress(b"x" * (1 << 20))
    out += obj.flush()
    return bytes(out)


BOMB = _gzip_bomb()


def _gzipped_bomb(conn, stop, n):
    conn.sendall(b"HTTP/1.1 200 OK\r\n"
                 b"Content-Type: text/event-stream\r\n"
                 b"Content-Encoding: gzip\r\n"
                 b"Connection: close\r\n\r\n")
    try:
        conn.sendall(BOMB)
    except OSError:
        return
    stop.wait(10.0)


def test_a_compressed_stream_cannot_inflate_without_bound():
    """`iter_bytes`/`iter_lines` hand a whole raw chunk to the content decoder
    before yielding, so a `content-encoding: gzip` peer expands the wire by its
    full ratio before any cap is consulted -- measured at 200 MB from a 194 KB
    body against an 8 KiB cap in the POST path. Raw chunks plus an explicitly
    bounded decompressor is the only shape that holds."""
    with _RawSse(_gzipped_bomb) as srv:
        reader = SseReader(srv.url, API_KEY, on_event=_Sink(),
                           on_epoch=_Sink(), idle_timeout_s=5.0,
                           backoff_base_s=0.01, backoff_cap_s=0.05)
        tracemalloc.start()
        reader.start()
        try:
            srv.wait_connects(2)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            reader.stop()
    assert peak < 8 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB from a gzip bomb"


def test_the_reader_asks_for_an_identity_encoding():
    """Belt as well as braces: nothing about SSE benefits from compression,
    and not asking for it is the cheapest half of the bound above."""
    peer = _Peer(_open_stream(_epoch()))
    epochs = _Sink()
    with _reader(peer, on_epoch=epochs):
        epochs.wait(1)
    assert peer.requests[0].headers["accept-encoding"] == "identity"


# -- §4.2/§4.3 reconnect -------------------------------------------------


def test_a_dropped_stream_is_reconnected():
    peer = _Peer(_closed_stream(_epoch(), _notify(msg_id=1)),
                 _open_stream(_epoch(), _notify(msg_id=2)))
    events = _Sink()
    with _reader(peer, on_event=events):
        got = events.wait(2)
    assert [e.msg_id for e in got] == [1, 2]
    assert _count("sse_reconnects_total") >= 1


def test_a_refused_connection_is_retried():
    peer = _Peer(_refused, _refused, _open_stream(_epoch("late")))
    epochs = _Sink()
    with _reader(peer, on_epoch=epochs):
        assert epochs.wait(1) == ["late"]
    assert peer.connects >= 3


def test_a_non_200_is_retried_and_dispatches_nothing():
    """A 401 means re-register (§8.1); the reader cannot do that, but it must
    not go quiet either -- an unread outcome stream is lost outcomes."""
    events, epochs = _Sink(), _Sink()
    peer = _Peer(_status(401), _status(401), _open_stream(_epoch("after-401")))
    with _reader(peer, on_event=events, on_epoch=epochs):
        assert epochs.wait(1) == ["after-401"]
    assert events.items == []


def test_a_401_asks_the_owner_and_stops_when_it_declines():
    """§8.1's answer to `unauthorized` is *re-register*, which this class
    cannot do -- it holds an api_key, not a binding. So it asks the owner, and
    a "no" ends the loop instead of hammering a revoked key forever."""
    asked = []
    peer = _Peer(_status(401))
    with _reader(peer, on_unauthorized=lambda status: asked.append(status)) as r:
        for _ in range(200):                 # the thread exits on its own
            if not r._thread.is_alive():
                break
            time.sleep(0.01)
        assert asked == [401], "the hook must be asked exactly once"
        assert not r._thread.is_alive(), "a declined 401 must end the loop"
    assert peer.connects == 1, "no reconnect after the owner declined"


def test_a_401_keeps_retrying_when_the_owner_says_so():
    """A truthy hook means "I fixed it, keep going" -- and no hook at all keeps
    the historical behaviour, which the test above this one pins."""
    peer = _Peer(_status(401), _status(401), _open_stream(_epoch("after-401")))
    epochs = _Sink()
    with _reader(peer, on_epoch=epochs, on_unauthorized=lambda status: True):
        assert epochs.wait(1) == ["after-401"]


def test_a_restarted_reader_gets_a_fresh_verdict_after_a_halt():
    """A halt is a property of one run: an owner that re-registered and
    restarted this reader is entitled to a new answer on the new key. The
    second attempt below is a refused connect, so a `_halted` that survived
    the restart would end the loop before the stream that follows it."""
    peer = _Peer(_status(401), _refused, _open_stream(_epoch("after-restart")))
    epochs = _Sink()
    reader = SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=epochs,
                       idle_timeout_s=5.0, backoff_base_s=0.001,
                       backoff_cap_s=0.01,
                       on_unauthorized=lambda status: False,
                       _transport=peer.transport)
    reader.start()
    for _ in range(500):
        if not reader._thread.is_alive():
            break
        time.sleep(0.01)
    assert not reader._thread.is_alive(), "the declined 401 must halt the loop"

    reader.start()
    try:
        assert epochs.wait(1) == ["after-restart"]
    finally:
        peer.release()
        reader.stop()


def test_a_raising_unauthorized_hook_does_not_stop_the_reader():
    """App code, on the reader thread: a bug in it must not silently end the
    outcome stream."""
    peer = _Peer(_status(401), _open_stream(_epoch("after-401")))
    epochs = _Sink()

    def boom(status):
        raise RuntimeError(SECRET)

    out = _capture()
    with _reader(peer, on_epoch=epochs, on_unauthorized=boom):
        assert epochs.wait(1) == ["after-401"]
    assert SECRET not in out.getvalue()


def test_a_non_200_is_a_failed_connect_whatever_the_body_says():
    """The status decides, not the body and not the content-type.

    An auth proxy that answers 403 with `content-type: text/event-stream` is
    not a stream. A reader that only checks for 5xx consumes that body as a
    live stream -- `connected` reports True, the backoff curve never runs, and
    whatever the proxy chose to put in it is dispatched as real outcomes.
    """
    events, epochs = _Sink(), _Sink()

    def forbidden(peer, request):
        body = (_epoch("from-the-proxy") + _notify(msg_id=1)).encode()
        return httpx.Response(403, headers=SSE_HEADERS, content=body)

    peer = _Peer(forbidden, forbidden, _open_stream(_epoch("the-real-server")))
    with _reader(peer, on_event=events, on_epoch=epochs):
        assert epochs.wait(1) == ["the-real-server"]
    assert epochs.items == ["the-real-server"], "the 403 body was parsed"
    assert events.items == [], "the 403 body was dispatched"
    assert _count("sse_reconnects_total") >= 2


def test_a_redirect_is_not_followed():
    """Following a 3xx would hand the bearer token to whatever host the
    redirect names."""
    def redirect(peer, request):
        return httpx.Response(302, headers={"location": "http://evil/notify"})

    peer = _Peer(redirect, _open_stream(_epoch("ok")))
    epochs = _Sink()
    with _reader(peer, on_epoch=epochs):
        epochs.wait(1)
    assert all(r.url.host == "h" for r in peer.requests)


def test_the_reconnect_backs_off_on_a_growing_curve(monkeypatch):
    """Not a hot loop: every failed attempt must cost a longer wait than the
    last, or a server that just booted gets hammered by every producer at once
    (§4.3's thundering herd)."""
    import common.msging.clnt.sse as sse

    seen: list[int] = []
    real = sse._backoff_delay

    def spy(attempt, base_s, cap_s):
        seen.append(attempt)
        real(attempt, base_s, cap_s)
        return 0.0

    monkeypatch.setattr(sse, "_backoff_delay", spy)
    peer = _Peer(_refused, _refused, _refused, _open_stream(_epoch("up")))
    epochs = _Sink()
    # The delay itself is stubbed to 0, so the base only decides what counts
    # as a "healthy" stream -- well above how long a refused connect takes.
    with _reader(peer, on_epoch=epochs, backoff_base_s=5.0, backoff_cap_s=5.0):
        epochs.wait(1)
    assert seen[:3] == [0, 1, 2], f"attempt counter did not grow: {seen}"


def test_the_backoff_curve_resets_after_a_healthy_stream(monkeypatch):
    import common.msging.clnt.sse as sse

    seen: list[int] = []
    monkeypatch.setattr(sse, "_backoff_delay",
                        lambda attempt, base_s, cap_s: seen.append(attempt) or 0.0)
    # Two failures, then a stream that lives well past backoff_base_s, then
    # more failures: the curve must start over rather than resume at 2.
    peer = _Peer(_refused, _refused, _open_stream(_epoch("up")))
    epochs = _Sink()
    with _reader(peer, on_epoch=epochs, backoff_base_s=0.05,
                 backoff_cap_s=0.05):
        epochs.wait(1)
        assert seen == [0, 1]
        time.sleep(0.3)              # well past backoff_base_s
        peer.release()
        peer.wait_connects(4)
    assert 0 in seen[2:], f"curve did not reset after a healthy stream: {seen}"


def _blackhole(conn, stop, n):
    """Accepts, reads the request, and never answers. No status line ever."""
    stop.wait(30.0)


def test_a_peer_that_never_opens_a_stream_does_not_reset_the_backoff_curve(
        monkeypatch):
    """§4.3's herd control *is* the growing curve, and only a stream that
    actually carried bytes may reset it.

    A blackhole peer -- accepts the connection, never sends a status line --
    burns the full connect and header-read budget on every attempt. Counting
    that as stream uptime resets `attempt` every cycle, so this peer is retried
    at the base interval forever and the curve never climbs toward its cap:
    exactly the un-spread reconnect storm §4.3 exists to prevent.
    """
    import common.msging.clnt.sse as sse

    seen: list[int] = []
    monkeypatch.setattr(sse, "_backoff_delay",
                        lambda attempt, base_s, cap_s: seen.append(attempt) or 0.0)
    # The header read times out after idle_timeout_s, which is comfortably
    # longer than backoff_base_s -- so a clock started before the connect calls
    # every one of these attempts a healthy stream.
    with _RawSse(_blackhole) as srv:
        reader = SseReader(srv.url, API_KEY, on_event=_Sink(),
                           on_epoch=_Sink(), idle_timeout_s=0.3,
                           backoff_base_s=0.2, backoff_cap_s=0.2)
        reader.start()
        try:
            _eventually(lambda: len(seen) >= 4,
                        f"only {len(seen)} backoff(s) after 4 dead connects")
        finally:
            reader.stop()
    assert seen[:4] == [0, 1, 2, 3], f"the curve did not grow: {seen}"


def test_backoff_is_jittered():
    """Same delay for everyone means the herd reconnects in lockstep (§4.3).
    A constant curve cannot survive this."""
    samples = [_backoff_delay(5, 0.5, 30.0) for _ in range(200)]
    assert len(set(samples)) > 100, "backoff is not jittered"
    assert min(samples) < 0.5 * max(samples), "jitter band is too narrow"


def test_backoff_grows_with_the_attempt_count():
    early = max(_backoff_delay(0, 0.5, 30.0) for _ in range(200))
    late = max(_backoff_delay(5, 0.5, 30.0) for _ in range(200))
    assert late > early * 4


def test_backoff_is_capped():
    assert all(0.0 <= _backoff_delay(a, 0.5, 2.0) <= 2.0
               for a in range(64) for _ in range(8))


def test_backoff_never_returns_a_negative_or_a_nan():
    for attempt in (0, 1, 40, 1024):
        d = _backoff_delay(attempt, 0.5, 30.0)
        assert 0.0 <= d <= 30.0


def test_stop_interrupts_the_backoff_sleep():
    """A 100 s backoff must not become a 100 s `stop()`."""
    peer = _Peer(_refused)
    reader = SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=_Sink(),
                       idle_timeout_s=1.0, backoff_base_s=100.0,
                       backoff_cap_s=100.0, _transport=peer.transport)
    reader.start()
    peer.wait_connects(1)
    started = time.monotonic()
    reader.stop()
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"stop() waited {elapsed:.2f}s on a 100s backoff"
    assert _sse_threads() == []


def test_the_reader_does_not_reconnect_after_stop():
    peer = _Peer(_closed_stream(_epoch()))
    reader = SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=_Sink(),
                       idle_timeout_s=1.0, backoff_base_s=0.001,
                       backoff_cap_s=0.01, _transport=peer.transport)
    reader.start()
    peer.wait_connects(2)
    reader.stop()
    settled = peer.connects
    time.sleep(0.2)
    assert peer.connects == settled


# -- thread lifecycle ----------------------------------------------------


def test_stop_is_idempotent_and_joins_the_thread():
    peer = _Peer(_open_stream(HB))
    reader = SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=_Sink(),
                       idle_timeout_s=1.0, _transport=peer.transport)
    reader.start()
    peer.release()
    reader.stop()
    reader.stop()
    reader.stop()
    assert _sse_threads() == []


def test_stop_does_not_return_while_the_reader_is_still_live():
    """`stop()` joins, and the join is the whole contract: returning while the
    reader thread is still running leaves app code firing callbacks into a host
    that believes it has shut the channel down."""
    entered, release = threading.Event(), threading.Event()

    def on_event(_ev):
        entered.set()
        release.wait(10.0)

    peer = _Peer(_open_stream(_epoch(), _notify()))
    reader = SseReader(URL, API_KEY, on_event=on_event, on_epoch=_Sink(),
                       idle_timeout_s=1.0, producer_id="joinpin",
                       backoff_base_s=0.001, backoff_cap_s=0.01,
                       _transport=peer.transport)
    reader.start()
    try:
        assert entered.wait(10.0), "the callback never ran"
        peer.release()
        # The reader is parked inside app code and stays there for 0.3 s after
        # stop() is called. A join that does not wait returns before it exits.
        threading.Timer(0.3, release.set).start()
        reader.stop()
        assert _named_threads("msging-sse-joinpin") == [], \
            "stop() returned while the reader thread was still running"
    finally:
        release.set()
        peer.release()
        reader.stop()


def test_the_reader_thread_is_a_daemon():
    """A host that forgets `stop()` must still be able to exit. A non-daemon
    reader reconnects forever, so the interpreter would hang at shutdown."""
    peer = _Peer(_open_stream(_epoch()))
    epochs = _Sink()
    reader = SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=epochs,
                       idle_timeout_s=1.0, producer_id="daemonic",
                       _transport=peer.transport)
    reader.start()
    try:
        epochs.wait(1)
        threads = _named_threads("msging-sse-daemonic")
        assert len(threads) == 1, f"expected one reader, got {threads!r}"
        assert threads[0].daemon is True, "the reader thread is not a daemon"
    finally:
        peer.release()
        reader.stop()


def test_stop_before_start_is_a_no_op():
    reader = SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=_Sink(),
                       idle_timeout_s=1.0, _transport=_Peer().transport)
    reader.stop()
    assert reader.connected is False


def test_start_twice_spawns_one_reader():
    peer = _Peer(_open_stream(_epoch()))
    epochs = _Sink()
    reader = SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=epochs,
                       idle_timeout_s=1.0, producer_id="twice",
                       _transport=peer.transport)
    reader.start()
    reader.start()
    reader.start()
    try:
        epochs.wait(1)
        assert _sse_threads().count("msging-sse-twice") == 1
        assert peer.connects == 1
    finally:
        peer.release()
        reader.stop()


def test_the_thread_is_named_for_its_producer():
    peer = _Peer(_open_stream(_epoch()))
    epochs = _Sink()
    reader = SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=epochs,
                       idle_timeout_s=1.0, producer_id="prodA",
                       _transport=peer.transport)
    reader.start()
    try:
        epochs.wait(1)
        assert "msging-sse-prodA" in _sse_threads()
    finally:
        peer.release()
        reader.stop()


def test_a_reader_can_be_restarted():
    peer = _Peer(_open_stream(_epoch("a")), _open_stream(_epoch("b")))
    epochs = _Sink()
    reader = SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=epochs,
                       idle_timeout_s=1.0, _transport=peer.transport)
    reader.start()
    epochs.wait(1)
    peer.release()
    reader.stop()
    peer.hold.clear()
    reader.start()
    try:
        assert epochs.wait(2) == ["a", "b"]
    finally:
        peer.release()
        reader.stop()
    assert _sse_threads() == []


def test_connected_tracks_the_link():
    peer = _Peer(_open_stream(_epoch()))
    epochs = _Sink()
    reader = SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=epochs,
                       idle_timeout_s=1.0, _transport=peer.transport)
    assert reader.connected is False
    reader.start()
    epochs.wait(1)
    assert reader.connected is True
    peer.release()
    reader.stop()
    assert reader.connected is False


def test_stop_from_inside_a_callback_does_not_deadlock():
    """The callback runs on the reader thread; a `stop()` from there must not
    try to join the thread it is running on."""
    peer = _Peer(_open_stream(_epoch()))
    done = threading.Event()
    holder = {}

    def on_epoch(_epoch_value):
        holder["reader"].stop()
        done.set()

    reader = SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=on_epoch,
                       idle_timeout_s=1.0, backoff_base_s=0.001,
                       backoff_cap_s=0.01, _transport=peer.transport)
    holder["reader"] = reader
    reader.start()
    try:
        assert done.wait(10.0), "stop() from a callback deadlocked"
    finally:
        peer.release()
        reader.stop()
    assert _sse_threads() == []


# -- transport ownership -------------------------------------------------


def test_a_caller_supplied_transport_is_never_closed():
    """A long-lived pool handed in for reuse must survive every reconnect and
    the stop; the reader closes a `Client` on every attempt."""
    peer = _Peer(_closed_stream(_epoch()))
    reader = SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=_Sink(),
                       idle_timeout_s=1.0, backoff_base_s=0.001,
                       backoff_cap_s=0.01, _transport=peer.transport)
    reader.start()
    peer.wait_connects(3)
    reader.stop()
    assert peer.closes == 0


def test_a_retrying_transport_is_rejected():
    """Transport-level retries make N+1 silent connect attempts inside one of
    ours, defeating the jittered backoff that §4.3 leans on."""
    with pytest.raises(ValueError) as exc:
        SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=_Sink(),
                  idle_timeout_s=1.0,
                  _transport=httpx.HTTPTransport(retries=3))
    assert "retries" in str(exc.value)


def test_a_non_retrying_transport_is_accepted():
    SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=_Sink(),
              idle_timeout_s=1.0, _transport=httpx.HTTPTransport(retries=0))


# -- the request the reader makes ----------------------------------------


def test_the_request_carries_the_bearer_token_and_accepts_sse():
    peer = _Peer(_open_stream(_epoch()))
    epochs = _Sink()
    with _reader(peer, on_epoch=epochs):
        epochs.wait(1)
    request = peer.requests[0]
    assert request.method == "GET"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["accept"] == "text/event-stream"
    assert str(request.url) == URL


def test_notify_url_is_the_section_5_path():
    assert notify_url("https://ingest.prod:8443/", "ingest") == \
        "https://ingest.prod:8443/talium/ingest/v1/notify"


# -- callbacks are arbitrary app code ------------------------------------


def test_an_exception_in_on_event_does_not_kill_the_reader():
    class _Once:
        def __init__(self):
            self.sink = _Sink()

        def __call__(self, ev):
            self.sink(ev)
            if ev.msg_id == 1:
                raise RuntimeError("app blew up")

    cb = _Once()
    peer = _Peer(_open_stream(_epoch(), _notify(msg_id=1), _notify(msg_id=2),
                              _notify(msg_id=3)))
    with _reader(peer, on_event=cb):
        got = cb.sink.wait(3)
    assert [e.msg_id for e in got] == [1, 2, 3]


def test_an_exception_in_on_epoch_does_not_discard_the_frames_behind_it():
    """A sweep callback that raises -- disk full while moving `sent/` back to
    `pending/` -- must not tear the stream down.

    The frames are delivered in ONE socket read on purpose. If the exception
    escapes, it unwinds out of the parser's whole batch, so every notice that
    arrived alongside the epoch frame is dropped mid-list and is recoverable
    only via `sent_timeout_s`. The peer never repeats them: the second script
    is a live but silent stream, so a reconnect delivers nothing and the only
    way these two notices arrive is for the raising callback to be contained.
    """
    epochs = _Sink(raises=RuntimeError("sweep failed"))
    events = _Sink()
    batch = _epoch("boot-1") + _notify(msg_id=5) + _notify(msg_id=6)
    peer = _Peer(_open_stream(batch), _open_stream())
    with _reader(peer, on_event=events, on_epoch=epochs):
        assert [e.msg_id for e in events.wait(2)] == [5, 6]
    assert epochs.items == ["boot-1"]


def test_a_callback_that_always_raises_does_not_stop_the_stream():
    events = _Sink(raises=ValueError("always"))
    peer = _Peer(_open_stream(_epoch(), *[_notify(msg_id=i)
                                          for i in range(1, 11)]))
    with _reader(peer, on_event=events):
        assert len(events.wait(10)) == 10


# -- §10.4 logging -------------------------------------------------------


def _capture() -> io.StringIO:
    stream = io.StringIO()
    configure_logging(json=True,
                      logger_factory=structlog.PrintLoggerFactory(file=stream))
    return stream


def test_the_api_key_is_never_logged():
    out = _capture()
    peer = _Peer(_open_stream(_epoch(), _notify()))
    events = _Sink()
    with _reader(peer, on_event=events):
        events.wait(1)
    assert out.getvalue().strip(), "expected a log line"
    assert API_KEY not in out.getvalue()


def test_the_api_key_is_never_logged_on_failure():
    out = _capture()
    peer = _Peer(_refused, _status(401), _open_stream(_epoch()))
    epochs = _Sink()
    with _reader(peer, on_epoch=epochs):
        epochs.wait(1)
    assert out.getvalue().strip(), "expected a log line"
    assert API_KEY not in out.getvalue()


def test_the_notify_payload_is_never_logged():
    """§10.4: `reason` and `info` are server- and app-authored and can echo
    real user data. `msg_id` and `code` are what debugging needs."""
    out = _capture()
    peer = _Peer(_open_stream(_epoch(),
                              _notify(reason=SECRET, info={"ssn": SECRET})))
    events = _Sink()
    with _reader(peer, on_event=events):
        events.wait(1)
    assert SECRET not in out.getvalue()


def test_a_callback_exception_message_is_never_logged():
    """A handler's exception text is app data. Task 11 shipped one that
    carried an api_key and an SSN straight into a support bundle."""
    out = _capture()
    boom = _Sink(raises=RuntimeError(f"failed key={API_KEY} ssn={SECRET}"))
    peer = _Peer(_open_stream(_epoch(), _notify()))
    with _reader(peer, on_event=boom):
        boom.wait(1)
    text = out.getvalue()
    assert API_KEY not in text and SECRET not in text


def test_a_callback_exception_is_logged_by_type_and_location():
    out = _capture()
    boom = _Sink(raises=KeyError("k"))
    peer = _Peer(_open_stream(_epoch(), _notify()))
    with _reader(peer, on_event=boom):
        boom.wait(1)
    line = _find(out.getvalue(), "sse_callback_error")
    assert line is not None, "a callback failure must be visible"
    assert line["exc_type"] == "KeyError"
    assert line["callback"] == "on_event"
    assert "test_sse.py" in line["at"]


def test_sse_connected_reports_the_epoch_and_whether_it_changed():
    out = _capture()
    peer = _Peer(_open_stream(_epoch("boot-7")))
    epochs = _Sink()
    with _reader(peer, on_epoch=epochs, server_alias="prod"):
        epochs.wait(1)
    line = _find(out.getvalue(), "sse_connected")
    assert line is not None
    assert line["server_epoch"] == "boot-7"
    assert line["epoch_changed"] is True
    assert line["server_alias"] == "prod"


def test_sse_lost_is_logged_with_a_reason():
    out = _capture()
    peer = _Peer(_closed_stream(_epoch()), _open_stream(_epoch()))
    epochs = _Sink()
    with _reader(peer, on_epoch=epochs, server_alias="prod"):
        peer.wait_connects(2)
        epochs.wait(1)
    line = _find(out.getvalue(), "sse_lost")
    assert line is not None and line["server_alias"] == "prod"
    assert line["reason"]


def test_sse_lost_carries_the_fields_10_4_lists():
    """§10.4 was reconciled to the reader in this pass: `downtime_s` is not
    knowable when a link drops (the outage has just started), so it rides on
    the matching `sse_connected` and `sse_lost` carries `uptime_s`/`attempt`
    instead. Pinned so the drift cannot come back unrecorded."""
    out = _capture()
    peer = _Peer(_closed_stream(_epoch()), _open_stream(_epoch()))
    epochs = _Sink()
    with _reader(peer, on_epoch=epochs, server_alias="prod"):
        peer.wait_connects(2)
        epochs.wait(1)
    line = _find(out.getvalue(), "sse_lost")
    assert line is not None
    assert set(line) >= {"server_alias", "reason", "uptime_s", "attempt"}
    assert isinstance(line["uptime_s"], (int, float))
    assert isinstance(line["attempt"], int)


def test_epoch_sweep_names_the_epoch_that_caused_it():
    """§10.4's `swept` count belongs to whoever performs the sweep -- this
    module only reports the change, and `epoch_swept_msgs_total` (§10.5) is the
    sweeper's counter. The reader logs the epoch it saw instead."""
    out = _capture()
    peer = _Peer(_closed_stream(_epoch("boot-1")), _open_stream(_epoch("boot-2")))
    epochs = _Sink()
    with _reader(peer, on_epoch=epochs, server_alias="prod"):
        epochs.wait(2)
    line = _find(out.getvalue(), "epoch_sweep")
    assert line is not None
    assert line["server_epoch"] in ("boot-1", "boot-2")
    assert line["server_alias"] == "prod"


def test_sse_lost_is_logged_once_per_outage_even_without_an_epoch_frame():
    """`sse_lost` is once per *outage*, and an outage ends when the link comes
    back -- not when an epoch frame happens to parse.

    A peer whose epoch frame is malformed or absent otherwise logs `sse_lost`
    exactly once for the whole process lifetime, and the `downtime_s` on the
    next `sse_connected` is cumulative since the first-ever outage.
    """
    out = _capture()
    peer = _Peer(_closed_stream(HB), _closed_stream(HB),
                 _open_stream(_epoch("up")))
    epochs = _Sink()
    with _reader(peer, on_epoch=epochs, server_alias="prod"):
        epochs.wait(1)
    assert _count_events(out.getvalue(), "sse_lost") >= 2


def test_a_base_exception_from_a_callback_does_not_kill_the_reader_silently(
        monkeypatch):
    """`_safe_call` catches `Exception`; a `SystemExit` or `KeyboardInterrupt`
    out of app code is neither, and it unwinds the reader thread. That is
    allowed to be fatal -- it must not be *silent*: `connected` stuck False, no
    reconnect, and nothing in the log to say why outcomes stopped arriving.

    Type and location only, like every other callback failure (§10.4).
    """
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    out = _capture()
    boom = _Sink(raises=KeyboardInterrupt())
    peer = _Peer(_open_stream(_epoch(), _notify()))
    reader = SseReader(URL, API_KEY, on_event=boom, on_epoch=_Sink(),
                       idle_timeout_s=1.0, producer_id="dying",
                       backoff_base_s=0.001, backoff_cap_s=0.01,
                       _transport=peer.transport)
    reader.start()
    try:
        boom.wait(1)
        _eventually(lambda: _find(out.getvalue(), "sse_reader_died") is not None,
                    "the reader thread died without saying so")
    finally:
        peer.release()
        reader.stop()
    line = _find(out.getvalue(), "sse_reader_died")
    assert line["exc_type"] == "KeyboardInterrupt"
    assert "test_sse.py" in line["at"]
    assert API_KEY not in out.getvalue()
    assert reader.connected is False


# -- `_Decoder`: the content-encoding the reader did not ask for ---------


def test_the_decoder_passes_identity_through():
    assert b"".join(_Decoder("").feed(b"data: x\n\n")) == b"data: x\n\n"
    assert b"".join(_Decoder("identity").feed(b"y")) == b"y"


def test_the_decoder_undoes_deflate():
    """A proxy may compress even against `accept-encoding: identity`, and
    `deflate` is the other encoding httpx would have handled for us."""
    body = (_epoch("e1") + _notify(msg_id=3)).encode()
    raw = zlib.compress(body)
    assert b"".join(_Decoder("deflate").feed(raw)) == body


def test_the_decoder_undoes_gzip_in_bounded_steps():
    body = b"data: " + b"x" * (4 * _DECODE_CHUNK) + b"\n\n"
    obj = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    raw = obj.compress(body) + obj.flush()
    decoder = _Decoder("gzip")
    steps = list(decoder.feed(raw))
    assert b"".join(steps) == body
    assert all(len(s) <= _DECODE_CHUNK for s in steps), \
        f"a step handed back more than the cap: {[len(s) for s in steps]}"


def test_the_decoder_is_case_and_whitespace_insensitive():
    raw = zlib.compress(b"ok")
    assert b"".join(_Decoder("  DEFLATE ").feed(raw)) == b"ok"


def test_the_decoder_rejects_an_encoding_it_cannot_bound():
    """`br`/`zstd` have no bounded incremental decoder in the stdlib. Dropping
    the link is loud and safe; guessing is neither."""
    for enc in ("br", "zstd", "compress"):
        with pytest.raises(Exception) as exc:
            _Decoder(enc)
        assert enc in str(exc.value)


def test_the_decoder_never_echoes_a_long_encoding_name():
    """The header is peer-controlled and lands in a log line."""
    with pytest.raises(Exception) as exc:
        _Decoder("z" * 4096)
    assert len(str(exc.value)) < 200


def test_the_decoder_rejects_a_malformed_compressed_stream():
    with pytest.raises(Exception):
        list(_Decoder("gzip").feed(b"this is not gzip at all"))


def _br_encoded(conn, stop, n):
    conn.sendall(b"HTTP/1.1 200 OK\r\n"
                 b"Content-Type: text/event-stream\r\n"
                 b"Content-Encoding: br\r\n"
                 b"Connection: close\r\n\r\n")
    try:
        conn.sendall(b"\x1b\x0e\x00\xf8\x25" * 64)
        stop.wait(10.0)
    except OSError:
        return


def test_an_unsupported_encoding_drops_the_link_and_retries():
    """End to end: an encoding the reader cannot bound is a reconnect, not a
    crashed thread and not a silently mis-parsed stream."""
    with _RawSse(_br_encoded) as srv:
        reader = SseReader(srv.url, API_KEY, on_event=_Sink(),
                           on_epoch=_Sink(), idle_timeout_s=5.0,
                           backoff_base_s=0.01, backoff_cap_s=0.05)
        reader.start()
        try:
            srv.wait_connects(3)
        finally:
            reader.stop()
    assert _count("sse_reconnects_total") >= 2


def test_the_retry_check_is_shared_with_the_post_transport():
    """One rule, one implementation. `sse.py` already imports
    `_CallerOwnedTransport` from `transport.py`; a near-verbatim second copy of
    the retry check drifts the moment either message is edited."""
    import common.msging.clnt.sse as sse
    import common.msging.clnt.transport as transport
    assert sse._reject_retrying_transport is transport._reject_retrying_transport


def test_the_sse_rejection_message_names_its_own_reason():
    """Shared check, caller-specific reason: the POST path is §2.2's single
    attempt, the reader is §4.3's jittered reconnect."""
    with pytest.raises(ValueError) as exc:
        SseReader(URL, API_KEY, on_event=_Sink(), on_epoch=_Sink(),
                  idle_timeout_s=1.0, _transport=httpx.HTTPTransport(retries=3))
    assert "4.3" in str(exc.value) and "retries=3" in str(exc.value)


# -- helpers -------------------------------------------------------------


def _count(name: str) -> float:
    return sum(metrics().get(name, {}).values())


def _lines(out: str) -> list[dict]:
    got = []
    for raw in out.strip().splitlines():
        try:
            got.append(json.loads(raw))
        except ValueError:
            continue
    return got


def _find(out: str, event: str) -> dict | None:
    for line in _lines(out):
        if line.get("event") == event:
            return line
    return None


def _count_events(out: str, event: str) -> int:
    return sum(1 for line in _lines(out) if line.get("event") == event)
