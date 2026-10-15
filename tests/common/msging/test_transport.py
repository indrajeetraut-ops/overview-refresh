"""One POST, one answer (design §2.2). Every test here drives the real
`post_once` through an `httpx.MockTransport`, so "exactly one attempt" is
observable as a call count rather than asserted about the source.

The status -> Code expectations are §8.1's table, cross-checked against what
`srv/router.py` actually returns for `/event`: 202, 400, 401, 413, 503.

Three groups need real sockets rather than a mock, because what they are
about is not reachable through `MockTransport`:

  - the wall-clock deadline (§2.2's one `req_timeout_s`) -- only a peer that
    actually trickles bytes can show that a per-operation timeout is not a
    total one;
  - the response-size cap under `content-encoding` -- the decompressor is what
    has to be bounded, not the socket;
  - transport-level retries -- a mock has no connection pool to retry with.
"""
import gzip
import io
import json
import socket
import threading
import time
import tracemalloc
import zlib

import httpx
import pytest
import structlog

from common.msging.clnt.transport import _Deadline, _read_capped, post_once
from common.msging.codes import Code
from common.msging.log import configure_logging
from common.msging.metrics import metrics, reset_metrics
from common.msging.models import Envelope

PAYLOAD_MARKER = "payload-marker-must-never-be-logged"
API_KEY = "tk_live_" + "s" * 40


def setup_function():
    reset_metrics()


def _env() -> Envelope:
    return Envelope(producer_id="p", msg_id=1, service_name="ingest",
                    created_at="2026-07-25T10:15:30Z", payload={"x": 1})


def _post(handler, **kw):
    transport = httpx.MockTransport(handler)
    return post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
                     gzip_min_bytes=0, _transport=transport, **kw)


class _Counter:
    """Wraps a handler and records every request the transport is asked for."""

    def __init__(self, handler):
        self._handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    @property
    def n(self) -> int:
        return len(self.requests)


class _RecordingTransport(httpx.MockTransport):
    """A mock that remembers whether anyone closed it."""

    def __init__(self, handler):
        super().__init__(handler)
        self.closes = 0
        self.kwargs: dict = {}

    def close(self) -> None:
        self.closes += 1


class _RawServer:
    """A socket that speaks whatever bytes the test hands it, on its own thread.

    `httpx.MockTransport` short-circuits the network, so anything about
    *timing* on the wire has to go through a real socket.
    """

    def __init__(self, script):
        self._script = script
        self.stop = threading.Event()
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _serve(self):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        try:
            conn.recv(1 << 16)
            self._script(conn, self.stop)
        except OSError:
            pass
        finally:
            conn.close()

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self._sock.close()
        self._thread.join(5.0)
        return False


def _trickle(head: bytes, chunk: bytes, every_s: float = 0.4):
    """A peer that answers, then dribbles -- one chunk per `every_s`, forever."""
    def script(conn, stop):
        conn.sendall(head)
        while not stop.is_set():
            conn.sendall(chunk)
            time.sleep(every_s)
    return script


# -- §8.1 status -> code -------------------------------------------------


def test_202_maps_to_ok():
    assert _post(lambda r: httpx.Response(202)).code is Code.OK


def test_401_maps_to_unauthorized():
    result = _post(lambda r: httpx.Response(401, json={"reason": "revoked"}))
    assert result.code is Code.UNAUTHORIZED


def test_400_maps_to_rejected():
    result = _post(lambda r: httpx.Response(400, json={"reason": "bad"}))
    assert result.code is Code.REJECTED


def test_413_maps_to_too_large():
    result = _post(lambda r: httpx.Response(413, json={"code": "too_large"}))
    assert result.code is Code.TOO_LARGE


def test_503_maps_to_server_busy():
    result = _post(lambda r: httpx.Response(503, json={"code": "server_busy"},
                                            headers={"Retry-After": "5"}))
    assert result.code is Code.SERVER_BUSY


