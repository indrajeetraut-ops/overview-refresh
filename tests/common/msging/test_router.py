"""FastAPI router + server API (design §5 paths, §6.2 server API, §6.3 ASGI
wiring, §8.1 status -> code, §10.1 registration, §10.4 logging, §10.5 metrics).

This is the wiring layer, so the tests pin the *contract a client depends on*,
not the shape of the code: the exact mount paths, the exact status code for
each §8.1 condition, that auth is actually enforced before anything else, that
`server_epoch` is one value per PROCESS however many services are mounted, and
that no api_key ever reaches a log line.
"""
import asyncio
import gzip
import io
import json
import time
import tracemalloc
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
import httpx
import structlog
from fastapi import FastAPI

from common.msging import srv
# The one place a test crosses the §6.4 client/server line on purpose: the
# server's notice bound is only meaningful against the cap the client parser
# actually enforces, and pinning it to a copied number is how they drift.
from common.msging.clnt.sse import MAX_EVENT_BYTES as CLIENT_MAX_EVENT_BYTES
from common.msging.config import MsgingConfig
from common.msging.errors import ConfigError
from common.msging.log import configure_logging
from common.msging.metrics import metrics, reset_metrics
# The test seams are private (srv._testing), which is the point of the module:
# they are destructive against a live server and must not sit in srv.__all__.
from common.msging.srv import _testing, api as srv_api
from common.msging.srv.router import (ENROLL_HEADER, MAX_DESCRIPTOR_BYTES,
                                      _descriptor)

pytestmark = pytest.mark.asyncio

DEFAULT_MAX_MSG_BYTES = 33554432          # §5.2 default, 32 MiB


# --------------------------------------------------------------- helpers


def _env(pid: str, msg_id: int, service_name: str = "ingest") -> dict:
    """A minimal §7 envelope. `schema_version` and `dedup` are deliberately
    omitted: models.Envelope defaults them ("1.0" / False), and a client that
    leaves them off must still be accepted."""
    return {"producer_id": pid, "msg_id": msg_id, "service_name": service_name,
            "created_at": "2026-07-25T10:15:30Z", "payload": {"x": 1}}


def _series(name: str, **labels) -> float:
    key = json.dumps(labels, sort_keys=True, separators=(",", ":"))
    return metrics().get(name, {}).get(key, 0)


def _frames(text: str) -> list[str]:
    """SSE frames from a chunk of stream text."""
    return [f for f in text.split("\n\n") if f.strip()]


def _data(frame: str) -> dict:
    for line in frame.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError(f"no data line in frame: {frame!r}")


def _scope(method: str, path: str, headers: dict[bytes, bytes],
           query: bytes = b"") -> dict:
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": query,
        "root_path": "", "client": ("127.0.0.1", 1234), "server": ("t", 80),
        "headers": [(b"host", b"t"), *headers.items()],
    }


async def _raw_post(app, path: str, receive, headers: dict[bytes, bytes] | None = None):
    """POST driven straight at the ASGI app with a caller-supplied `receive`.

    `httpx.ASGITransport` always hands the body over as one `http.request`
    message with a Content-Length, which is precisely the shape the C1 memory
    bound is *not* about: the attack is a chunked body, no declared length,
    arriving in many frames. Driving `receive` by hand is the only way to send
    that shape -- and the only way to count how many frames the server pulls
    before it gives up. It is also the only way to put a non-ASCII byte in a
    header, which httpx will not encode.
    """
    scope = _scope("POST", path, headers or {})
    status: dict = {}
    body = b""

    async def send(message: dict) -> None:
        nonlocal body
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
        elif message["type"] == "http.response.body":
            body += message.get("body", b"")

    await app(scope, receive, send)
    return status["code"], (json.loads(body) if body else {})


def _body_once(payload: bytes):
    """A `receive` that delivers `payload` as a single, complete body."""
    async def receive() -> dict:
        return {"type": "http.request", "body": payload, "more_body": False}
    return receive


def _chunked(chunk: bytes, count: int):
    """A `receive` streaming `count` frames of `chunk`, then end-of-body.

    Returns `(receive, pulls)` where `pulls` is a one-element list holding how
    many frames the server has asked for -- the C1 assertion.
    """
    pulls = [0]

    async def receive() -> dict:
        pulls[0] += 1
        if pulls[0] > count:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    return receive, pulls


async def _read_frames(app, path: str, key: str, want: int,
                       timeout: float = 2.0, query: bytes = b"",
                       headers_out: dict | None = None) -> list[str]:
    """Open the SSE stream and return its first `want` frames, then hang up.

    Driven against the ASGI app directly rather than through
    `httpx.ASGITransport`: that transport concatenates the whole response body
    before it returns, so an endless stream -- which is exactly what `/notify`
    is -- never yields a first frame to assert on. This is the same three
    messages Starlette itself would send, and it exercises the real route,
    the real `StreamingResponse` and the real hub.
    """
    scope = _scope("GET", path,
                   {b"authorization": f"Bearer {key}".encode()}, query=query)
    buf = ""
    status: dict = {}
    enough = asyncio.Event()
    hung_up = asyncio.Event()

    async def receive() -> dict:
        await hung_up.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        nonlocal buf
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
            status["headers"] = {k.decode().lower(): v.decode()
                                 for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            buf += message.get("body", b"").decode()
            if len(_frames(buf)) >= want:
                enough.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(enough.wait(), timeout)
    finally:
        hung_up.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert status["code"] == 200, status
    assert status["headers"]["content-type"].startswith("text/event-stream")
    if headers_out is not None:
        headers_out.update(status["headers"])
    return _frames(buf)[:want]


@asynccontextmanager
async def _held_stream(app, path: str, key: str, timeout: float = 2.0):
    """Open /notify and hold it open for the body of the `with` block.

    `_read_frames` hangs up as soon as it has what it wants, which is no use
    for asserting on state that only exists *while* a stream is live -- the
    `sse_connections` gauge, or what `srv_stop()` has to tear down.
    """
    scope = _scope("GET", path, {b"authorization": f"Bearer {key}".encode()})
    opened = asyncio.Event()
    hung_up = asyncio.Event()

    async def receive() -> dict:
        await hung_up.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            opened.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(opened.wait(), timeout)
        yield
    finally:
        hung_up.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _mounted(app: FastAPI) -> set[tuple[str, str]]:
    """(path, method) for every route the app serves, from its OpenAPI schema.

    Read off the schema rather than walked over `app.routes`: FastAPI keeps an
    included router as a single opaque entry there, so a scan of that list
    silently reports no routes at all. The schema is the served surface.
    """
    return {(path, method)
            for path, ops in app.openapi()["paths"].items()
            for method in ops}


async def _register(client, service: str = "ingest",
                    body: dict | None = None) -> tuple[str, str]:
    r = await client.post(f"/talium/{service}/v1/register", json=body or {})
    assert r.status_code == 200, r.text
    return r.json()["producer_id"], r.json()["api_key"]


def _app_for(*names: str, config: MsgingConfig | None = None) -> FastAPI:
    app = FastAPI()
    for i, name in enumerate(names):
        app.include_router(srv.srv_init(name, config=config if i == 0 else None))
    return app


class _Client(httpx.AsyncClient):
    """An httpx client over the ASGI app that also keeps a handle on the app
    itself -- `/notify` cannot be read through the transport (see
    `_read_frames`) and has to be driven directly."""

    def __init__(self, app: FastAPI) -> None:
        super().__init__(transport=httpx.ASGITransport(app=app),
                         base_url="http://t")
        self.asgi_app = app


def _client_for(app: FastAPI) -> _Client:
    return _Client(app)


# -------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _clean_metrics():
    """Metrics are a process-global store (§10.5); no test may inherit counts."""
    reset_metrics()
    yield
    reset_metrics()


@pytest.fixture()
def log_lines():
    """Rendered JSON log lines (§10.4), as a support bundle would carry them."""
    stream = io.StringIO()
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=stream))
    yield lambda: [json.loads(ln) for ln in stream.getvalue().splitlines() if ln.strip()]
    structlog.reset_defaults()


