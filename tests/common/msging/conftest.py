"""Shared fixtures for common.msging tests.

Everything the client-facade tests need is built against an
`httpx.MockTransport` standing in for a whole server: `/register`, `/event`,
`/notify` and `/close`. No live server, no sockets, no sleeps waiting on one.

The mock is deliberately a *server*, not a stub per endpoint -- the facade
drives all four paths and the tests assert on what actually went over the wire
(`MockServer.envelopes`) and on how many times each path was hit
(`MockServer.calls`, which is what pins §2.2's "exactly one POST").
"""
from __future__ import annotations

import gzip
import json
import time

import httpx
import pytest

from common.msging import clnt
from common.msging.models import Envelope

#: Long enough that `redact_key` returns a prefix rather than the opaque
#: marker, so a test asserting the *whole* key never reaches a log line is
#: actually testing something.
API_KEY = "test-api-key-0123456789-abcdefghij"

DEFAULT_MAX_MSG_BYTES = 33554432


@pytest.fixture(autouse=True)
def _client_state_is_per_test():
    """Module-level client state is process-wide (§6.1 is module functions),
    so it has to be torn down between tests or a leaked cleaner/SSE thread
    keeps sweeping another test's store."""
    yield
    clnt._reset_for_tests()


def _body(request: httpx.Request) -> bytes:
    raw = request.content
    if request.headers.get("content-encoding") == "gzip":
        return gzip.decompress(raw)
    return raw


class MockServer:
    """One `httpx.MockTransport` answering every §5 path.

    Args:
        max_msg_bytes: what `/register` reports back as the server's limit
            (§6.1: "the response carries the server's limits so client and
            server configs cannot drift").
        heartbeat_s: what `/register` reports back as the server's SSE
            keepalive interval (§4.2), the other half of that same promise.
            The default is deliberately well under `write_cfg`'s
            `idle_timeout_s: 5` so the ordinary fixture is *not* itself a
            drifted pair -- §4.2 wants the idle timer at ~3x the heartbeat.
            A test that wants the drift (a server that talks less often than
            the client is willing to wait) passes a bigger one.
        event_status: status for `/event`. 202 by default; set 503/401/413 to
            drive §8.1's other branches.
        event_error: an exception factory raised instead of answering, for the
            timeout/unreachable paths.
        notify_status: status for the SSE `GET /notify`.
        event_delay_s: seconds `/event` blocks before answering. This is how a
            test gets a *slow* server without a socket -- the deadline
            behaviour of `close(timeout_s)` is only visible when one POST costs
            real time.
        register_status / register_body: drive `/register`'s failure paths
            (§10.1: a refused enrollment is terminal). `register_body=...` with
            a non-dict sends it as raw text, which is the malformed-body case.
        register_once: serve `register_status`/`register_body` for the FIRST
            `/register` only, then go back to issuing identities. This is what
            makes "did not follow the redirect" testable: a 3xx whose target
            answers 404 looks the same as a refusal, so the target has to
            *succeed* -- and then only a client that refused the redirect
            still raises.
    """

    def __init__(self, *, max_msg_bytes: int = DEFAULT_MAX_MSG_BYTES,
                 event_status: int = 202, event_error=None,
                 notify_status: int = 200, id_prefix: str = "prod-test",
                 notify_frames: list[str] | None = None,
                 event_delay_s: float = 0.0,
                 register_status: int = 200,
                 register_body=None,
                 register_headers: dict | None = None,
                 heartbeat_s: float = 1.5,
                 register_once: bool = False) -> None:
        self.register_once = register_once
        self.max_msg_bytes = max_msg_bytes
        self.heartbeat_s = heartbeat_s
        self.id_prefix = id_prefix
        self.event_delay_s = event_delay_s
        self.register_status = register_status
        self.register_body = register_body
        self.register_headers = register_headers
        # What `GET /notify` streams. The default is a bare keepalive, so no
        # test gets an epoch frame -- and therefore a §4.3 sweep -- it did not
        # ask for. A buffered mock body ends as soon as it is read, so the
        # reader replays these on every reconnect; outcomes are idempotent.
        self.notify_frames = list(notify_frames or [": hb\n\n"])
        self.event_status = event_status
        self.event_error = event_error
        self.notify_status = notify_status
        self.envelopes: list[Envelope] = []
        self.registrations: list[dict] = []
        self.closes: list[dict] = []
        self.calls: dict[str, int] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        name = path.rsplit("/", 1)[-1]
        self.calls[name] = self.calls.get(name, 0) + 1
        if name == "register":
            self.registrations.append(json.loads(_body(request) or b"{}"))
            if self.register_status != 200 or self.register_body is not None:
                kw = ({"json": self.register_body}
                      if isinstance(self.register_body, (dict, list))
                      else {"text": str(self.register_body or "")})
                response = httpx.Response(self.register_status,
                                          headers=self.register_headers or {},
                                          **kw)
                if self.register_once:
                    self.register_status, self.register_body = 200, None
                return response
            # A fresh id per issue, so a test can tell "reused the stored
            # identity" from "registered again" by the id alone.
            return httpx.Response(200, json={
                "producer_id": f"{self.id_prefix}-{len(self.registrations)}",
                "api_key": API_KEY,
                "service_name": "ingest",
                "registered_at": "2026-07-25T10:15:30Z",
                "max_msg_bytes": self.max_msg_bytes,
                "heartbeat_s": self.heartbeat_s,
                "retry_after_s": 5,
            })
        if name == "event":
            if self.event_delay_s:
                time.sleep(self.event_delay_s)
            if self.event_error is not None:
                raise self.event_error()
            data = json.loads(_body(request))
            self.envelopes.append(Envelope.model_validate(data))
            if self.event_status == 202:
                return httpx.Response(202, json={"code": "ok",
                                                 "msg_id": data["msg_id"]})
            return httpx.Response(self.event_status,
                                  json={"code": "server_busy", "reason": "mock"})
        if name == "notify":
            if self.notify_status != 200:
                return httpx.Response(self.notify_status,
                                      json={"code": "unauthorized"})
            # A complete (already-ended) stream: the reader takes it, sees EOF
            # and reconnects on its jittered curve.
            return httpx.Response(200,
                                  content="".join(self.notify_frames).encode(),
                                  headers={"content-type": "text/event-stream"})
        if name == "close":
            self.closes.append(json.loads(_body(request) or b"{}"))
            return httpx.Response(200, json={"code": "ok"})
        return httpx.Response(404, json={"code": "rejected", "reason": "no route"})