def test_429_maps_to_server_busy():
    """A rate-limited server is up, not unreachable. §8.1 has no `throttled`
    row, and `server_busy` is its nearest neighbour -- same state, same
    handling, same `Retry-After`. Labelling it `stored_unreachable` would send
    an operator hunting a network fault that does not exist."""
    result = _post(lambda r: httpx.Response(429, headers={"Retry-After": "2"}))
    assert result.code is Code.SERVER_BUSY
    assert result.retry_after_s == 2.0


def test_200_maps_to_already_processed():
    """§8.1's table. v1's server never returns 200 from /event, but the mapping
    is the contract and §9.1 is what would start using it."""
    result = _post(lambda r: httpx.Response(200, json={"code": "already_processed"}))
    assert result.code is Code.ALREADY_PROCESSED


def test_every_mapped_status_is_distinct():
    """Guards the whole table at once: a mapping collapsed to a single code
    (e.g. everything -> ok) cannot survive this."""
    seen = {}
    for status in (200, 202, 400, 401, 413, 429, 503):
        seen[status] = _post(lambda r, s=status: httpx.Response(s)).code
    assert seen == {
        200: Code.ALREADY_PROCESSED,
        202: Code.OK,
        400: Code.REJECTED,
        401: Code.UNAUTHORIZED,
        413: Code.TOO_LARGE,
        429: Code.SERVER_BUSY,
        503: Code.SERVER_BUSY,
    }


def test_unexpected_5xx_is_stored_not_rejected():
    """A 500/502 from the server or a gateway is not a verdict on the msg. It
    must leave the record retryable, never NACK it."""
    for status in (500, 502, 504):
        result = _post(lambda r, s=status: httpx.Response(s, text="gateway"))
        assert result.code is Code.SERVER_BUSY, status


def test_unexpected_4xx_is_stored_not_rejected():
    """404/405 means misrouted, not malformed: the envelope was never judged,
    so the msg stays PENDING rather than becoming a NACK the app must resolve."""
    for status in (404, 405, 451):
        result = _post(lambda r, s=status: httpx.Response(s, text="nope"))
        assert result.code is Code.STORED_UNREACHABLE, status


def test_reason_is_carried_from_the_body():
    result = _post(lambda r: httpx.Response(400, json={"code": "rejected",
                                                       "reason": "payload: missing"}))
    assert result.code is Code.REJECTED
    assert result.reason == "payload: missing"


def test_ok_reason_is_empty():
    result = _post(lambda r: httpx.Response(202, json={"code": "ok", "reason": "",
                                                       "msg_id": 1}))
    assert result.code is Code.OK
    assert result.reason == ""


def test_a_bodyless_failure_reasons_with_its_status():
    """With no `reason` in the body there is still something an operator can
    grep for, and it has to name the status -- `reason` is the only free-form
    field §8 gives the log line."""
    for status in (500, 404):
        result = _post(lambda r, s=status: httpx.Response(s))
        assert result.reason == f"http {status}", status


def test_a_long_reason_is_truncated():
    """`reason` ends up in logs; a server (or something in front of it) must
    not be able to size those lines. 2 KiB fits inside the response read cap,
    so this exercises the truncation itself, not the cap."""
    result = _post(lambda r: httpx.Response(400, json={"reason": "x" * 2000}))
    assert result.code is Code.REJECTED
    assert 0 < len(result.reason) <= 512
    assert result.reason.startswith("x")


def test_a_huge_response_body_is_not_read_whole():
    """A megabyte of body from a broken or hostile peer must not become a
    megabyte in this process, and must still yield a usable code."""
    result = _post(lambda r: httpx.Response(413, json={"code": "too_large",
                                                       "reason": "x" * 1_000_000}))
    assert result.code is Code.TOO_LARGE
    assert len(result.reason) <= 512


# -- the response read cap (§ module contract) ---------------------------


def _streamed(status: int, chunks, headers=None) -> httpx.Response:
    return httpx.Response(status, content=chunks, headers=headers or {})