@pytest_asyncio.fixture()
async def client(tmp_path):
    _testing.reset_for_tests(base_dir=tmp_path)
    app = _app_for("ingest")
    await srv.srv_start()
    async with _client_for(app) as c:
        yield c
    await srv.srv_stop()


# ------------------------------------------------------- §5 mount paths


async def test_routes_mount_under_talium_service_v1(tmp_path):
    """§5: PATH = talium/{service_name}/v1/{register,event,notify,close}."""
    _testing.reset_for_tests(base_dir=tmp_path)
    app = _app_for("ingest")
    mounted = _mounted(app)
    assert ("/talium/ingest/v1/register", "post") in mounted
    assert ("/talium/ingest/v1/event", "post") in mounted
    assert ("/talium/ingest/v1/notify", "get") in mounted
    assert ("/talium/ingest/v1/close", "post") in mounted


async def test_the_mounted_paths_actually_route(tmp_path):
    """The schema above says what is declared; this says what answers. A
    prefix that is off by a segment is a 404, and a client whose config points
    at §5's path gets nothing."""
    _testing.reset_for_tests(base_dir=tmp_path)
    app = _app_for("ingest")
    async with _client_for(app) as c:
        assert (await c.post("/talium/ingest/v1/register", json={})).status_code == 200
        # Unauthenticated, so 401 -- but 401 is proof the route exists.
        assert (await c.post("/talium/ingest/v1/event", json={})).status_code == 401
        assert (await c.get("/talium/ingest/v1/notify")).status_code == 401
        assert (await c.post("/talium/ingest/v1/close", json={})).status_code == 401
        assert (await c.post("/ingest/v1/event", json={})).status_code == 404


async def test_two_services_mount_side_by_side(tmp_path):
    """One process may host several named services -- convention is one, not a
    limit (§6.3)."""
    _testing.reset_for_tests(base_dir=tmp_path)
    app = _app_for("ingest", "audit")
    paths = {path for path, _ in _mounted(app)}
    assert "/talium/ingest/v1/event" in paths
    assert "/talium/audit/v1/event" in paths


async def test_service_name_must_be_path_safe(tmp_path):
    _testing.reset_for_tests(base_dir=tmp_path)
    with pytest.raises(ValueError):
        srv.srv_init("ing est/../v2")


async def test_same_service_cannot_be_mounted_twice(tmp_path):
    _testing.reset_for_tests(base_dir=tmp_path)
    srv.srv_init("ingest")
    with pytest.raises(RuntimeError):
        srv.srv_init("ingest")


# ------------------------------------------------------------ /register


