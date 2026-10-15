"""The HTTP skin: one `APIRouter` per service, mounted at §5's paths.

    talium/{service_name}/v1/register    issue producer_id + api_key (§10.1)
    talium/{service_name}/v1/event       POST -> enqueue -> 202 (§6.2)
    talium/{service_name}/v1/notify      SSE outcome stream (§4)
    talium/{service_name}/v1/close       courtesy close (§2.4)

**The lib returns a router; the host owns the app (§6.3).** Nothing here
creates a process, installs middleware, or configures logging. `make_app()` is
sugar over `srv_init()` + a lifespan for a dedicated process -- it is the same
path, not a second one.

**§8.1 is the whole contract of `/event`,** and the order the checks run in is
part of it:

    verify()          -> 401 unauthorized      before anything is read
    shutting_down     -> 503 server_busy + Retry-After
    size              -> 413 too_large         before anything is parsed
    parse             -> 400 rejected
    queue.offer()     -> 503 server_busy + Retry-After
    otherwise         -> 202 ok

Auth first so an anonymous caller cannot make the server buffer a body or
learn its limits. The shutdown check next, so a server that is draining does
not read a body it will never process -- and after auth, so it is not a state
probe for anyone who can reach the port. Size before parse so an oversized body
is never handed to pydantic. `offer()` last because acceptance *is* enqueueing.

**Nothing here calls `Auth.verify()`.** These routes are `async def`, and the
store read behind a verify cache miss is blocking I/O -- a file rewrite or a
database round trip. Run on the event loop it serialised every other request
behind it, and since a garbage Bearer token costs the same lookup as a real
one, one unauthenticated caller could do it on purpose. `verify_async()` probes
the cache on the loop and takes the miss to a thread; `issue()` and
`touch_last_seen()` go to a thread outright, because both write.

**Counting lives one layer down.** `queue.py` owns `msgs_accepted_total`,
`queue_depth`, `queue_full_total` and their log lines; `notify.py` owns the
notice counters and `sse_connections`. This module owns exactly the three §10.5
names none of them can see -- `registrations_total`,
`registrations_refused_total{reason}` and `auth_failures_total{code}` -- and
must not re-count the others.

Bodies for the non-2xx cases are `{"code", "reason"}`: §8 makes `code` the
wire field in a `200`/`202` body and the same enum everywhere, and `reason` is
free-form and human-only -- code must never branch on it.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import re
import zlib
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from .. import metrics
from ..codes import Code
from ..config import MsgingConfig
from ..errors import AuthError, CapacityError
from ..log import get_logger, redact_key
from ..models import Envelope
from . import api

_log = get_logger("msging.srv.router")

#: §10.1's pre-shared secret, presented on /register when mode=enrollment_key.
ENROLL_HEADER = "X-Talium-Enroll-Key"

#: A service_name becomes a path segment (§5), so it must not be able to
#: escape one.
_SERVICE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")

#: `auth_failures_total{code}` label for a bad/absent enrollment key. The other
#: labels are AuthError's own codes (missing | unknown | revoked).
CODE_ENROLLMENT = "enrollment"

#: Cap for the small JSON object bodies -- `/register`'s producer descriptor
#: (§6.1) and `/close`'s `{producer_id}` (§2.4).
#:
#: **Not `max_msg_bytes`.** That limit sizes a *payload* and defaults to 32 MiB
#: (§5.2). The descriptor is different in kind: it is `{host, kind,
#: agent_version}`-shaped metadata, it is accepted from an *unauthenticated*
#: caller (`mode: open` is v1's default, §10.1), it is stored verbatim and
#: forever, and `FileProducerStore` rewrites the whole file on every upsert and
#: re-parses it on every `Auth.verify` cache miss. Sizing it against
#: `max_msg_bytes` therefore turns anonymous registrations into unbounded disk
#: growth *and* read amplification on the auth path. 8 KiB is generous for what
#: the field is for.
MAX_DESCRIPTOR_BYTES = 8192


class _TooLarge(Exception):
    """`size` bytes were seen before the cap was crossed.

    `exact` says whether that is the body's real size. A declared
    Content-Length is exact; a streaming read is not -- C1's whole point is
    that the server stops reading at the cap and so never learns the total.
    """

    def __init__(self, size: int, exact: bool = True) -> None:
        self.size = size
        self.exact = exact

    def describe(self) -> str:
        return f"{self.size} bytes" if self.exact else f"over {self.size} bytes"


class _Malformed(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _err(status: int, code: str, reason: str = "",
         headers: dict | None = None, **extra) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"code": str(code), "reason": reason, **extra},
                        headers=headers)


def _unauthorized(code: str, service_name: str,
                  authorization: str | None = None) -> JSONResponse:
    """§10.4's `auth_failed` line + §10.5's `auth_failures_total{code}`.

    `key_prefix` only, via `redact_key` -- which returns the opaque marker
    rather than the input for anything short enough that a prefix would BE the
    key. The presented key never reaches the log by any path.
    """
    metrics.counter("auth_failures_total", code=code)
    _log.warning("auth_failed", code=code, service_name=service_name,
                 key_prefix=redact_key(_bearer(authorization)))
    return _err(401, Code.UNAUTHORIZED, code)


def _busy(st, reason: str) -> JSONResponse:
    """§8.1's `503 server_busy` + `Retry-After`.

    The one code that already means "the server has no room right now, keep the
    msg and come back". Used for a full queue, for a shutting-down server, and
    for §10.1's registration caps -- all three are capacity, none of them is the
    caller presenting something wrong, and §8 allows exactly one vocabulary.
    """
    return _err(503, Code.SERVER_BUSY, reason,
                headers={"Retry-After": str(st.cfg.server.retry_after_s)})


def _source(request: Request) -> str:
    """The rate-limit key for one `/register` caller.

    `request.client.host`, and deliberately not `X-Forwarded-For`: that header
    is caller-controlled, so trusting it would let the very loop this bounds
    hand itself a fresh key per request. Behind a proxy this collapses to the
    proxy and the limit becomes global -- see `_RegisterRate`, where that is the
    documented behaviour rather than a gap.
    """
    client = request.client
    return client.host if client else "unknown"


def _bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization.removeprefix("Bearer ").strip()


def _enroll_matches(presented: str, configured: str) -> bool:
    """Constant-time compare of §10.1's pre-shared secret, on *bytes*.

    `hmac.compare_digest` on two `str`s requires both to be ASCII and raises
    otherwise; Starlette decodes a header as latin-1, so any non-ASCII byte in
    `X-Talium-Enroll-Key` turned an unauthenticated request into a 500 with a
    `UnicodeEncodeError` traceback instead of the 401 the check exists to
    return. Comparing bytes fixes that and keeps the comparison constant-time.

    The two sides are encoded differently on purpose: the header value is a
    latin-1 decode of the wire bytes, so latin-1 recovers exactly what was
    sent, while the configured key came from YAML as text and its wire form is
    UTF-8. Encoding both back to their wire bytes is what makes a non-ASCII
    configured key actually usable rather than merely non-fatal.
    """
    return hmac.compare_digest(presented.encode("latin-1", "replace"),
                               configured.encode("utf-8"))


def _wire_cap(limit: int, gzipped: bool) -> int:
    """The cap on *wire* bytes for a body whose decompressed cap is `limit`.

    Identical to `limit` for an unencoded body. For a gzipped one the wire
    bytes may legitimately exceed it a little: deflate falls back to stored
    blocks on incompressible input, costing 5 bytes per 64 KiB, and gzip adds
    an 18-byte header/trailer. That slack is the whole of the allowance -- a
    gzip stream is otherwise not permitted to be larger than the envelope it
    carries, because deflate can encode arbitrarily many bytes of input into
    zero bytes of output (empty stored blocks), which would leave the
    decompressed cap alone bounding nothing.
    """
    if not gzipped:
        return limit
    return limit + (limit // 65535 + 1) * 5 + 64


class _GunzipCap:
    """Incremental gzip inflate that never holds more than `limit` bytes.

    `max_length` rather than a plain `decompress()`: the limit is checked on
    the *decompressed* envelope (that is what the client sized itself against
    at register(), §8.1), and a gzip bomb would otherwise turn a few hundred
    accepted bytes into gigabytes of server memory before the check ran. Fed
    chunk by chunk so the compressed side is never fully buffered either (C1).

    **Multi-member aware.** RFC 1952 lets a gzip file be a concatenation of
    members, and `zlib.decompressobj` stops at the end of the first one,
    parking the rest in `unused_data`. Ignoring that silently truncates:
    `gzip(a) + gzip(b)` would yield `a` alone, so a body could smuggle
    unlimited bytes past the cap and the envelope the client sent would not be
    the envelope the server parsed. Each finished member therefore rolls a
    fresh decompressor over the remainder, and `limit` applies to the total.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._obj = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self._out: list[bytes] = []
        self.total = 0

    def feed(self, data: bytes) -> None:
        while data:
            if self._obj.eof:
                self._obj = zlib.decompressobj(16 + zlib.MAX_WBITS)
            try:
                piece = self._obj.decompress(data, self._limit + 1 - self.total)
            except zlib.error as exc:
                raise _Malformed(f"gzip: {type(exc).__name__}") from exc
            self.total += len(piece)
            if self.total > self._limit or self._obj.unconsumed_tail:
                raise _TooLarge(self.total, exact=False)
            self._out.append(piece)
            # b"" until the member ends, then whatever followed it.
            data = self._obj.unused_data

    def finish(self) -> bytes:
        if not self._obj.eof:
            raise _Malformed("gzip: truncated stream")
        return b"".join(self._out)