def test_read_capped_stops_pulling_at_the_cap():
    """The cap is on *bytes pulled off the socket*, not just on what is
    returned. A 300 KB body must stop being read after ~8 KiB, and the bytes
    handed back must be exactly the cap -- not the overshoot of the last
    chunk. The existing `len(reason) <= 512` assertion cannot see either of
    those: reason truncation alone satisfies it."""
    pulled = 0

    def oversized():
        nonlocal pulled
        for _ in range(100):
            pulled += 1
            yield b"x" * 3000        # deliberately not a divisor of 8192

    raw = _read_capped(_streamed(413, oversized()), _Deadline(30.0))
    assert len(raw) == 8192, "the returned body is not clipped to the cap"
    assert pulled == 3, f"kept reading past the cap: {pulled} chunks pulled"


def test_read_capped_returns_a_short_body_whole():
    raw = _read_capped(_streamed(400, iter([b'{"reason": "hi"}'])), _Deadline(30.0))
    assert raw == b'{"reason": "hi"}'


def test_a_compressed_response_is_still_decoded():
    body = gzip.compress(json.dumps({"reason": "compressed but readable"}).encode())
    result = _post(lambda r: httpx.Response(400, content=body,
                                            headers={"content-encoding": "gzip"}))
    assert result.code is Code.REJECTED
    assert result.reason == "compressed but readable"


def test_a_gzip_bomb_response_cannot_balloon_this_process():
    """The module promises a broken or hostile peer cannot stream megabytes
    into the process. `iter_bytes()` breaks that promise: it decodes a whole
    64 KiB raw chunk *before* yielding, so the 8 KiB cap is consulted only
    after the expansion -- measured at a 151 MB peak from a 194 KB response.

    A 200 MB-of-zeros body, compressed, in 64 KiB wire chunks.
    """
    comp = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    blob = bytearray()
    for _ in range(200):
        blob += comp.compress(b"\0" * 1_000_000)
    blob += comp.flush()
    wire = bytes(blob)
    assert len(wire) < 1_000_000, "the test's own payload must stay small"

    def chunks():
        for i in range(0, len(wire), 65536):
            yield wire[i:i + 65536]

    def handler(request):
        return httpx.Response(413, content=chunks(),
                              headers={"content-encoding": "gzip"})

    transport = httpx.MockTransport(handler)
    tracemalloc.start()
    try:
        result = post_once("http://h", "ingest", "k", _env(), timeout_s=5.0,
                           gzip_min_bytes=0, _transport=transport)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result.code is Code.TOO_LARGE
    assert peak < 4 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB for an 8 KiB cap"


# -- §2.2's one req_timeout_s, as a wall clock ---------------------------


def test_a_trickling_body_cannot_outlast_the_deadline():
    """`httpx.Timeout` arms every *operation*; §2.2 bounds the whole *call*.

    A peer emitting 40 bytes just inside the read timeout resets that timeout
    forever, so the read never expires: with `timeout_s=1.0` the unfixed code
    ran for 16 s against a 100 KB trickle and did not finish at all against an
    endless one. §6.4 runs `send()` on the producer's own thread, so that is
    the application stopping.
    """
    head = b"HTTP/1.1 202 Accepted\r\nContent-Length: 100000\r\n\r\n"
    with _RawServer(_trickle(head, b"x" * 40)) as srv:
        started = time.monotonic()
        result = post_once(srv.base_url, "ingest", "k", _env(), timeout_s=1.0,
                           gzip_min_bytes=0)
        elapsed = time.monotonic() - started
    assert elapsed < 4.0, f"post_once ran for {elapsed:.2f}s under a 1.0s timeout"
    assert result.code is Code.OK


def test_trickling_response_headers_cannot_outlast_the_deadline():
    """Same defect one phase earlier: a peer dribbling header lines never lets
    the header read expire either, and there `post_once` has no status to fall
    back on. It has to come back `stored_timeout`, not hang."""
    head = b"HTTP/1.1 202 Accepted\r\n"
    with _RawServer(_trickle(head, b"X-Pad: y\r\n")) as srv:
        started = time.monotonic()
        result = post_once(srv.base_url, "ingest", "k", _env(), timeout_s=1.0,
                           gzip_min_bytes=0)
        elapsed = time.monotonic() - started
    assert elapsed < 4.0, f"post_once ran for {elapsed:.2f}s under a 1.0s timeout"
    assert result.code is Code.STORED_TIMEOUT