_CFG = """\
client:
  default_server: test_srv
  servers:
    test_srv:
      base_path: "{base_path}"
      service_name: ingest
    other_srv:
      base_path: "https://other.test:8443"
      service_name: ingest
    off_srv:
      base_path: "https://off.test:8443"
      enabled: false
  defaults:
    req_timeout_s:    {req_timeout_s}
    retry_num:        {retry_num}
    max_pending:      {max_pending}
    slot_wait_s:      {slot_wait_s}
    max_in_flight:    {max_in_flight}
    sent_timeout_s:   {sent_timeout_s}
    close_timeout_s:  {close_timeout_s}
    msg_id_block:     1000
    idle_timeout_s:   {idle_timeout_s}
    idle_heartbeats:  {idle_heartbeats}
    sweep_interval_s: {sweep_interval_s}
    gzip_min_bytes:   1024
    base_dir:         "{base_dir}"
"""


def write_cfg(tmp_path, **overrides) -> str:
    """A client config rooted at `tmp_path`.

    Sweeps are effectively off by default (`sweep_interval_s` is an hour) so
    nothing runs behind a test's back -- the tests that want a pass call
    `flush()`/`close()`, which sweep synchronously.

    **That default is also a trap, and it cost this module real coverage.**
    With every fixture at `sweep_interval_s=3600`, no test ever observed the
    background loop: `register()` could stop starting the cleaner, the SSE
    reader could stop being wired to the epoch sweep (§4.3 off entirely), and
    `sent_timeout_s` / `max_pending` could stop reaching their consumers, with
    the suite green throughout. Anything about §4.4 belongs on `looping_cfg`
    below, not here.
    """
    opts = dict(req_timeout_s=2, retry_num=3, max_pending=10000,
                slot_wait_s=0.05, max_in_flight=64, sent_timeout_s=300,
                close_timeout_s=2, sweep_interval_s=3600, idle_timeout_s=5, idle_heartbeats=5,
                base_path="https://ingest.test:8443",
                base_dir=str(tmp_path / "store"))
    opts.update(overrides)
    path = tmp_path / "msging.yaml"
    path.write_text(_CFG.format(**opts), encoding="utf-8")
    return str(path)