async def _read_body(request: Request, limit: int) -> bytes:
    """The request body, decompressed if gzipped, capped at `limit` (§5.2).

    **Streamed, never buffered whole.** `await request.body()` materialises the
    entire wire body before any cap can be applied, which makes `max_msg_bytes`
    a validation rule and not a memory bound: `Transfer-Encoding: chunked`
    carries no Content-Length to short-circuit on, and `/register` takes a body
    from an unauthenticated caller (§10.1's `mode: open` default). Pulling
    chunk by chunk with a running total and aborting the moment the cap is
    crossed keeps the resident cost at the cap plus one transport chunk,
    whatever the sender claims or sends.

    §1: contents are gzipped, so the wire body and the envelope are not the
    same size and only the envelope's size is the one §8.1 talks about. The
    Content-Length shortcut therefore only applies when the body is *not*
    encoded -- for a gzipped body the declared length describes the compressed
    bytes and says nothing about the limit -- but the wire side still gets its
    own cap (`_wire_cap`), because a decompressed-only limit bounds no memory
    at all on the way in.
    """
    encoding = (request.headers.get("content-encoding") or "").lower()
    gzipped = encoding == "gzip"
    declared = request.headers.get("content-length")
    if not gzipped and declared and declared.isdigit() and int(declared) > limit:
        raise _TooLarge(int(declared))

    wire_cap = _wire_cap(limit, gzipped)
    gunzip = _GunzipCap(limit) if gzipped else None
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        # Checked before the chunk is retained, so the accumulator itself never
        # exceeds the cap; raising here also closes the stream, which is what
        # stops the transport being asked for the rest of the body.
        if total > wire_cap:
            raise _TooLarge(total, exact=False)
        if gunzip is not None:
            gunzip.feed(chunk)
        else:
            chunks.append(chunk)
    if gunzip is not None:
        return gunzip.finish()
    return b"".join(chunks)