def test_a_202_survives_a_body_read_that_times_out():
    """§2.2 makes the status line the accept signal, and the body is optional
    decoration. A 202 whose body never arrives means the server *took* the
    msg; reporting `stored_timeout` leaves the record PENDING, the cleaner loop
    resends it, and with no server-side dedup in v1 (§9.1) the app gets a
    guaranteed duplicate. The failed read may cost the `reason`, nothing more.
    """
    def handler(request):
        def stalls():
            raise httpx.ReadTimeout("body stalled", request=request)
            yield b""                                   # pragma: no cover
        return httpx.Response(202, headers={"content-length": "100"},
                              content=stalls())

    result = _post(handler)
    assert result.code is Code.OK, "a status already in hand was thrown away"
    assert "ReadTimeout" in result.reason


def test_a_rejection_survives_a_body_read_that_times_out():
    """The same rule the other way round: a 400 is a verdict whether or not
    the explanation arrives. Downgrading it to `stored_timeout` would retry a
    msg the server has already refused."""
    def handler(request):
        def stalls():
            raise httpx.ReadTimeout("body stalled", request=request)
            yield b""                                   # pragma: no cover
        return httpx.Response(400, headers={"content-length": "100"},
                              content=stalls())

    result = _post(handler)
    assert result.code is Code.REJECTED


def test_the_configured_timeout_reaches_the_client():
    """`req_timeout_s` is config (§5); it must arrive on the request rather
    than httpx's five-second default."""
    seen = {}

    def handler(request):
        seen.update(request.extensions["timeout"])
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    post_once("http://h", "ingest", "k", _env(), timeout_s=7.5,
              gzip_min_bytes=0, _transport=transport)
    assert seen == {"connect": 7.5, "read": 7.5, "write": 7.5, "pool": 7.5}


# -- Retry-After (§8.1) --------------------------------------------------


def test_retry_after_seconds_is_surfaced():
    """§8.1: `server_busy` retries *after Retry-After*, and `post_once` is the
    only layer that ever sees the header. Dropping it here means the cleaner
    loop (§4.4) can never honour it."""
    result = _post(lambda r: httpx.Response(503, headers={"Retry-After": "5"}))
    assert result.code is Code.SERVER_BUSY
    assert result.retry_after_s == 5.0


def test_retry_after_http_date_is_surfaced_as_seconds():
    """RFC 7231 allows a date, and a proxy in front of the server may well
    send one. The loop wants seconds either way."""
    when = time.strftime("%a, %d %b %Y %H:%M:%S GMT",
                         time.gmtime(time.time() + 30))
    result = _post(lambda r: httpx.Response(503, headers={"Retry-After": when}))
    assert result.retry_after_s is not None
    assert 20.0 <= result.retry_after_s <= 40.0


def test_retry_after_is_none_when_absent_or_unusable():
    assert _post(lambda r: httpx.Response(503)).retry_after_s is None
    assert _post(lambda r: httpx.Response(
        503, headers={"Retry-After": "soon"})).retry_after_s is None
    assert _post(lambda r: httpx.Response(
        503, headers={"Retry-After": "nan"})).retry_after_s is None


def test_a_retry_after_in_the_past_clamps_to_zero():
    when = time.strftime("%a, %d %b %Y %H:%M:%S GMT",
                         time.gmtime(time.time() - 600))
    assert _post(lambda r: httpx.Response(
        503, headers={"Retry-After": when})).retry_after_s == 0.0


def test_retry_after_is_none_on_a_transport_failure():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)
    assert _post(boom).retry_after_s is None


# -- transport failures --------------------------------------------------


def test_timeout_maps_to_stored_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)
    assert _post(handler).code is Code.STORED_TIMEOUT


def test_connect_timeout_maps_to_stored_timeout():
    def handler(request):
        raise httpx.ConnectTimeout("slow", request=request)
    assert _post(handler).code is Code.STORED_TIMEOUT


def test_connection_error_maps_to_stored_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)
    assert _post(handler).code is Code.STORED_UNREACHABLE