async def test_register_returns_identity_and_server_limits(client):
    r = await client.post("/talium/ingest/v1/register", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["producer_id"] and body["api_key"]
    assert body["api_key"].startswith("tk_prod_")
    # The client checks size before storing, against this value (§8.1).
    assert body["max_msg_bytes"] == DEFAULT_MAX_MSG_BYTES
    assert body["heartbeat_s"] == 15.0        # client sets idle_timeout ~3x (§4.2)
    assert body["service_name"] == "ingest"
    assert body["registered_at"]
    assert _series("registrations_total") == 1


async def test_register_stores_the_descriptor(client, tmp_path):
    pid, _ = await _register(client, body={"host": "laptop"})
    stored = json.loads((tmp_path / "producers.json").read_text())
    assert stored[pid]["descriptor"] == {"host": "laptop"}
    # Never the plaintext key -- prefix + hash only (§10.4).
    assert "api_key" not in stored[pid]


async def test_register_in_enrollment_key_mode(tmp_path):
    """§10.1: pre-shared secret checked before issuing; bad or missing -> 401."""
    cfg = MsgingConfig()
    cfg.server.registration.mode = "enrollment_key"
    cfg.server.registration.key = "s3cret"
    cfg.producer_store.file.path = str(tmp_path / "producers.json")
    _testing.reset_for_tests(config=cfg)
    app = _app_for("ingest", config=cfg)
    async with _client_for(app) as c:
        bad = await c.post("/talium/ingest/v1/register", json={},
                           headers={"X-Talium-Enroll-Key": "nope"})
        assert bad.status_code == 401
        assert bad.json()["code"] == "unauthorized"

        # Differs from the real key in the LAST character only: a comparison
        # that looked at a prefix -- or stopped early -- would accept this.
        near = await c.post("/talium/ingest/v1/register", json={},
                            headers={"X-Talium-Enroll-Key": "s3crex"})
        assert near.status_code == 401

        # ...and one that is a strict prefix of it.
        short = await c.post("/talium/ingest/v1/register", json={},
                             headers={"X-Talium-Enroll-Key": "s3cre"})
        assert short.status_code == 401

        missing = await c.post("/talium/ingest/v1/register", json={})
        assert missing.status_code == 401

        ok = await c.post("/talium/ingest/v1/register", json={},
                          headers={"X-Talium-Enroll-Key": "s3cret"})
        assert ok.status_code == 200 and ok.json()["api_key"]
    assert _series("auth_failures_total", code="enrollment") == 4


async def test_a_non_ascii_enrollment_key_header_is_401_not_500(tmp_path):
    """`hmac.compare_digest` on two `str`s encodes ASCII and raises otherwise.

    Starlette decodes headers as latin-1, so `X-Talium-Enroll-Key: caf\\xe9`
    made an *unauthenticated*, endlessly repeatable request return 500 with a
    UnicodeEncodeError traceback in the log, instead of the 401 the check
    exists to produce. The comparison has to happen on bytes.
    """
    cfg = MsgingConfig()
    cfg.server.registration.mode = "enrollment_key"
    cfg.server.registration.key = "s3cret"
    cfg.producer_store.file.path = str(tmp_path / "producers.json")
    _testing.reset_for_tests(config=cfg)
    app = _app_for("ingest", config=cfg)
    status, body = await _raw_post(
        app, "/talium/ingest/v1/register", _body_once(b"{}"),
        {b"content-type": b"application/json",
         b"x-talium-enroll-key": b"caf\xe9"})
    assert status == 401, status
    assert body["code"] == "unauthorized"
    assert _series("auth_failures_total", code="enrollment") == 1


async def test_a_non_ascii_enrollment_key_still_authenticates(tmp_path):
    """The other half: a key with a non-ASCII character must not merely fail
    safely, it must actually work. The header carries the UTF-8 bytes the
    client sent; the configured key is text from YAML. Both sides encode back
    to their wire form, so they meet."""
    cfg = MsgingConfig()
    cfg.server.registration.mode = "enrollment_key"
    cfg.server.registration.key = "café-s3cret"
    cfg.producer_store.file.path = str(tmp_path / "producers.json")
    _testing.reset_for_tests(config=cfg)
    app = _app_for("ingest", config=cfg)
    status, body = await _raw_post(
        app, "/talium/ingest/v1/register", _body_once(b"{}"),
        {b"content-type": b"application/json",
         b"x-talium-enroll-key": "café-s3cret".encode("utf-8")})
    assert status == 200, (status, body)
    assert body["api_key"]


async def test_an_unknown_registration_mode_fails_at_config_load(tmp_path):
    """§10.1: `mode` is `open | enrollment_key` and nothing else. Without this
    check a typo'd `enrolment_key` is not a mode, so the `== "enrollment_key"`
    branch in /register never fires and the server silently degrades to `open`
    -- issuing api_keys to anyone who can reach the port, which is exactly what
    the operator was configuring the mode to prevent."""
    cfg = MsgingConfig()
    cfg.server.registration.mode = "enrolment_key"          # one 'l'
    cfg.server.registration.key = "s3cret"
    cfg.producer_store.file.path = str(tmp_path / "producers.json")
    with pytest.raises(ConfigError, match="enrolment_key"):
        _testing.reset_for_tests(config=cfg)


# ------------------------------------------------- /register body limits


async def test_an_oversized_descriptor_is_413(client, tmp_path):
    """The descriptor gets its own, much smaller cap.

    Sized against `max_msg_bytes` it was a 32 MiB unauthenticated write that
    `FileProducerStore.upsert` rewrites whole and `Auth.verify` re-parses on
    every cache miss: 100 registrations would leave a 3.2 GB producers.json in
    the auth read path. `max_msg_bytes` is a payload limit and must not be
    reused for a stored, anonymous, metadata field.
    """
    over = json.dumps({"host": "x" * MAX_DESCRIPTOR_BYTES}).encode()
    # Comfortably inside the payload limit -- so this 413 can only come from a
    # cap of its own.
    assert len(over) < srv_api.machinery().cfg.server.max_msg_bytes
    r = await client.post("/talium/ingest/v1/register", content=over,
                          headers={"Content-Type": "application/json"})
    assert r.status_code == 413, r.text
    assert r.json()["code"] == "too_large"
    # Nothing was issued and nothing was written.
    assert _series("registrations_total") == 0
    stored = tmp_path / "producers.json"
    assert not stored.exists() or json.loads(stored.read_text() or "{}") == {}


async def test_a_descriptor_just_under_the_cap_is_accepted(client):
    """The cap has to be usable: `{host, kind, agent_version}` and then some."""
    body = json.dumps({"host": "x" * (MAX_DESCRIPTOR_BYTES - 200)}).encode()
    assert len(body) < MAX_DESCRIPTOR_BYTES
    r = await client.post("/talium/ingest/v1/register", content=body,
                          headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text


async def test_register_stops_pulling_the_body_at_the_cap(tmp_path):
    """`max_msg_bytes` was a validation rule, not a memory bound.

    `await request.body()` buffers the entire wire body before any cap can be
    applied. A chunked request declares no Content-Length to short-circuit on,
    and /register needs no auth at all (§10.1's `mode: open` is the v1
    default), so a 2 GiB chunked POST was an unauthenticated OOM -- the server
    answered 413 only after it had already resident the whole body.

    The server must stop *pulling* once the cap is crossed, which is what the
    frame count here measures.
    """
    _testing.reset_for_tests(base_dir=tmp_path)
    app = _app_for("ingest")
    receive, pulls = _chunked(b"a" * 1024, count=4096)      # 4 MiB on offer
    status, body = await _raw_post(app, "/talium/ingest/v1/register", receive,
                                   {b"content-type": b"application/json"})
    assert status == 413, status
    assert body["code"] == "too_large"
    ceiling = MAX_DESCRIPTOR_BYTES // 1024 + 1
    assert pulls[0] <= ceiling, (
        f"server pulled {pulls[0]} KiB frames for a {MAX_DESCRIPTOR_BYTES}-byte "
        f"cap; it should have stopped by {ceiling}"
    )


async def test_descriptor_survives_deeply_nested_json():
    """`json.loads` raises RecursionError -- not a ValueError -- on input that
    is merely *deep*, and an uncaught one escaped the route as a 500 with a
    traceback, unauthenticated and endlessly repeatable.

    Driven at `_descriptor` directly rather than over HTTP: it takes ~10 KB of
    nesting to exhaust the stack, and both callers now cap their bodies below
    that, so the 500 is no longer reachable through a route. The guard stays
    because the *cap* is what moved -- widen `MAX_DESCRIPTOR_BYTES` and an
    uncaught RecursionError comes straight back.
    """
    deep = b"[" * 20000
    assert _descriptor(deep) is None
    assert _descriptor(b'{"a":' * 20000) is None


async def test_a_deeply_nested_body_is_a_4xx_not_a_500(client):
    """The same body over the wire is a client error either way -- 413 for
    exceeding the descriptor cap -- and never a 500."""
    r = await client.post("/talium/ingest/v1/register", content=b"[" * 20000,
                          headers={"Content-Type": "application/json"})
    assert r.status_code == 413, r.text
    assert r.json()["code"] == "too_large"


async def test_a_deeply_nested_envelope_is_400_not_500(client):
    """The same body on the authenticated route, through pydantic's parser."""
    pid, key = await _register(client)
    r = await client.post("/talium/ingest/v1/event", content=b'{"a":' * 20000,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "rejected"


async def test_enrollment_key_mode_without_a_key_fails_at_config_load(tmp_path):
    """§10.1: `key` is required when mode=enrollment_key. Failing at wiring
    time is the point -- the alternative is a server that issues to anyone."""
    cfg = MsgingConfig()
    cfg.server.registration.mode = "enrollment_key"
    cfg.producer_store.file.path = str(tmp_path / "producers.json")
    with pytest.raises(ConfigError):
        _testing.reset_for_tests(config=cfg)


async def test_api_key_never_reaches_a_log_line(client, log_lines):
    r = await client.post("/talium/ingest/v1/register", json={"host": "laptop"})
    api_key = r.json()["api_key"]
    await client.post("/talium/ingest/v1/event", json=_env(r.json()["producer_id"], 1),
                      headers={"Authorization": f"Bearer {api_key}"})
    await client.post("/talium/ingest/v1/event", json=_env("x", 1),
                      headers={"Authorization": "Bearer tk_prod_bogus_key_value"})
    lines = log_lines()
    assert lines, "expected log output"
    blob = json.dumps(lines)
    assert api_key not in blob
    assert "tk_prod_bogus_key_value" not in blob
    # §10.4: producer_registered carries the mode; only the router knows it.
    reg = [ln for ln in lines if ln["event"] == "producer_registered"]
    assert any(ln.get("mode") == "open" for ln in reg)


async def test_one_log_event_name_is_one_log_line(client, log_lines):
    """§10.4 fixes `producer_registered`'s fields at (producer_id, mode), and
    only the router knows the mode. auth.py was emitting a *second*, different
    line under the same name -- (producer_id, key_prefix) -- so every query on
    that event name saw two shapes and half of them were missing the field
    being filtered on. Two lines, two names."""
    await client.post("/talium/ingest/v1/register", json={"host": "laptop"})
    lines = log_lines()

    reg = [ln for ln in lines if ln["event"] == "producer_registered"]
    assert len(reg) == 1, [ln["event"] for ln in lines]
    assert reg[0]["mode"] == "open" and "key_prefix" not in reg[0]

    mint = [ln for ln in lines if ln["event"] == "producer_key_issued"]
    assert len(mint) == 1 and mint[0]["key_prefix"]
    assert mint[0]["producer_id"] == reg[0]["producer_id"]


# --------------------------------------------------------------- /event


async def test_event_without_auth_is_401(client):
    pid, _ = await _register(client)
    r = await client.post("/talium/ingest/v1/event", json=_env(pid, 1))
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"
    assert _series("auth_failures_total", code="missing") == 1


async def test_event_with_an_unknown_key_is_401(client):
    pid, _ = await _register(client)
    r = await client.post("/talium/ingest/v1/event", json=_env(pid, 1),
                          headers={"Authorization": "Bearer tk_prod_not_a_real_key"})
    assert r.status_code == 401
    assert _series("auth_failures_total", code="unknown") == 1


async def test_event_for_another_producer_is_401(client):
    """A key authenticates one binding; it must not let its holder post as
    someone else (§3: one binding = one producer_id + api_key)."""
    _, key_a = await _register(client)
    pid_b, _ = await _register(client)
    r = await client.post("/talium/ingest/v1/event", json=_env(pid_b, 1),
                          headers={"Authorization": f"Bearer {key_a}"})
    assert r.status_code == 401


async def test_event_with_auth_is_202(client):
    pid, key = await _register(client)
    r = await client.post("/talium/ingest/v1/event", json=_env(pid, 1),
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 202
    assert r.json()["code"] == "ok"
    assert r.json()["msg_id"] == 1


async def test_malformed_envelope_is_400_rejected(client):
    """§8.1: `rejected  400 malformed envelope`."""
    pid, key = await _register(client)
    auth = {"Authorization": f"Bearer {key}"}
    missing_fields = await client.post("/talium/ingest/v1/event",
                                       json={"producer_id": pid}, headers=auth)
    assert missing_fields.status_code == 400
    assert missing_fields.json()["code"] == "rejected"

    not_json = await client.post("/talium/ingest/v1/event", content=b"{nope",
                                 headers={**auth, "Content-Type": "application/json"})
    assert not_json.status_code == 400
    assert not_json.json()["code"] == "rejected"


async def test_rejection_reason_does_not_echo_the_payload(client):
    """§10.4's never-log-the-payload rule has a mirror on the wire: a
    validation reason must name fields, never values."""
    pid, key = await _register(client)
    env = _env(pid, 1)
    env["msg_id"] = "not-an-int-Sup3rSecretValue"
    r = await client.post("/talium/ingest/v1/event", json=env,
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 400
    assert "Sup3rSecretValue" not in r.text


async def test_oversized_event_is_413_too_large(client):
    pid, key = await _register(client)
    env = _env(pid, 1)
    env["payload"] = {"blob": "x" * 200}
    _testing.set_max_msg_bytes_for_tests(100)
    r = await client.post("/talium/ingest/v1/event", json=env,
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 413
    assert r.json()["code"] == "too_large"


async def test_size_is_checked_after_auth(client):
    """An unauthenticated caller must not learn the server's size limit, and
    must not be able to make the server buffer a huge body first."""
    _testing.set_max_msg_bytes_for_tests(100)
    r = await client.post("/talium/ingest/v1/event",
                          json={"blob": "x" * 500})
    assert r.status_code == 401


async def test_gzipped_event_is_accepted_and_sized_after_decompression(client):
    """§1: contents are gzipped. The limit is the envelope's size, not the
    compressed body's -- otherwise a compressible payload slips past it."""
    import gzip
    pid, key = await _register(client)
    auth = {"Authorization": f"Bearer {key}",
            "Content-Type": "application/json", "Content-Encoding": "gzip"}

    ok = await client.post("/talium/ingest/v1/event",
                           content=gzip.compress(json.dumps(_env(pid, 5)).encode()),
                           headers=auth)
    assert ok.status_code == 202

    env = _env(pid, 6)
    env["payload"] = {"blob": "x" * 100000}       # compresses to a few hundred bytes
    _testing.set_max_msg_bytes_for_tests(1000)
    big = await client.post("/talium/ingest/v1/event",
                            content=gzip.compress(json.dumps(env).encode()),
                            headers=auth)
    assert big.status_code == 413
    assert big.json()["code"] == "too_large"


async def test_event_stops_pulling_the_body_at_the_cap(client):
    """The authenticated half of the same memory bound (C1).

    Also the only test that sends a body with no Content-Length at all, which
    is the single path on which the post-read size check is reachable: with a
    declared length the header shortcut answers first, so a chunked request is
    what proves the byte counter itself works.
    """
    pid, key = await _register(client)
    _testing.set_max_msg_bytes_for_tests(4096)
    receive, pulls = _chunked(b"a" * 1024, count=4096)      # 4 MiB on offer
    status, body = await _raw_post(
        client.asgi_app, "/talium/ingest/v1/event", receive,
        {b"authorization": f"Bearer {key}".encode(),
         b"content-type": b"application/json"})
    assert status == 413, status
    assert body["code"] == "too_large"
    assert pulls[0] <= 5, (
        f"server pulled {pulls[0]} KiB frames for a 4096-byte cap")


async def test_a_gzip_bomb_is_413_with_bounded_memory(client):
    """§1's bodies are gzipped, so the cap is on the *decompressed* envelope --
    which means a plain `decompress()` would inflate the bomb first and check
    afterwards. 64 MiB of zeros ship in ~64 KiB.

    The limit is set to 1 MiB, not to a few hundred bytes, on purpose: the wire
    cap would otherwise reject the compressed body on its own and this would
    prove nothing about the decompression cap. Here the bomb is comfortably
    *inside* every wire-side check, and only the capped inflate stands between
    it and 64 MiB of resident memory.
    """
    pid, key = await _register(client)
    _testing.set_max_msg_bytes_for_tests(1024 * 1024)
    bomb = gzip.compress(b"\0" * (64 * 1024 * 1024))
    assert len(bomb) < 1024 * 1024, "the bomb must clear the wire cap by itself"
    auth = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "Content-Encoding": "gzip"}
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        r = await client.post("/talium/ingest/v1/event", content=bomb, headers=auth)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert r.status_code == 413, r.text
    assert r.json()["code"] == "too_large"
    assert peak < 8 * 1024 * 1024, (
        f"inflated {peak} bytes of a 64 MiB bomb for a 1 MiB cap")


async def test_a_truncated_gzip_stream_is_rejected(client):
    """`obj.eof` is the only thing that distinguishes a complete member from a
    connection that died mid-body. Without it a body whose trailer never
    arrived -- so whose CRC and length were never checked -- is accepted as a
    valid envelope."""
    pid, key = await _register(client)
    auth = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "Content-Encoding": "gzip"}
    whole = gzip.compress(json.dumps(_env(pid, 4)).encode())
    # Trailer chopped: every deflate block is present, so the payload
    # decompresses in full and only `eof` can tell that it is unverified.
    for cut, what in ((whole[:-4], "trailer"), (whole[:len(whole) // 2], "half")):
        r = await client.post("/talium/ingest/v1/event", content=cut, headers=auth)
        assert r.status_code == 400, (what, r.status_code, r.text)
        assert r.json()["code"] == "rejected"


async def test_multi_member_gzip_is_not_silently_truncated(client):
    """RFC 1952 lets a gzip body be a concatenation of members, and
    `decompressobj` stops at the end of the first one. With `unused_data`
    unchecked everything after that member was discarded in silence, so the
    envelope the server parsed was not the one the client sent."""
    pid, key = await _register(client)
    auth = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "Content-Encoding": "gzip"}
    js = json.dumps(_env(pid, 12)).encode()
    half = len(js) // 2
    body = gzip.compress(js[:half]) + gzip.compress(js[half:])
    r = await client.post("/talium/ingest/v1/event", content=body, headers=auth)
    assert r.status_code == 202, r.text
    assert r.json()["msg_id"] == 12


async def test_trailing_gzip_members_count_against_the_cap(client):
    """...and the discarded members were a hole in the size limit: a tiny first
    member set the measured size, and the 10 MiB behind it cost nothing."""
    pid, key = await _register(client)
    _testing.set_max_msg_bytes_for_tests(2000)
    auth = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "Content-Encoding": "gzip"}
    body = gzip.compress(b'{"a":1}') + gzip.compress(b"\0" * (10 * 1024 * 1024))
    r = await client.post("/talium/ingest/v1/event", content=body, headers=auth)
    assert r.status_code == 413, r.text
    assert r.json()["code"] == "too_large"


async def test_queue_full_is_503_with_retry_after(client):
    pid, key = await _register(client)
    auth = {"Authorization": f"Bearer {key}"}
    restore = _testing.set_queue_max_for_tests(1)  # unstarted: nothing drains it
    try:
        first = await client.post("/talium/ingest/v1/event", json=_env(pid, 1),
                                  headers=auth)
        assert first.status_code == 202
        r = await client.post("/talium/ingest/v1/event", json=_env(pid, 2),
                              headers=auth)
        assert r.status_code == 503
        assert r.json()["code"] == "server_busy"
        assert r.headers["Retry-After"] == "5"
        assert _series("queue_full_total") == 1
    finally:
        # The seam hands back an undo, so the machinery is left as it was
        # found -- the real, running queue is back and the black-hole
        # replacement is gone.
        restore()

    seen: list[int] = []
    srv.on_event(lambda env: seen.append(env.msg_id))
    after = await client.post("/talium/ingest/v1/event", json=_env(pid, 3),
                              headers=auth)
    assert after.status_code == 202
    await asyncio.sleep(0.05)
    assert seen == [3], "restore() did not put the running queue back"


async def test_handler_receives_the_envelope_after_202(client):
    seen = []
    srv.on_event(lambda env: seen.append(env.msg_id))
    pid, key = await _register(client)
    r = await client.post("/talium/ingest/v1/event", json=_env(pid, 9),
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 202
    assert seen == [], "handler must not run inside the request handler (§6.2)"
    await asyncio.sleep(0.05)
    assert seen == [9]
    assert _series("msgs_accepted_total") == 1


async def test_handler_exception_does_not_reach_the_client(client, log_lines):
    def boom(env):
        raise ValueError(f"secret payload {env.payload}")

    srv.on_event(boom)
    pid, key = await _register(client)
    r = await client.post("/talium/ingest/v1/event", json=_env(pid, 3),
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 202
    assert "secret payload" not in r.text
    await asyncio.sleep(0.05)
    blob = json.dumps(log_lines())
    assert "secret payload" not in blob
    assert "handler_error" in blob


# --------------------------------------------------------------- /notify


async def test_notify_streams_the_epoch_first(client):
    pid, key = await _register(client)
    frames = await _read_frames(client.asgi_app, "/talium/ingest/v1/notify", key, want=1)
    assert frames[0].startswith("event: epoch")
    assert _data(frames[0])["server_epoch"]


async def test_notify_without_auth_is_401(client):
    r = await client.get("/talium/ingest/v1/notify")
    assert r.status_code == 401


async def test_notify_ignores_a_producer_id_query_param(client):
    """The stream's producer comes from the api_key, never from the request.

    A key authenticates one binding (§3), and this stream carries that
    binding's outcomes; if a query param could retarget it, any registered
    producer could read -- and by consuming, steal -- another's notices.
    """
    pid_a, key_a = await _register(client)
    pid_b, _key_b = await _register(client)
    srv.send_notice(pid_a, 101, "ack")
    srv.send_notice(pid_b, 202, "nack")

    frames = await _read_frames(client.asgi_app, "/talium/ingest/v1/notify",
                                key_a, want=2,
                                query=f"producer_id={pid_b}".encode())
    ev = _data(frames[1])
    assert ev["msg_id"] == 101, "the query param steered the stream"
    assert ev["producer_id"] == pid_a
    # B's notice is untouched, still waiting for B.
    assert srv_api.machinery().hub.buffered(pid_b) == 1


async def test_notify_sets_the_anti_buffering_headers(client):
    """§4.2: the client detects a dead link by the silence between heartbeats.
    An nginx in front that buffers the stream swallows exactly that signal, so
    the response has to opt out of it explicitly."""
    _pid, key = await _register(client)
    headers: dict = {}
    await _read_frames(client.asgi_app, "/talium/ingest/v1/notify", key, want=1,
                       headers_out=headers)
    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"


async def test_two_services_in_one_process_share_one_epoch(tmp_path):
    """§4.3/§6.3: `server_epoch` is a PROCESS boot id. Two hubs would hand out
    two epochs, and each service's clients would sweep all of sent/ back to
    pending/ every time the *other* service's stream reconnected."""
    _testing.reset_for_tests(base_dir=tmp_path)
    app = _app_for("ingest", "audit")
    await srv.srv_start()
    try:
        async with _client_for(app) as c:
            _, key_a = await _register(c, "ingest")
            _, key_b = await _register(c, "audit")
            a = await _read_frames(c.asgi_app, "/talium/ingest/v1/notify", key_a, want=1)
            b = await _read_frames(c.asgi_app, "/talium/audit/v1/notify", key_b, want=1)
        assert _data(a[0])["server_epoch"] == _data(b[0])["server_epoch"]
    finally:
        await srv.srv_stop()


async def test_mounting_a_second_service_does_not_change_the_epoch(tmp_path):
    """The other half of the same rule, and the one a shared-hub-by-accident
    implementation still gets wrong: minting the epoch inside `srv_init()`
    leaves every already-connected producer of the FIRST service seeing a new
    boot id when the second is mounted -- i.e. sweeping all of sent/ back to
    pending/ and resending, for a server that never restarted (§4.3)."""
    _testing.reset_for_tests(base_dir=tmp_path)
    app = _app_for("ingest")
    await srv.srv_start()
    try:
        async with _client_for(app) as c:
            _, key = await _register(c, "ingest")
            path = "/talium/ingest/v1/notify"
            before = await _read_frames(c.asgi_app, path, key, want=1)
            app.include_router(srv.srv_init("audit"))
            after = await _read_frames(c.asgi_app, path, key, want=1)
        assert _data(after[0])["server_epoch"] == _data(before[0])["server_epoch"]
    finally:
        await srv.srv_stop()


async def test_send_notice_reaches_the_stream(client):
    pid, key = await _register(client)
    srv.send_notice(pid, 42, "ack", reason="stored", info={"row": 7},
                    event_id="sess-abc")
    frames = await _read_frames(client.asgi_app, "/talium/ingest/v1/notify", key, want=2)
    ev = _data(frames[1])
    assert frames[1].startswith("event: notify")
    assert ev["msg_id"] == 42 and ev["code"] == "ack"
    assert ev["reason"] == "stored" and ev["info"] == {"row": 7}
    assert ev["event_id"] == "sess-abc" and ev["ts"]


async def test_an_oversized_notice_is_bounded_before_it_reaches_the_wire(client):
    """§8.2's `info` is an app bag, and nothing bounded it server-side.

    The client parser caps one SSE event at `MAX_EVENT_BYTES`; a frame over
    that is not resynchronisable, so the reader drops the link -- on every
    delivery attempt, for as long as the notice sits in the ring. An app that
    hands back a >64 KiB `info` therefore wedges that producer's outcome
    channel in a permanent reconnect loop, recoverable only via
    `sent_timeout_s`. Bound at the source instead, and loudly.

    The outcome itself still goes out: `code` is what resolves the msg, and
    dropping the whole notice to save the bag strands the msg it answers.
    """
    pid, key = await _register(client)
    srv.send_notice(pid, 42, "ack", reason="stored",
                    info={"blob": "x" * (4 * CLIENT_MAX_EVENT_BYTES)})
    frames = await _read_frames(client.asgi_app, "/talium/ingest/v1/notify",
                                key, want=2)
    assert len(frames[1].encode()) < CLIENT_MAX_EVENT_BYTES, \
        "the frame is larger than the client can parse"
    ev = _data(frames[1])
    assert ev["msg_id"] == 42 and ev["code"] == "ack"
    assert ev["info"] == {}, "the oversized bag rode out anyway"


async def test_the_notice_bound_sits_below_the_client_frame_cap(client):
    """The server-side bound is only useful if it is strictly under the cap the
    client enforces, with room for the `event:`/`data:` framing around it."""
    assert srv_api.machinery().cfg.server.max_notice_bytes < CLIENT_MAX_EVENT_BYTES


async def test_an_oversized_notice_is_logged_loudly_without_its_payload(
        client, log_lines):
    pid, key = await _register(client)
    secret = "ssn-9999-must-never-be-logged"
    srv.send_notice(pid, 7, "nack", reason=secret,
                    info={"bag": secret + "y" * 200000})
    line = next((ln for ln in log_lines()
                 if ln["event"] == "notice_oversized"), None)
    assert line is not None, "an app bug this expensive must be visible"
    assert line["producer_id"] == pid and line["msg_id"] == 7
    assert line["size_bytes"] > line["max_bytes"]
    assert secret not in json.dumps(line)


async def test_a_notice_inside_the_bound_is_untouched(client):
    pid, key = await _register(client)
    srv.send_notice(pid, 8, "ack", reason="stored", info={"row": 7})
    frames = await _read_frames(client.asgi_app, "/talium/ingest/v1/notify",
                                key, want=2)
    ev = _data(frames[1])
    assert ev["info"] == {"row": 7} and ev["reason"] == "stored"


async def test_a_notice_that_cannot_be_shed_under_the_bound_is_dropped(client):
    """Shedding `info` and `reason` bounds everything the app controls except
    the identifiers themselves. `code` and `event_id` are what the client
    correlates on, so they are never truncated -- one that is itself megabytes
    long simply cannot go out, and a lost notice is counted, never silent."""
    pid, _key = await _register(client)
    srv.send_notice(pid, 9, "ack", event_id="z" * 200000)
    assert _series("notices_dropped_total", reason="too_large") == 1
    assert srv_api.machinery().hub.buffered(pid) == 0


async def test_send_notice_from_a_worker_thread_is_bridged_to_the_loop(client):
    """hub.publish() raises off the loop thread (§6.4). send_notice() is the
    app-facing wrapper, and an app that offloads to an executor must still be
    able to report an outcome -- so the wrapper does the handoff."""
    pid, key = await _register(client)
    await asyncio.to_thread(srv.send_notice, pid, 77, "nack", "bad row")
    await asyncio.sleep(0.05)
    frames = await _read_frames(client.asgi_app, "/talium/ingest/v1/notify", key, want=2)
    ev = _data(frames[1])
    assert ev["msg_id"] == 77 and ev["code"] == "nack"


async def test_send_notice_before_srv_start_raises(tmp_path):
    _testing.reset_for_tests(base_dir=tmp_path)
    with pytest.raises(RuntimeError, match="before srv_start"):
        srv.send_notice("p", 1, "ack")


async def test_send_notice_after_srv_stop_says_after_srv_stop(tmp_path):
    """Both cases look like `loop is None`, but they are not the same mistake:
    one is unwired code, the other is a shutdown race. Telling the caller it
    forgot to call `srv_start()` when it in fact called `srv_stop()` sends them
    looking in the wrong place."""
    _testing.reset_for_tests(base_dir=tmp_path)
    _app_for("ingest")
    await srv.srv_start()
    await srv.srv_stop()
    with pytest.raises(RuntimeError, match="after srv_stop"):
        srv.send_notice("p", 1, "ack")


async def test_send_notice_onto_a_dead_loop_is_counted(tmp_path):
    """The one genuinely silent loss window.

    A loop that is bound and *open* but no longer running accepts
    `call_soon_threadsafe` and never runs the callback: the notice is gone, no
    exception, no counter, and the client sits in SENT until `sent_timeout_s`.
    A closed loop is the same loss wearing a RuntimeError. Both have to land in
    `notices_dropped_total` -- notify.py's one-ring rule is that there is
    exactly one place a notice can vanish and it is counted there.
    """
    _testing.reset_for_tests(base_dir=tmp_path)
    _app_for("ingest")
    await srv.srv_start()
    st = srv_api.machinery()
    live = st.loop
    dead = asyncio.new_event_loop()             # open, never run
    try:
        st.loop = dead
        await asyncio.to_thread(srv.send_notice, "p", 1, "ack")
        assert _series("notices_dropped_total", reason="no_loop") == 1

        dead.close()
        await asyncio.to_thread(srv.send_notice, "p", 2, "ack")
        assert _series("notices_dropped_total", reason="no_loop") == 2
    finally:
        if not dead.is_closed():
            dead.close()
        st.loop = live
        await srv.srv_stop()


# ------------------------------------------------------- §6.2 lifecycle


async def test_srv_stop_drains_the_sse_registry(tmp_path):
    """§6.2: `srv_stop()` drains "worker pool, SSE registry" -- both halves.

    Draining only the pool left every per-producer ring, every superseded
    /notify generator and a non-zero `sse_connections` alive, so §6.3's
    embed-in-a-longer-lived-process case leaked the whole registry on every
    stop/start cycle.
    """
    _testing.reset_for_tests(base_dir=tmp_path)
    app = _app_for("ingest")
    await srv.srv_start()
    stopped = False
    try:
        async with _client_for(app) as c:
            pid, key = await _register(c)
            async with _held_stream(c.asgi_app, "/talium/ingest/v1/notify", key):
                hub = srv_api.machinery().hub
                assert hub.tracked() == 1
                assert _series("sse_connections") == 1
                await srv.srv_stop()
                stopped = True
                assert hub.tracked() == 0, "rings survived srv_stop()"
                assert _series("sse_connections") == 0, "gauge survived srv_stop()"
    finally:
        if not stopped:
            await srv.srv_stop()


async def test_srv_stop_discards_buffered_notices_and_counts_them(tmp_path):
    """A stop loses outcomes. That is allowed -- `sent_timeout_s` recovers them
    (§4.4) -- but it must be visible, not silent."""
    _testing.reset_for_tests(base_dir=tmp_path)
    app = _app_for("ingest")
    await srv.srv_start()
    stopped = False
    try:
        async with _client_for(app) as c:
            pid, _key = await _register(c)
            srv.send_notice(pid, 7, "ack")
            hub = srv_api.machinery().hub
            assert hub.buffered(pid) == 1
            await srv.srv_stop()
            stopped = True
            assert hub.buffered(pid) == 0
            assert _series("notices_dropped_total", reason="dropped") == 1
    finally:
        if not stopped:
            await srv.srv_stop()


async def test_the_epoch_survives_a_stop_start_cycle(tmp_path):
    """§4.3: `server_epoch` is the id of a *process*, not of a lifespan.

    A host that reloads its lifespan -- or embeds the library and cycles it --
    has not restarted the server, and a fresh epoch tells every connected
    client to sweep all of `sent/` back to `pending/` and resend. The hub is
    built once, in `_Machinery`, and `srv_stop()` must not rebuild it.
    """
    _testing.reset_for_tests(base_dir=tmp_path)
    app = _app_for("ingest")
    path = "/talium/ingest/v1/notify"
    await srv.srv_start()
    async with _client_for(app) as c:
        _pid, key = await _register(c)
        before = _data((await _read_frames(c.asgi_app, path, key, want=1))[0])
    await srv.srv_stop()

    await srv.srv_start()
    try:
        async with _client_for(app) as c:
            after = _data((await _read_frames(c.asgi_app, path, key, want=1))[0])
        assert after["server_epoch"] == before["server_epoch"]
    finally:
        await srv.srv_stop()


async def test_the_pool_runs_again_after_a_stop_start_cycle(tmp_path):
    """The other half: a cycled process still delivers. `srv_stop()` closes the
    queue, so a restart that did not reopen it would answer 503 forever."""
    _testing.reset_for_tests(base_dir=tmp_path)
    app = _app_for("ingest")
    seen: list[int] = []
    srv.on_event(lambda env: seen.append(env.msg_id))
    await srv.srv_start()
    await srv.srv_stop()
    await srv.srv_start()
    try:
        async with _client_for(app) as c:
            pid, key = await _register(c)
            r = await c.post("/talium/ingest/v1/event", json=_env(pid, 21),
                             headers={"Authorization": f"Bearer {key}"})
            assert r.status_code == 202, r.text
            await asyncio.sleep(0.05)
        assert seen == [21]
    finally:
        await srv.srv_stop()


# ------------------------------------------------- test seams are private


async def test_the_test_seams_are_not_public_api():
    """They mint a new `server_epoch`, install a queue with no workers, and
    rewrite a live server's size limit. Sitting in `srv.__all__` next to
    `srv_start` and `send_notice` put a silent msg black hole one autocomplete
    away: `set_queue_max_for_tests(2)` on a running server answers 202 to msgs
    no worker will ever see."""
    for name in ("reset_for_tests", "set_queue_max_for_tests",
                 "set_max_msg_bytes_for_tests"):
        assert name not in srv.__all__, f"{name} is exported as public API"
        assert not hasattr(srv, name), f"srv.{name} is reachable"
        assert hasattr(_testing, name), f"{name} lost, not moved"


async def test_reset_for_tests_refuses_while_the_pool_is_running(tmp_path):
    """It cannot stop a pool -- it is sync -- so dropping the machinery would
    leave those workers running against state nothing routes to, hand every
    reconnecting client a new epoch, and leave `_dispatch` reading an
    `ev_handler` of None."""
    _testing.reset_for_tests(base_dir=tmp_path)
    _app_for("ingest")
    await srv.srv_start()
    try:
        with pytest.raises(RuntimeError, match="srv_stop"):
            _testing.reset_for_tests(base_dir=tmp_path)
    finally:
        await srv.srv_stop()


# ---------------------------------------------------------------- /close


async def test_close_is_200_and_fires_on_close(client):
    fired = []
    srv.on_close(lambda pid: fired.append(pid))
    pid, key = await _register(client)
    r = await client.post("/talium/ingest/v1/close", json={"producer_id": pid},
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and fired == [pid]
    assert r.json()["code"] == "ok"


async def test_close_without_auth_is_401(client):
    pid, _ = await _register(client)
    r = await client.post("/talium/ingest/v1/close", json={"producer_id": pid})
    assert r.status_code == 401


async def test_close_drops_the_notify_channel(client):
    """§2.4: the server drops the SSE channel.

    Asserted on the hub, not on "the first frame is the epoch" -- the epoch
    frame is unconditionally first on every stream, so that assertion held
    whether or not `/close` dropped anything. What has to be true is that the
    producer's ring is gone and its notices were counted as dropped (§10.5).
    """
    pid, key = await _register(client)
    hub = srv_api.machinery().hub
    srv.send_notice(pid, 1, "ack")
    srv.send_notice(pid, 2, "ack")
    assert hub.buffered(pid) == 2 and hub.tracked() == 1

    r = await client.post("/talium/ingest/v1/close", json={"producer_id": pid},
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert hub.buffered(pid) == 0, "the ring survived /close"
    assert hub.tracked() == 0, "the producer's hub state survived /close"
    assert _series("notices_dropped_total", reason="dropped") == 2
    # ...and the reconnect really does start clean.
    frames = await _read_frames(client.asgi_app, "/talium/ingest/v1/notify", key, want=1)
    assert frames[0].startswith("event: epoch")


async def test_close_for_another_producer_is_401(client):
    """A key authenticates one binding (§3). Naming someone else's producer_id
    in the close body must not fire their `on_close` hook or drop their SSE
    channel -- that is a remote denial of their outcome stream."""
    fired = []
    srv.on_close(lambda p: fired.append(p))
    _, key_a = await _register(client)
    pid_b, _ = await _register(client)
    r = await client.post("/talium/ingest/v1/close", json={"producer_id": pid_b},
                          headers={"Authorization": f"Bearer {key_a}"})
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"
    assert fired == [], "the hook ran for a producer the caller does not own"


async def test_close_with_a_malformed_body_is_400(client):
    """A body that does not parse is a 400, the same as on /register.

    `_descriptor(raw) or {}` turned one into an empty dict, which skipped the
    producer_id mismatch check below it -- so a cross-producer close wrapped in
    a garbage body answered 200 where the well-formed one answers 401.
    """
    _, key_a = await _register(client)
    pid_b, _ = await _register(client)
    r = await client.post("/talium/ingest/v1/close", content=b"{nope",
                          headers={"Authorization": f"Bearer {key_a}",
                                   "Content-Type": "application/json"})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "rejected"


async def test_close_touches_last_seen(client, tmp_path):
    """§2.4's third step. `Auth.verify` caches for 5s and only touches on a
    cache miss, so after any authenticated call the route's own touch is the
    only thing that can move the timestamp again."""
    pid, key = await _register(client)
    auth = {"Authorization": f"Bearer {key}"}
    # Warms the verify cache and writes the first last_seen_at.
    await client.post("/talium/ingest/v1/event", json=_env(pid, 1), headers=auth)
    stored = tmp_path / "producers.json"
    before = json.loads(stored.read_text())[pid]["last_seen_at"]
    assert before

    r = await client.post("/talium/ingest/v1/close", json={"producer_id": pid},
                          headers=auth)
    assert r.status_code == 200
    after = json.loads(stored.read_text())[pid]["last_seen_at"]
    assert after != before, "close did not touch last_seen"


async def test_close_callback_exception_does_not_reach_the_client(client, log_lines):
    def boom(pid):
        raise ValueError("secret close detail")

    srv.on_close(boom)
    pid, key = await _register(client)
    r = await client.post("/talium/ingest/v1/close", json={"producer_id": pid},
                          headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert "secret close detail" not in r.text
    assert "secret close detail" not in json.dumps(log_lines())


# ------------------------------------------------------ §6.3 make_app


async def test_make_app_wires_the_lifespan(tmp_path):
    """§6.2: sugar over the primitive, not a second path."""
    _testing.reset_for_tests(base_dir=tmp_path)
    app = srv.make_app("ingest")
    seen = []
    srv.on_event(lambda env: seen.append(env.msg_id))
    async with app.router.lifespan_context(app):
        async with _client_for(app) as c:
            pid, key = await _register(c)
            r = await c.post("/talium/ingest/v1/event", json=_env(pid, 11),
                             headers={"Authorization": f"Bearer {key}"})
            assert r.status_code == 202
            await asyncio.sleep(0.05)
    assert seen == [11], "lifespan must have started the worker pool"


async def test_make_app_lifespan_stops_on_exit(tmp_path):
    """The other end of §6.3's lifespan. A `yield` with no `srv_stop()` behind
    it leaves the worker pool, the loop binding and the SSE registry running
    after the app has shut down -- which under a test runner or an embedding
    host means the next boot inherits the last one's machinery."""
    _testing.reset_for_tests(base_dir=tmp_path)
    app = srv.make_app("ingest")
    async with app.router.lifespan_context(app):
        assert srv_api.machinery().loop is not None
    assert srv_api.machinery().loop is None, "lifespan did not call srv_stop()"
    # ...and the queue really is closed: an accepted msg would be a 202 nobody
    # answers, so a stopped pool must reject instead (§8.1).
    async with _client_for(app) as c:
        pid, key = await _register(c)
        r = await c.post("/talium/ingest/v1/event", json=_env(pid, 12),
                         headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 503, r.text
        assert r.json()["code"] == "server_busy"


# ------------------------------------------- §10.1 registration caps
#
# `mode: open` issues an identity to anyone who reaches the port, and every
# identity is a permanent store row that the auth path reads. Task 13 capped
# the *size* of one descriptor; nothing capped the count, so a loop of
# anonymous POSTs grew producers.json without limit and every request --
# including a rejected one -- paid for the growth. `max_producers` and
# `rate_per_min` bound the count. Neither is a security boundary; that is
# `enrollment_key`. They are the blast radius of a hostile caller, and equally
# of a buggy client in a retry loop.


def _capped(tmp_path, **registration) -> MsgingConfig:
    cfg = MsgingConfig()
    cfg.producer_store.file.path = str(tmp_path / "producers.json")
    for key, value in registration.items():
        setattr(cfg.server.registration, key, value)
    _testing.reset_for_tests(config=cfg)
    return cfg


async def test_max_producers_answers_503_server_busy_with_retry_after(tmp_path):
    """§8.1's one capacity vocabulary. Not a 4xx: the caller presented nothing
    wrong, the server has no room -- and `server_busy` + `Retry-After` is the
    code that already means exactly that, so `RegistrationError` stays terminal
    for the things that really are terminal (§10.1's bad enrollment key)."""
    _capped(tmp_path, max_producers=2, rate_per_min=0)
    app = _app_for("ingest")
    async with _client_for(app) as c:
        for _ in range(2):
            assert (await c.post("/talium/ingest/v1/register",
                                 json={})).status_code == 200
        r = await c.post("/talium/ingest/v1/register", json={})
        assert r.status_code == 503, r.text
        assert r.json()["code"] == "server_busy"
        assert r.headers["retry-after"]
        assert _series("registrations_refused_total", reason="capacity") == 1
        assert _series("registrations_total") == 2


async def test_a_refused_registration_writes_nothing_to_the_store(tmp_path):
    """The whole point of the cap: the file stops growing. A refusal that still
    wrote a row would bound the count and nothing else."""
    _capped(tmp_path, max_producers=1, rate_per_min=0)
    app = _app_for("ingest")
    stored = tmp_path / "producers.json"
    async with _client_for(app) as c:
        await c.post("/talium/ingest/v1/register", json={})
        size = stored.stat().st_size
        for _ in range(20):
            assert (await c.post("/talium/ingest/v1/register",
                                 json={"host": "x" * 500})).status_code == 503
    assert stored.stat().st_size == size
    assert len(json.loads(stored.read_text())) == 1


async def test_max_producers_zero_means_unlimited(tmp_path):
    """The escape hatch for a deployment that genuinely has more producers than
    the default and controls its own perimeter -- with `backend: postgres`,
    because raising it on the file backend just moves the cliff."""
    _capped(tmp_path, max_producers=0, rate_per_min=0)
    app = _app_for("ingest")
    async with _client_for(app) as c:
        for _ in range(30):
            assert (await c.post("/talium/ingest/v1/register",
                                 json={})).status_code == 200
    assert _series("registrations_refused_total", reason="capacity") == 0


async def test_rate_per_min_refuses_the_one_over_the_window(tmp_path):
    _capped(tmp_path, rate_per_min=3, max_producers=0)
    app = _app_for("ingest")
    async with _client_for(app) as c:
        for _ in range(3):
            assert (await c.post("/talium/ingest/v1/register",
                                 json={})).status_code == 200
        r = await c.post("/talium/ingest/v1/register", json={})
        assert r.status_code == 503, r.text
        assert r.json()["code"] == "server_busy"
        assert r.headers["retry-after"]
        assert _series("registrations_refused_total", reason="rate") == 1


async def test_rate_per_min_zero_means_unlimited(tmp_path):
    _capped(tmp_path, rate_per_min=0, max_producers=0)
    app = _app_for("ingest")
    async with _client_for(app) as c:
        for _ in range(50):
            assert (await c.post("/talium/ingest/v1/register",
                                 json={})).status_code == 200
    assert _series("registrations_refused_total", reason="rate") == 0


async def test_the_rate_window_rolls_and_the_whole_map_is_dropped_with_it(tmp_path):
    """A fixed window, not a token bucket: `/register` is a once-per-producer-
    lifetime call, so "how many in the last minute" is the shape that matters.

    The roll drops the entire counter map, which is what keeps the cap from
    being the leak -- the memory is the distinct sources seen inside one window,
    not the process's whole history.
    """
    _capped(tmp_path, rate_per_min=2, max_producers=0)
    app = _app_for("ingest")
    rate = srv_api.machinery().register_rate
    rate._window = 0.05
    async with _client_for(app) as c:
        for _ in range(2):
            assert (await c.post("/talium/ingest/v1/register",
                                 json={})).status_code == 200
        assert (await c.post("/talium/ingest/v1/register",
                             json={})).status_code == 503
        assert rate._counts, "nothing was counted"
        await asyncio.sleep(0.06)
        assert (await c.post("/talium/ingest/v1/register",
                             json={})).status_code == 200
        assert len(rate._counts) == 1, "the window roll did not drop the map"


async def test_a_rate_limited_registration_logs_the_source_and_no_key_material(
        tmp_path, log_lines):
    """Loud, because either a client is looping `/register` or someone is
    filling the store -- and both need an operator. No key material: nothing
    has been minted at this point, and §10.4 is normative that none of it would
    be loggable if it had."""
    _capped(tmp_path, rate_per_min=1, max_producers=0)
    app = _app_for("ingest")
    async with _client_for(app) as c:
        await c.post("/talium/ingest/v1/register", json={})
        await c.post("/talium/ingest/v1/register", json={})
    lines = [ln for ln in log_lines() if ln["event"] == "registration_rate_limited"]
    assert len(lines) == 1
    assert lines[0]["rate_per_min"] == 1
    assert lines[0]["source"]
    assert "api_key" not in json.dumps(lines[0])
    assert "tk_prod_" not in json.dumps(lines[0])


async def test_the_caps_are_checked_before_the_enrollment_key(tmp_path):
    """Order: rate first, then the secret. A rate limit that only applied to
    callers who already knew the enrollment key would bound nothing an
    anonymous loop does -- and comparing a pre-shared secret is work the
    limiter exists to stop an unbounded number of."""
    cfg = MsgingConfig()
    cfg.producer_store.file.path = str(tmp_path / "producers.json")
    cfg.server.registration.mode = "enrollment_key"
    cfg.server.registration.key = "s3cret"
    cfg.server.registration.rate_per_min = 1
    _testing.reset_for_tests(config=cfg)
    app = _app_for("ingest")
    async with _client_for(app) as c:
        hdr = {ENROLL_HEADER: "s3cret"}
        assert (await c.post("/talium/ingest/v1/register", json={},
                             headers=hdr)).status_code == 200
        r = await c.post("/talium/ingest/v1/register", json={}, headers=hdr)
        assert r.status_code == 503
        # A wrong secret over the limit is still 503, not 401: the limiter ran
        # first and the secret was never compared.
        r = await c.post("/talium/ingest/v1/register", json={},
                         headers={ENROLL_HEADER: "wrong"})
        assert r.status_code == 503


async def test_register_does_not_block_the_event_loop_on_the_store(tmp_path):
    """`issue()` reads the store to check the cap and then rewrites it, and the
    route is `async def` -- doing that on the loop is what made one `/register`
    block every other request. It goes to a thread, so a heartbeat scheduled
    behind it still runs while the write is in flight."""
    _capped(tmp_path, rate_per_min=0, max_producers=0)
    app = _app_for("ingest")
    ticks = [0]

    async def ticker():
        while True:
            ticks[0] += 1
            await asyncio.sleep(0)

    store = srv_api.machinery().store
    original = store.upsert

    def slow_upsert(record):
        time.sleep(0.05)
        return original(record)

    store.upsert = slow_upsert
    pump = asyncio.create_task(ticker())
    try:
        async with _client_for(app) as c:
            assert (await c.post("/talium/ingest/v1/register",
                                 json={})).status_code == 200
    finally:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
    assert ticks[0] > 1, \
        "the loop made no progress during /register -- the store write ran on it"