def _validation_reason(exc: ValidationError) -> str:
    """Field names and error types -- never values.

    §10.4 forbids logging the payload; the same restraint applies to what goes
    back on the wire, because a pydantic error message embeds the offending
    input verbatim and a `reason` is exactly the kind of string that ends up in
    someone's log aggregator.
    """
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ())) or "envelope"
        parts.append(f"{loc}: {err.get('type', 'invalid')}")
    return "; ".join(parts) or "malformed envelope"


def srv_init(service_name: str, config: MsgingConfig | None = None) -> APIRouter:
    """Register one service and return its router, mounted at
    `talium/{service_name}/v1/*` (§6.2 step 1). The host includes it.

    Args:
        service_name: the name that becomes the path segment (§5). Must be
            `[A-Za-z0-9][A-Za-z0-9_-]*` -- it is interpolated into a URL path,
            so anything else could escape the prefix.
        config: process config, accepted on the first `srv_init()` only. The
            machinery is per-process (§6.2), so a second, different config
            would apply to nothing; passing one raises instead.

    Returns:
        An `APIRouter` carrying `/register`, `/event`, `/notify` and `/close`.

    Raises:
        ValueError: `service_name` is not path-safe.
        RuntimeError: this service is already mounted in this process, or a
            conflicting `config` was passed after the first call.
        ConfigError: the registration stanza is unusable (§10.1).
    """
    if not _SERVICE_NAME.fullmatch(service_name):
        raise ValueError(
            f"service_name {service_name!r} is not path-safe; it becomes a URL "
            "path segment (§5) and must match [A-Za-z0-9][A-Za-z0-9_-]*"
        )
    api.init_service(service_name, config)
    router = APIRouter(prefix=f"/talium/{service_name}/v1", tags=[service_name])

    @router.post("/register")
    async def register(request: Request) -> Response:
        """Issue a producer_id + api_key, honouring `registration.mode` (§10.1).

        The request body is the free-form producer descriptor (§6.1's
        `producer_description`), capped at `MAX_DESCRIPTOR_BYTES` -- free-form
        is not unbounded, and this route is the unauthenticated one. A
        `producer_type` string in it is also lifted
        onto the record. The response is the only place the plaintext api_key
        ever exists (see `Auth.issue`) -- it is returned here and never stored
        or logged. It carries the server's limits so client and server configs
        cannot drift (§6.1).
        """
        st = api.machinery()
        reg = st.cfg.server.registration
        if st.shutting_down:
            # Issuing an identity to a producer the process is about to stop
            # serving is pure cost: a permanent store row for a binding that
            # never sends anything.
            return _busy(st, "server is shutting down")
        if not st.register_rate.allow(_source(request), reg.rate_per_min):
            metrics.counter("registrations_refused_total", reason="rate")
            # Loud: either a client is looping /register or someone is filling
            # the store. Source only -- no key material exists yet.
            _log.warning("registration_rate_limited", service_name=service_name,
                         source=_source(request), rate_per_min=reg.rate_per_min)
            return _busy(st, "registration rate limit")
        if reg.mode == "enrollment_key":
            presented = request.headers.get(ENROLL_HEADER) or ""
            # compare_digest: a pre-shared secret, so timing matters. Bad or
            # missing -> 401 -> terminal RegistrationError on the client, no
            # retry loop; retrying a wrong secret never helps (§10.1).
            if not _enroll_matches(presented, reg.key or ""):
                return _unauthorized(CODE_ENROLLMENT, service_name)
        try:
            # MAX_DESCRIPTOR_BYTES, never max_msg_bytes: see the constant.
            raw = await _read_body(request, MAX_DESCRIPTOR_BYTES)
        except _TooLarge as exc:
            return _err(413, Code.TOO_LARGE,
                        f"descriptor is {exc.describe()}; the limit is "
                        f"{MAX_DESCRIPTOR_BYTES}")
        except _Malformed as exc:
            return _err(400, Code.REJECTED, exc.reason)
        descriptor = _descriptor(raw)
        if descriptor is None:
            return _err(400, Code.REJECTED, "descriptor must be a JSON object")
        producer_type = descriptor.get("producer_type")
        try:
            # In a thread: issue() reads the store to check the cap and then
            # rewrites it, and this route is `async def` -- doing that on the
            # loop is what made one /register block every other request.
            producer_id, api_key, registered_at = await asyncio.to_thread(
                st.auth.issue, descriptor,
                producer_type if isinstance(producer_type, str) else None)
        except CapacityError as exc:
            metrics.counter("registrations_refused_total", reason="capacity")
            return _busy(st, str(exc))
        metrics.counter("registrations_total")
        # §10.4's producer_registered fields are producer_id + mode, and this
        # is the only layer that knows the policy. auth.py logs the key mint
        # separately, as producer_key_issued -- one event name, one field list.
        _log.info("producer_registered", producer_id=producer_id, mode=reg.mode,
                  service_name=service_name, server_epoch=st.hub.epoch)
        return JSONResponse(status_code=200, content={
            "producer_id": producer_id,
            "api_key": api_key,
            "service_name": service_name,
            "registered_at": registered_at,
            # The server's limits, so the client can enforce them before
            # storing (§6.1, §8.1 too_large) and size its idle timer (§4.2).
            "max_msg_bytes": st.cfg.server.max_msg_bytes,
            "heartbeat_s": st.cfg.server.heartbeat_s,
            "retry_after_s": st.cfg.server.retry_after_s,
        })

    @router.post("/event")
    async def post_event(request: Request) -> Response:
        """Accept one msg: `POST -> enqueue -> 202` (§6.2). Status codes per
        §8.1; see the module docstring for why they are checked in this order.

        Note: the envelope's `service_name` is not checked against the mount
        path and auth is not service-scoped, so a key issued under one service
        may post an envelope naming another. An app that routes on
        `env.service_name` is routing on client-controlled data; route on the
        mount instead. Raised as an open design question, behaviour unchanged.
        """
        st = api.machinery()
        authorization = request.headers.get("authorization")
        try:
            # verify_async, never verify(): the store read behind a cache miss
            # is blocking I/O, and this route is `async def`. Run on the loop it
            # serialised every other request behind it -- including the SSE
            # heartbeats and the worker pool -- and a garbage Bearer token cost
            # exactly as much as a real one.
            record = await st.auth.verify_async(authorization)
        except AuthError as exc:
            return _unauthorized(exc.code, service_name, authorization)

        if st.shutting_down:
            # After auth, so an anonymous caller still cannot probe the server's
            # state, and before the body is read, so a shutting-down server does
            # not buffer 32 MiB it will never process. The queue is stopped a
            # moment later and would answer the same 503 anyway (queue.offer);
            # this only makes the window explicit rather than a race.
            return _busy(st, "server is shutting down")

        limit = st.cfg.server.max_msg_bytes
        try:
            raw = await _read_body(request, limit)
        except _TooLarge as exc:
            # size_bytes, never the body (§10.4).
            _log.warning("msg_too_large", producer_id=record.producer_id,
                         service_name=service_name, code=Code.TOO_LARGE,
                         size_bytes=exc.size, max_msg_bytes=limit)
            return _err(413, Code.TOO_LARGE,
                        f"msg is {exc.describe()}; max_msg_bytes is {limit}")
        except _Malformed as exc:
            return _err(400, Code.REJECTED, exc.reason)

        try:
            env = Envelope.model_validate_json(raw)
        except ValidationError as exc:
            _log.warning("msg_rejected", producer_id=record.producer_id,
                         service_name=service_name, code=Code.REJECTED,
                         size_bytes=len(raw))
            return _err(400, Code.REJECTED, _validation_reason(exc))

        if env.producer_id != record.producer_id:
            # A key authenticates one binding (§3). Letting its holder post
            # under another producer_id would let them steer that producer's
            # outcome stream. Same 401 -> the client re-registers (§8.1).
            return _unauthorized("producer_mismatch", service_name, authorization)

        if not st.queue.offer(env):
            # offer() already counted queue_full_total and logged; the route
            # only maps the False to §8.1's 503 + Retry-After.
            return _err(503, Code.SERVER_BUSY, "server queue full",
                        headers={"Retry-After": str(st.cfg.server.retry_after_s)})
        return _err(202, Code.OK, msg_id=env.msg_id)

    @router.get("/notify")
    async def notify(request: Request) -> Response:
        """The producer's SSE outcome stream (§4).

        The producer_id comes from the api_key, never from the query string:
        this stream carries one binding's outcomes, and a caller must not be
        able to read another producer's by naming it.
        """
        st = api.machinery()
        authorization = request.headers.get("authorization")
        try:
            record = await st.auth.verify_async(authorization)
        except AuthError as exc:
            return _unauthorized(exc.code, service_name, authorization)
        if st.shutting_down:
            # Opening a stream that srv_shutdown() is about to end would hand
            # the client an epoch frame and an immediate EOF, which its reader
            # counts as a lost link and reconnects on. A 503 says the same thing
            # once (§8.1).
            return _busy(st, "server is shutting down")
        return StreamingResponse(
            st.hub.subscribe(record.producer_id),
            media_type="text/event-stream",
            # no-store/no-cache for the obvious reason; X-Accel-Buffering
            # because an nginx in front will otherwise buffer the stream and
            # swallow the heartbeat the client uses to detect a dead link (§4.2).
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/close")
    async def close(request: Request) -> Response:
        """The client's informational close (§2.4).

        The client has already drained; the server holds no durable state, so
        there is nothing to fence. Fire the app hook, drop the SSE channel,
        touch `last_seen`, `200` -- in that order, per §2.4.
        """
        st = api.machinery()
        authorization = request.headers.get("authorization")
        try:
            record = await st.auth.verify_async(authorization)
        except AuthError as exc:
            return _unauthorized(exc.code, service_name, authorization)
        # No shutting_down check: /close is the client saying it has drained
        # (§2.4), so a shutting-down server is exactly when it is most useful.
        # It takes no queue slot and holds nothing open.
        try:
            # `{"producer_id": ...}`, so the descriptor cap, not max_msg_bytes.
            raw = await _read_body(request, MAX_DESCRIPTOR_BYTES)
        except _TooLarge as exc:
            return _err(413, Code.TOO_LARGE,
                        f"body is {exc.describe()}; the limit is "
                        f"{MAX_DESCRIPTOR_BYTES}")
        except _Malformed as exc:
            return _err(400, Code.REJECTED, exc.reason)
        body = _descriptor(raw)
        if body is None:
            # Same as /register: a body that does not parse is a 400, not a
            # silently-ignored one. `or {}` here skipped the mismatch check
            # below, so a garbage body turned a cross-producer close into a 200.
            return _err(400, Code.REJECTED, "body must be a JSON object")
        claimed = body.get("producer_id")
        if claimed and claimed != record.producer_id:
            return _unauthorized("producer_mismatch", service_name, authorization)

        producer_id = record.producer_id
        await api.fire_close(st, producer_id)
        # On the loop thread (this route is async), which is what drop() needs.
        st.hub.drop(producer_id)
        # In a thread: the file backend rewrites the whole store here.
        await asyncio.to_thread(st.store.touch_last_seen, producer_id,
                                api.utc_now_iso())
        _log.info("producer_closed", producer_id=producer_id,
                  service_name=service_name, server_epoch=st.hub.epoch)
        return _err(200, Code.OK)

    return router


def _descriptor(raw: bytes) -> dict | None:
    """Parse a small JSON object body. None when it is not an object.

    `RecursionError` and `MemoryError` are caught alongside `ValueError`
    because `json.loads` raises them, not a decode error, for input that is
    merely *deep*: `{"a":` repeated a few thousand times exhausts the
    interpreter stack. Both are 400s -- the caller's body is the problem --
    and leaving them uncaught turned a short unauthenticated POST into a 500
    with a traceback.
    """
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, RecursionError, MemoryError):
        return None
    return parsed if isinstance(parsed, dict) else None


def make_app(*service_names: str, config: MsgingConfig | None = None,
             **fastapi_kwargs) -> FastAPI:
    """A preconfigured `FastAPI` with the lifespan wired (§6.2 step 6).

    Sugar over `srv_init()` + §6.3's lifespan, for a dedicated process. A host
    that wants its own middleware, auth, CORS or `/healthz` uses `srv_init()`
    directly and keeps ownership of the app -- that is the primitive, and this
    is not a second path to it.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await api.srv_start()
        try:
            yield
        finally:
            # finally, not a bare yield/await pair: a startup that later fails
            # must still drain the worker pool it booted.
            await api.srv_stop()

    app = FastAPI(lifespan=lifespan, **fastapi_kwargs)
    for i, name in enumerate(service_names):
        app.include_router(srv_init(name, config=config if i == 0 else None))
    return app