def test_protocol_error_maps_to_stored_unreachable():
    def handler(request):
        raise httpx.RemoteProtocolError("half a response", request=request)
    assert _post(handler).code is Code.STORED_UNREACHABLE


def test_a_malformed_base_url_never_escapes_as_an_exception():
    """§8.3 keeps raises for programming errors; a mis-set base_path is a
    runtime condition and must come back as a code, not blow up send()."""
    result = post_once("not a url", "ingest", "k", _env(), timeout_s=1.0,
                       gzip_min_bytes=0)
    assert result.code is Code.STORED_UNREACHABLE


def test_an_unparseable_base_url_never_escapes_as_an_exception():
    """`InvalidURL` is not an `HTTPError`, so it needs its own arm of the
    except. A bad port is what actually reaches it -- `"not a url"` above
    raises `UnsupportedProtocol`, which is an `HTTPError` and takes the other
    path."""
    with pytest.raises(httpx.InvalidURL):
        httpx.URL("http://[::1")                # the branch really is reachable
    result = post_once("http://[::1", "ingest", "k", _env(), timeout_s=1.0,
                       gzip_min_bytes=0)
    assert result.code is Code.STORED_UNREACHABLE


# -- exactly one attempt (§2.2) ------------------------------------------


def test_success_makes_exactly_one_request():
    counter = _Counter(lambda r: httpx.Response(202))
    _post(counter)
    assert counter.n == 1


def test_failure_status_is_not_retried():
    for status in (400, 401, 413, 500, 503):
        counter = _Counter(lambda r, s=status: httpx.Response(s))
        _post(counter)
        assert counter.n == 1, status


def test_transport_error_is_not_retried():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)
    counter = _Counter(boom)
    _post(counter)
    assert counter.n == 1


def test_timeout_is_not_retried():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    counter = _Counter(slow)
    _post(counter)
    assert counter.n == 1


def test_a_redirect_is_not_followed():
    """Following a 3xx would turn one attempt into two POSTs -- and a resend
    the cleaner loop never authorised."""
    counter = _Counter(lambda r: httpx.Response(
        307, headers={"Location": "http://h/talium/ingest/v1/event2"}))
    result = _post(counter)
    assert counter.n == 1
    assert result.code is Code.STORED_UNREACHABLE


def test_the_built_in_transport_disables_httpx_level_retries():
    """httpx's own pool retries connect failures. That is a second attempt
    `post_once` is not allowed to make (§2.2) and it multiplies the connect
    phase by `retries + 1` timeouts, so the pool this function builds must
    pin `retries=0`."""
    seen = {}
    real = httpx.HTTPTransport

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    original = httpx.HTTPTransport
    httpx.HTTPTransport = spy
    try:
        post_once("http://127.0.0.1:9", "ingest", "k", _env(), timeout_s=0.5,
                  gzip_min_bytes=0)
    finally:
        httpx.HTTPTransport = original
    assert seen.get("retries") == 0, f"built the pool with {seen!r}"


def test_a_caller_transport_with_retries_is_rejected():
    """The docstring invites a long-lived pool for connection reuse; nothing
    stopped that pool having `retries=3`, which silently makes 4 connect
    attempts and stretches the call to 4 x `req_timeout_s`. Config error, so
    it raises (§8.3) rather than quietly breaking both §2.2 guarantees."""
    with pytest.raises(ValueError, match="retries"):
        post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
                  gzip_min_bytes=0, _transport=httpx.HTTPTransport(retries=3))


def test_a_caller_transport_without_retries_is_accepted():
    pool = httpx.HTTPTransport(retries=0)
    try:
        result = post_once("http://127.0.0.1:9", "ingest", "k", _env(),
                           timeout_s=0.5, gzip_min_bytes=0, _transport=pool)
        assert result.code is Code.STORED_UNREACHABLE
    finally:
        pool.close()


def test_transport_is_keyword_only():
    """`attempts` is already keyword-only; `_transport` sitting positionally
    right after `gzip_min_bytes` is how a stray argument becomes a transport."""
    with pytest.raises(TypeError):
        post_once("http://h", "ingest", "k", _env(), 1.0, 0,
                  httpx.MockTransport(lambda r: httpx.Response(202)))