#: A sweep period short enough that a loop test finishes fast, long enough that
#: it is not a busy-wait on a loaded CI box.
LOOP_SWEEP_S = 0.02


def write_loop_cfg(tmp_path, **overrides) -> str:
    """`write_cfg` with the cleaner loop actually running (§4.4)."""
    opts = dict(sweep_interval_s=LOOP_SWEEP_S)
    opts.update(overrides)
    return write_cfg(tmp_path, **opts)


def wait_until(predicate, timeout_s: float = 5.0, tick_s: float = 0.005) -> bool:
    """Poll `predicate` until it is true or `timeout_s` elapses.

    Every loop test needs this and none of them may use a bare `sleep`: a fixed
    sleep either flakes on a slow box or wastes the whole suite's time on a
    fast one. Returns the final value so a caller can assert on it and get a
    useful message.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(tick_s)


class CountingTransport(httpx.BaseTransport):
    """Wraps a transport and counts the `close()` calls it receives.

    `httpx.MockTransport.close()` is inherited and does nothing, which is
    exactly why a caller's transport being closed out from under it was
    invisible to this suite. This makes it visible.
    """

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self.inner = inner
        self.closes = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self.inner.handle_request(request)

    def close(self) -> None:
        self.closes += 1


@pytest.fixture
def mock_server() -> MockServer:
    return MockServer()


@pytest.fixture
def cfg_file(tmp_path) -> str:
    return write_cfg(tmp_path)


def _register(cfg, server: MockServer) -> str:
    clnt.clnt_init(cfg)
    producer = clnt.register({"host": "laptop-7", "kind": "collector"},
                             _transport=server.transport())
    return producer["producer_id"]


@pytest.fixture
def registered(cfg_file, mock_server) -> str:
    return _register(cfg_file, mock_server)


@pytest.fixture
def sent_envelopes(mock_server) -> list[Envelope]:
    return mock_server.envelopes


@pytest.fixture
def registered_full_store(tmp_path, mock_server):
    """A producer whose durable store is already at `max_pending`.

    `max_pending: 1` plus one stored msg, so the *next* send is the one §5.1
    rejects with `slot_full` after waiting `slot_wait_s`.
    """
    pid = _register(write_cfg(tmp_path, max_pending=1), mock_server)
    clnt.send(pid, {"first": 1})
    return pid, clnt._store_for_tests(pid)


@pytest.fixture
def registered_tiny_limit(tmp_path):
    """A server whose `/register` reports a 100-byte `max_msg_bytes`, so the
    client-side §8.1 check has something to reject before storing."""
    server = MockServer(max_msg_bytes=100)
    pid = _register(write_cfg(tmp_path), server)
    return pid, clnt._store_for_tests(pid)


@pytest.fixture
def nacked(registered):
    """A msg that has been sent and NACKed over the notification path."""
    pid = registered
    result = clnt.send(pid, {"x": 1})
    clnt._deliver_for_tests(pid, code="nack", msg_id=result.msg_id,
                            reason="bad payload")
    return pid, result.msg_id, clnt._store_for_tests(pid)


def _with_callback(pid: str, **kw):
    seen: list = []
    # §2.2/§6.1: the callback takes (msg_id, notify_ev). The fixture keeps the
    # ev, which is what the assertions below read.
    clnt.on_notify(pid, lambda msg_id, ev: seen.append(ev), **kw)
    return pid, seen


@pytest.fixture
def looping(tmp_path, mock_server):
    """A registered producer whose **cleaner loop is running** (§4.4).

    Returns `(producer_id, MockServer)`. Everything that only the background
    loop does -- retry, `sent_timeout_s` demotion, the epoch sweep, the
    `max_pending` alarm -- is unobservable without this.
    """
    clnt.clnt_init(write_loop_cfg(tmp_path))
    pid = clnt.register({"host": "x"},
                        _transport=mock_server.transport())["producer_id"]
    return pid, mock_server


@pytest.fixture
def registered_with_callback(registered):
    return _with_callback(registered)


@pytest.fixture
def registered_with_ack_callback(registered):
    return _with_callback(registered, notify_on_ack=True)