# -- who closes what -----------------------------------------------------


def test_a_self_built_transport_is_closed():
    """The pool this function builds is this function's to clean up. Not
    closing it leaks a connection pool -- and its sockets -- per send."""
    recorder = _RecordingTransport(lambda r: httpx.Response(202))
    original = httpx.HTTPTransport
    httpx.HTTPTransport = lambda **kw: recorder
    try:
        assert post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
                         gzip_min_bytes=0).code is Code.OK
    finally:
        httpx.HTTPTransport = original
    assert recorder.closes >= 1, "the transport post_once built was never closed"


def test_a_caller_supplied_transport_is_never_closed():
    """A caller passes a long-lived pool precisely so it survives the send.
    Closing it here would break every subsequent send through it."""
    recorder = _RecordingTransport(lambda r: httpx.Response(202))
    post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
              gzip_min_bytes=0, _transport=recorder)
    assert recorder.closes == 0, "post_once closed a pool it does not own"


def test_a_caller_supplied_transport_still_works_for_the_next_send():
    """The property behind the previous test, observed end to end."""
    counter = _Counter(lambda r: httpx.Response(202))
    pool = httpx.MockTransport(counter)
    for _ in range(3):
        result = post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
                           gzip_min_bytes=0, _transport=pool)
        assert result.code is Code.OK
    assert counter.n == 3


def test_a_timed_out_send_still_closes_its_transport():
    """The leak that matters is on the failure path, which is the one that
    repeats during an outage."""
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    recorder = _RecordingTransport(boom)
    original = httpx.HTTPTransport
    httpx.HTTPTransport = lambda **kw: recorder
    try:
        for _ in range(5):
            post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
                      gzip_min_bytes=0)
    finally:
        httpx.HTTPTransport = original
    assert recorder.closes == 5


# -- request shape -------------------------------------------------------


def test_posts_to_the_event_path():
    counter = _Counter(lambda r: httpx.Response(202))
    _post(counter)
    request = counter.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://h/talium/ingest/v1/event"


def test_trailing_slash_on_the_base_url_does_not_double_up():
    counter = _Counter(lambda r: httpx.Response(202))
    transport = httpx.MockTransport(counter)
    post_once("http://h/", "ingest", "k", _env(), timeout_s=1.0,
              gzip_min_bytes=0, _transport=transport)
    assert str(counter.requests[0].url) == "http://h/talium/ingest/v1/event"


def test_bearer_token_is_sent():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    post_once("http://h", "ingest", "secret", _env(), timeout_s=1.0,
              gzip_min_bytes=1 << 30, _transport=transport)
    assert seen["auth"] == "Bearer secret"


def test_content_type_is_json_when_gzipped():
    counter = _Counter(lambda r: httpx.Response(202))
    _post(counter)
    assert counter.requests[0].headers["content-type"] == "application/json"
    assert counter.requests[0].headers["content-encoding"] == "gzip"


def test_content_type_is_json_when_not_gzipped():
    """The plain branch builds its own header dict, so it needs its own test --
    the gzip one above cannot vouch for it."""
    counter = _Counter(lambda r: httpx.Response(202))
    transport = httpx.MockTransport(counter)
    post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
              gzip_min_bytes=1 << 30, _transport=transport)
    assert counter.requests[0].headers["content-type"] == "application/json"
    assert counter.requests[0].headers.get("content-encoding") is None


# -- gzip (§1) -----------------------------------------------------------


def test_body_is_gzipped_above_the_threshold():
    seen = {}

    def handler(request):
        seen["encoding"] = request.headers.get("content-encoding")
        return httpx.Response(202)

    transport = httpx.MockTransport(handler)
    post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
              gzip_min_bytes=0, _transport=transport)
    assert seen["encoding"] == "gzip"


def test_body_is_plain_below_the_threshold():
    """Below `gzip_min_bytes` gzip costs more than it saves (§5.1), and the
    body must then be readable JSON with no content-encoding at all."""
    counter = _Counter(lambda r: httpx.Response(202))
    transport = httpx.MockTransport(counter)
    post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
              gzip_min_bytes=1 << 30, _transport=transport)
    request = counter.requests[0]
    assert request.headers.get("content-encoding") is None
    assert json.loads(request.content)["msg_id"] == 1


def test_gzip_threshold_is_the_envelope_size_not_a_constant():
    """One byte either side of the real body size flips the decision."""
    size = len(_env().model_dump_json().encode("utf-8"))
    counter = _Counter(lambda r: httpx.Response(202))
    transport = httpx.MockTransport(counter)
    post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
              gzip_min_bytes=size, _transport=transport)
    post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
              gzip_min_bytes=size + 1, _transport=transport)
    assert counter.requests[0].headers.get("content-encoding") == "gzip"
    assert counter.requests[1].headers.get("content-encoding") is None


def test_gzip_body_is_exactly_one_complete_member():
    """`srv/router.py` rejects both truncated and multi-member gzip, so the
    client must emit exactly one well-formed member -- decompressed here the
    same way the server does it."""
    counter = _Counter(lambda r: httpx.Response(202))
    transport = httpx.MockTransport(counter)
    post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
              gzip_min_bytes=0, _transport=transport)
    wire = counter.requests[0].content

    obj = zlib.decompressobj(16 + zlib.MAX_WBITS)
    raw = obj.decompress(wire)
    assert obj.eof, "truncated gzip stream"
    assert obj.unused_data == b"", "more than one gzip member"
    assert json.loads(raw) == json.loads(_env().model_dump_json())


def test_gzipped_body_declares_its_compressed_length():
    """The server caps *wire* bytes as well as decompressed ones; a
    content-length describing the uncompressed size would be a lie."""
    counter = _Counter(lambda r: httpx.Response(202))
    transport = httpx.MockTransport(counter)
    post_once("http://h", "ingest", "k", _env(), timeout_s=1.0,
              gzip_min_bytes=0, _transport=transport)
    request = counter.requests[0]
    assert int(request.headers["content-length"]) == len(request.content)


def test_the_whole_envelope_is_sent_not_just_the_payload():
    """A resend has to rebuild the request verbatim (§3), so event_id, dedup
    and info travel too."""
    env = Envelope(producer_id="p", msg_id=7, service_name="ingest",
                   created_at="2026-07-25T10:15:30Z", event_id="e-1", dedup=True,
                   info={"k": "v"}, payload={"x": 1})
    counter = _Counter(lambda r: httpx.Response(202))
    transport = httpx.MockTransport(counter)
    post_once("http://h", "ingest", "k", env, timeout_s=1.0,
              gzip_min_bytes=1 << 30, _transport=transport)
    body = json.loads(counter.requests[0].content)
    assert body["event_id"] == "e-1" and body["dedup"] is True
    assert body["info"] == {"k": "v"} and body["payload"] == {"x": 1}
    assert body["msg_id"] == 7 and body["schema_version"] == "1.0"


# -- §10.4 logging -------------------------------------------------------


def _post_capturing_logs(handler, api_key=API_KEY, gzip_min_bytes=1 << 30,
                         payload=None):
    stream = io.StringIO()
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=stream))
    env = Envelope(producer_id="p", msg_id=9, service_name="ingest",
                   created_at="2026-07-25T10:15:30Z",
                   payload=payload if payload is not None else {"secret": PAYLOAD_MARKER})
    transport = httpx.MockTransport(handler)
    result = post_once("http://h", "ingest", api_key, env, timeout_s=1.0,
                       gzip_min_bytes=gzip_min_bytes, _transport=transport)
    return result, stream.getvalue()


def _last_line(out: str) -> dict:
    return json.loads(out.strip().splitlines()[-1])


def test_the_api_key_is_never_logged():
    _, out = _post_capturing_logs(lambda r: httpx.Response(202))
    assert out.strip(), "expected a log line"
    assert API_KEY not in out


def test_the_api_key_is_never_logged_on_failure():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)
    _, out = _post_capturing_logs(boom)
    assert out.strip(), "expected a log line"
    assert API_KEY not in out


def test_the_payload_is_never_logged():
    _, out = _post_capturing_logs(lambda r: httpx.Response(202))
    assert PAYLOAD_MARKER not in out


def test_the_payload_is_never_logged_on_a_rejection():
    _, out = _post_capturing_logs(
        lambda r: httpx.Response(400, json={"reason": "payload.secret: string_type"}))
    assert PAYLOAD_MARKER not in out


def test_the_log_line_carries_msg_id_size_and_err_code():
    """`size_bytes` is what debugging needs in place of the payload (§10.4)."""
    _, out = _post_capturing_logs(lambda r: httpx.Response(202))
    line = _last_line(out)
    assert line["event"] == "msg_sent"
    assert line["level"] == "info"
    assert line["msg_id"] == 9
    assert line["err_code"] == "ok"
    assert line["producer_id"] == "p"
    assert isinstance(line["size_bytes"], int) and line["size_bytes"] > 0
    assert isinstance(line["latency_ms"], (int, float))


def test_size_bytes_is_the_envelope_and_wire_bytes_is_what_went_out():
    """Two different numbers doing two different jobs (§10.4): `max_msg_bytes`
    is measured against the envelope, the server's body cap against the wire.
    Every other logging test pins `gzip_min_bytes` above the threshold, where
    they happen to be equal and either could stand in for the other."""
    _, out = _post_capturing_logs(lambda r: httpx.Response(202), gzip_min_bytes=0,
                                  payload={"filler": "compress-me " * 200})
    line = _last_line(out)
    assert line["gzipped"] is True
    assert line["wire_bytes"] < line["size_bytes"]
    assert line["size_bytes"] > 2000


def test_wire_bytes_equals_size_bytes_when_the_body_is_not_gzipped():
    _, out = _post_capturing_logs(lambda r: httpx.Response(202))
    line = _last_line(out)
    assert line["gzipped"] is False
    assert line["wire_bytes"] == line["size_bytes"]


def test_retry_after_is_logged_when_the_server_sends_one():
    _, out = _post_capturing_logs(
        lambda r: httpx.Response(503, headers={"Retry-After": "5"}))
    assert _last_line(out)["retry_after_s"] == 5.0


def test_a_failed_send_is_logged_loudly():
    """An `ok` and a `stored_unreachable` must not read the same in a support
    bundle -- the failure is the line an operator is grepping for."""
    def boom(request):
        raise httpx.ConnectError("refused", request=request)
    _, out = _post_capturing_logs(boom)
    line = _last_line(out)
    assert line["event"] == "msg_sent"
    assert line["level"] == "warning"
    assert line["err_code"] == "stored_unreachable"
    assert line["status"] is None
    assert line["reason"]


def test_attempts_is_logged_when_the_caller_supplies_it():
    """§10.4 lists `attempts` on `msg_sent`. One POST cannot know it, so the
    send path passes it down rather than emitting a second line."""
    transport = httpx.MockTransport(lambda r: httpx.Response(202))
    stream = io.StringIO()
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=stream))
    post_once("http://h", "ingest", "k", _env(), timeout_s=1.0, gzip_min_bytes=0,
              _transport=transport, attempts=3)
    assert _last_line(stream.getvalue())["attempts"] == 3


# -- §10.5 metrics -------------------------------------------------------


def test_metrics_count_the_send_by_err_code():
    _post(lambda r: httpx.Response(202))
    _post(lambda r: httpx.Response(503))
    snap = metrics()
    assert snap["msgs_sent_total"]['{"err_code":"ok"}'] == 1
    assert snap["msgs_sent_total"]['{"err_code":"server_busy"}'] == 1
    assert snap["send_latency_ms"]["{}"]["count"] == 2


def test_metrics_count_a_transport_failure_too():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)
    _post(boom)
    snap = metrics()
    assert snap["msgs_sent_total"]['{"err_code":"stored_unreachable"}'] == 1


@pytest.mark.parametrize("status", [202, 400, 401, 413, 429, 503])
def test_a_code_is_always_returned_never_an_exception(status):
    result = _post(lambda r: httpx.Response(status))
    assert isinstance(result.code, Code)
    assert isinstance(result.reason, str)
    assert result.retry_after_s is None or isinstance(result.retry_after_s, float)
