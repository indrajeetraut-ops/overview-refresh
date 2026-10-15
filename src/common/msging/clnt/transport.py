"""One POST, one answer (design §2.2, §6.4).

`post_once` is the entire wire side of `send()`: it builds the request, makes
**exactly one** attempt, and turns whatever came back -- a status, a timeout, a
refused connection -- into a `Code` from §8.1. It never retries. Retry and
backoff belong to the cleaner loop (§4.4), and there is exactly one reason for
that: a msg is already durably stored before this function is called, so the
promise to the producer holds whether or not this attempt lands. Retrying
inline would only stretch `send()`'s latency past the one `req_timeout_s` §2.2
bounds it at, and it would put retry policy in two places.

**Blocking, not async.** The client half is threads + blocking httpx (§6.4);
the server half is asyncio. Nothing in `clnt/` imports `srv/` (see
`tests/common/msging/test_boundaries.py`).

**Nothing here raises for a runtime condition.** DNS failure, refused
connection, TLS error, read timeout, a 500 from a gateway -- all of them come
back as `(Code, reason)`. §8.3 keeps exceptions for programming errors, and a
network that is down is not one. In particular a timeout is *not* a failure of
the msg: the record stays `PENDING` and the loop retries.

**The status -> Code map is §8.1's table**, cross-checked against what
`srv/router.py` actually returns from `/event`: `202` ok, `400` rejected,
`401` unauthorized, `413` too_large, `503` server_busy. `200` is in the table
as `already_processed` and v1's server never emits it (§9.1 would). Anything
else is not in the table at all, and the choice for those is deliberately
conservative -- see `_code_for`.

**One wall-clock deadline, not one timeout per socket operation.** §2.2 bounds
`send()` at a single `req_timeout_s`, and `httpx.Timeout` alone cannot deliver
that: it arms every *operation* with `timeout_s`, so a peer that emits one byte
just under the timeout resets the clock forever. Measured on the unfixed code:
`timeout_s=1.0` against a server trickling 40 bytes every 0.4 s parked
`post_once` for 16 s on the body and indefinitely on the response headers.
§6.4 puts `send()` on the producer's own thread, so that is the whole app
stopping. `_Deadline` below fixes an absolute instant at call time and both
halves of the read path honour it -- see its docstring for the split.

Counting: this module owns §10.5's `msgs_sent_total{err_code}` and
`send_latency_ms`, because one POST is the only thing that knows its own
latency, and §10.4's `msg_sent` log line. The send path above it must not
re-count or re-log them; it passes `attempts` down instead.

Never logs the api_key (only `redact_key`'s prefix) and never logs the
payload -- `size_bytes` is what debugging needs (§10.4).
"""
from __future__ import annotations

import gzip
import json
import math
import threading
import time
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import NamedTuple

import httpx

from common.msging import metrics
from common.msging.codes import Code
from common.msging.log import get_logger, redact_key
from common.msging.models import Envelope

_logger = get_logger(__name__)

#: §8.1's table, keyed by the status `srv/router.py` returns.
_STATUS_CODES: dict[int, Code] = {
    200: Code.ALREADY_PROCESSED,   # reserved for §9.1; v1's server never sends it
    202: Code.OK,
    400: Code.REJECTED,
    401: Code.UNAUTHORIZED,
    413: Code.TOO_LARGE,
    # 429 is not a verdict on the msg either, and it is `server_busy`'s exact
    # neighbour in §8.1: the server is up and asking for less traffic, and it
    # sends the same Retry-After. `stored_unreachable` would tell an operator
    # the server is down when it is not.
    429: Code.SERVER_BUSY,
    503: Code.SERVER_BUSY,
}

#: Cap on the *response* bytes read back. The body is `{"code", "reason"}` and
#: nothing else is ever used from it, so there is no reason to let a broken or
#: hostile peer stream megabytes into this process -- the same posture
#: `srv/router.py` takes on the way in. Applied to the bytes *on the wire* and
#: again to whatever they decompress into; see `_read_capped`.
_MAX_RESPONSE_BYTES = 8192

#: `reason` is free-form and human-only (§8), and it ends up in logs. Bound it.
_MAX_REASON_CHARS = 512


class SendResult(NamedTuple):
    """What one POST came back with.

    A tuple so the shape stays obvious, named so the third field cannot be
    silently dropped the way it was when this returned `(Code, str)`.

    Attributes:
        code: the §8.1 code.
        reason: free-form and human-only (§8). Log it, surface it, never
            branch on it.
        retry_after_s: the server's `Retry-After` in seconds, or `None` when
            it sent none (or sent something unparseable). §8.1 says
            `server_busy` retries *after Retry-After* rather than on the
            backoff curve, and `post_once` is the only layer that ever sees
            the header -- the cleaner loop (§4.4) reads it from here.
    """

    code: Code
    reason: str
    retry_after_s: float | None = None


def event_url(base_url: str, service_name: str) -> str:
    """§5's path: `{base_path}/talium/{service_name}/v1/event`."""
    return f"{base_url.rstrip('/')}/talium/{service_name}/v1/event"


def _encode(env: Envelope, gzip_min_bytes: int) -> tuple[bytes, bytes, dict[str, str]]:
    """`(envelope bytes, wire bytes, headers)`.

    gzip at or above `gzip_min_bytes`, plain below it -- below the threshold
    gzip costs more than it saves (§5.1). `gzip.compress` emits exactly one
    well-formed member, which is what the server will accept: `router.py`
    rejects both truncated streams and multi-member concatenations. `mtime=0`
    keeps the bytes deterministic for a given envelope (no wall clock in the
    gzip header), so a resend is byte-identical to the first attempt.

    The two sizes are different things and both matter: the *envelope* size is
    what `max_msg_bytes` is measured against (§8.1), and the *wire* size is
    what the server's compressed-body cap sees.
    """
    raw = env.model_dump_json().encode("utf-8")
    if len(raw) >= gzip_min_bytes:
        return raw, gzip.compress(raw, mtime=0), {
            "content-type": "application/json",
            "content-encoding": "gzip",
        }
    return raw, raw, {"content-type": "application/json"}


def _code_for(status: int) -> Code:
    """Status -> §8.1 code, with a rule for what the table does not cover.

    Off-table statuses are never mapped to `rejected` or `too_large`. Those
    two are verdicts on the msg -- `rejected` NACKs the record and hands the
    app a decision to make, `too_large` drops it outright -- and a 404 from a
    misconfigured base_path or a 502 from a proxy is not a verdict on
    anything. So:

      - 5xx  -> `server_busy`: the server exists and did not take it. Same
        state and same handling as the 503 the table does name (PENDING, the
        loop retries).
      - anything else (an unexpected 2xx/3xx/4xx) -> `stored_unreachable`:
        the msg was never judged, so it stays PENDING and is retried. That can
        mean retrying a 404 forever, which a misrouted client deserves to see
        in its logs -- the alternative, silently NACKing a msg nobody
        rejected, is worse.
    """
    code = _STATUS_CODES.get(status)
    if code is not None:
        return code
    if status >= 500:
        return Code.SERVER_BUSY
    return Code.STORED_UNREACHABLE


class _Deadline:
    """One wall-clock instant that the whole exchange has to finish inside.

    Two mechanisms, because httpx offers no total-timeout knob and the two
    blocking phases are reachable in different ways:

      - **The body read** is our own loop, so it simply checks `expired()` per
        chunk and raises. Deterministic, thread-free, and it is the phase the
        review measured at 16 s under a 1 s timeout.
      - **Connect, write and the response headers** are inside one opaque
        blocking call. The only handle on those is the socket, so `arm()`
        starts a timer that closes the client out from under them. On a
        platform where closing an fd does not wake a blocked `recv`, the
        per-operation `httpx.Timeout` still ends that one operation, so the
        bound degrades to `2 x timeout_s` rather than back to unbounded.

    `fired` is what separates "the deadline killed it" from "the network did";
    without it the abort surfaces as a plain transport error and would be
    reported as `stored_unreachable` instead of §8.1's `stored_timeout`.

    TODO: when a caller passes its own long-lived pool, the timer cannot close
    the client (that would tear down a transport this function does not own),
    so connect/header trickle against a *shared* pool is still bounded only by
    the per-operation timeout. Closing just this request's connection needs a
    handle httpx does not expose before the response exists.
    """

    def __init__(self, timeout_s: float) -> None:
        self._at = time.monotonic() + max(timeout_s, 0.0)
        self._timeout_s = max(timeout_s, 0.0)
        self._fired = threading.Event()
        self._timer: threading.Timer | None = None

    @property
    def fired(self) -> bool:
        return self._fired.is_set()

    def expired(self) -> bool:
        return time.monotonic() >= self._at

    def arm(self, abort) -> None:
        def _fire() -> None:
            self._fired.set()
            try:
                abort()
            except Exception:                       # noqa: BLE001
                # The abort races the request finishing normally; a transport
                # already halfway shut is not an error worth propagating into
                # a timer thread nobody is joining.
                pass

        self._timer = threading.Timer(self._timeout_s, _fire)
        self._timer.daemon = True
        self._timer.start()

    def disarm(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


def _read_capped(response: httpx.Response, deadline: _Deadline) -> bytes:
    """At most `_MAX_RESPONSE_BYTES` of body, and never past `deadline`.

    `iter_raw`, not `iter_bytes`: `iter_bytes` hands a whole raw chunk (64 KiB)
    to the content decoder before it yields anything, so a peer answering with
    `content-encoding: gzip` expands the wire by its full compression ratio
    *before* the cap is ever consulted -- measured at a 151 MB peak from a
    194 KB response against an 8 KiB cap. Raw chunks are bounded by the wire,
    and `_decode_capped` bounds the expansion explicitly.

    Raises:
        httpx.ReadTimeout: the deadline passed before the body finished. The
            caller already holds the status by then, so this only costs the
            `reason` string (§2.2).
    """
    if response.is_stream_consumed:
        # A transport that answers with bytes instead of a socket (e.g.
        # `httpx.MockTransport`) has httpx buffer and decode the body during
        # construction. There is no stream left to pull from and nothing left
        # to bound -- those bytes are already in the process.
        return bytes(response.content[:_MAX_RESPONSE_BYTES])
    raw = bytearray()
    for chunk in response.iter_raw():
        raw += chunk
        if len(raw) >= _MAX_RESPONSE_BYTES:
            break
        if deadline.expired():
            raise httpx.ReadTimeout(
                "response body did not finish within req_timeout_s")
    return _decode_capped(bytes(raw[:_MAX_RESPONSE_BYTES]),
                          response.headers.get("content-encoding", ""))


def _decode_capped(raw: bytes, encoding: str) -> bytes:
    """Undo `content-encoding` with a hard ceiling on the output.

    `zlib`'s `max_length` is the only decompressor bound available, and it is
    the whole point: without it the cap on the wire bytes is not a cap on
    memory. The input is already truncated at `_MAX_RESPONSE_BYTES`, so the
    stream is normally incomplete -- `decompressobj` returns what it decoded
    and does not complain, which is exactly what `_reason_from` wants.
    """
    enc = encoding.strip().lower()
    if not raw or enc in ("", "identity"):
        return raw
    if enc == "gzip":
        wbits = 16 + zlib.MAX_WBITS
    elif enc == "deflate":
        wbits = zlib.MAX_WBITS
    else:
        # br/zstd and friends: no `reason` rather than an unbounded decoder.
        # `_reason_from` falls back to the status, which is the real signal.
        return b""
    try:
        return zlib.decompressobj(wbits).decompress(raw, _MAX_RESPONSE_BYTES)
    except zlib.error:
        return b""


def _retry_after_s(value: str | None) -> float | None:
    """`Retry-After` in seconds, or `None` if there wasn't a usable one.

    RFC 7231 allows delta-seconds or an HTTP-date; `srv/router.py` sends the
    former (`retry_after_s`), a proxy in front of it may well send the latter.
    A date already in the past clamps to 0 -- "retry now" -- and anything
    unparseable is `None`, because a header nobody can read must not be able
    to stall the cleaner loop.
    """
    if not value:
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


class _CallerOwnedTransport(httpx.BaseTransport):
    """Forwards to a transport this module did not create, and never closes it.

    `post_once` closes its `Client` on every path -- in `finally` and from the
    deadline timer -- and closing a `Client` closes its transport. A caller
    that passed a long-lived pool for connection reuse would find it dead after
    the first send, so the pool is wrapped rather than handed over.
    """

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._inner.handle_request(request)

    def close(self) -> None:
        """No-op: the caller owns this pool's lifetime."""


#: `post_once`'s half of the reason below. §2.2 allows one attempt and one
#: `req_timeout_s`.
_RETRY_WHY_POST = ("post_once makes exactly one attempt (design §2.2) and "
                   "transport-level retries would be extra attempts it is not "
                   "allowed to make")


def _reject_retrying_transport(transport: httpx.BaseTransport,
                               why: str = _RETRY_WHY_POST) -> None:
    """A pool built with `retries=N` quietly makes N+1 connect attempts inside
    one of the caller's, and stretches the connect phase to N+1 timeouts --
    measured at 4 attempts and 1.51 s for `retries=3`. That is a config error,
    so it raises (§8.3) instead of silently breaking the caller's guarantee.

    Shared with `sse.py`, which already takes `_CallerOwnedTransport` from
    here: the *check* is one rule with one implementation, and only the reason
    differs -- the POST path owns §2.2's single attempt, the reader owns §4.3's
    jittered reconnect, and a copy of this drifts the moment either is edited.
    """
    retries = getattr(getattr(transport, "_pool", None), "_retries", 0)
    if retries:
        raise ValueError(
            f"_transport was built with retries={retries}; {why}. Pass "
            "httpx.HTTPTransport(retries=0)."
        )


def _reason_from(status: int, raw: bytes) -> str:
    """The body's `reason`, or a status stand-in. Never branches on `reason`
    itself -- §8 makes it human-only -- and never returns unbounded text."""
    try:
        body = json.loads(raw) if raw else None
    except (ValueError, RecursionError, MemoryError):
        body = None
    if isinstance(body, dict):
        reason = body.get("reason")
        if isinstance(reason, str) and reason:
            return reason[:_MAX_REASON_CHARS]
        if reason:
            return str(reason)[:_MAX_REASON_CHARS]
    if status in (200, 202):
        return ""
    return f"http {status}"


def post_once(base_url: str, service_name: str, api_key: str, env: Envelope,
              timeout_s: float, gzip_min_bytes: int, *,
              _transport: httpx.BaseTransport | None = None,
              attempts: int | None = None) -> SendResult:
    """POST one envelope to `/event`, once, and return a `SendResult`.

    Args:
        base_url: the binding's `base_path`, e.g. `https://ingest.prod:8443`.
        service_name: the §5 path segment.
        api_key: presented as `Authorization: Bearer ...`. Never logged.
        env: the envelope, sent whole -- a resend has to rebuild the request
            verbatim (§3), so `event_id`, `dedup` and `info` travel with it.
        timeout_s: `req_timeout_s`, as one **wall-clock deadline** on the whole
            call -- connect, write, headers and body -- not one budget per
            socket operation. Expiry is `stored_timeout` and the record stays
            PENDING (§8.1). See `_Deadline` for how each phase is held to it.
        gzip_min_bytes: bodies at or above this are gzipped (§1, §5.1).
        _transport: keyword-only injection seam. Tests pass an
            `httpx.MockTransport`; a caller that wants connection reuse across
            sends passes one long-lived `httpx.HTTPTransport` (it is *not*
            closed here -- only a transport this function created is). It must
            not have transport-level retries enabled; see
            `_reject_retrying_transport`. When omitted, a fresh transport is
            built with `retries=0`, because httpx's own transport-level retry
            would be a second attempt this function is not allowed to make.
        attempts: the record's attempt count, for the `msg_sent` log line
            (§10.4). Owned by the caller -- this function only ever makes one
            attempt and cannot know how many came before.

    Returns:
        `SendResult(code, reason, retry_after_s)`.

    Raises:
        ValueError: `_transport` has transport-level retries enabled. That is
            a config error, not a runtime condition (§8.3).

    Never raises for a network or HTTP condition -- see the module docstring.

    TODO: `already_processed` (200) is mapped but nothing upstream consumes it
    yet; §9.1 owns that path.
    """
    url = event_url(base_url, service_name)
    envelope_bytes, wire, headers = _encode(env, gzip_min_bytes)
    headers["authorization"] = f"Bearer {api_key}"

    if _transport is not None:
        _reject_retrying_transport(_transport)
    built = httpx.HTTPTransport(retries=0) if _transport is None else None
    client = httpx.Client(
        # A transport we built is ours to close; a caller's is wrapped so that
        # closing the client -- which happens on every path, including from
        # the deadline timer -- cannot take their pool down with it.
        transport=built if built is not None else _CallerOwnedTransport(_transport),
        timeout=httpx.Timeout(timeout_s),
        # A 3xx must not become a second POST -- §2.2 allows exactly one.
        follow_redirects=False,
    )
    deadline = _Deadline(timeout_s)
    deadline.arm(client.close)

    started = time.monotonic()
    status: int | None = None
    code: Code | None = None
    reason = ""
    retry_after_s: float | None = None
    try:
        with client.stream("POST", url, content=wire, headers=headers) as response:
            status = response.status_code
            retry_after_s = _retry_after_s(response.headers.get("retry-after"))
            # §2.2 makes the status line the accept signal, so it decides the
            # code right here. Everything below can only fill in `reason`: a
            # 202 whose body stalls is still a 202, and downgrading it would
            # leave the record PENDING for a msg the server already took --
            # a guaranteed duplicate, since v1 has no server-side dedup (§9.1).
            code = _code_for(status)
            try:
                reason = _reason_from(status, _read_capped(response, deadline))
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                reason = _exc_reason(exc)
    except httpx.TimeoutException as exc:
        # Checked before TransportError: TimeoutException is a subclass of it,
        # and a timeout is its own §8.1 code.
        if code is None:
            code, reason = Code.STORED_TIMEOUT, _exc_reason(exc)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # HTTPError covers connect/DNS/TLS/protocol failures; InvalidURL is
        # not an HTTPError and is what a mis-set base_path produces. Both are
        # "did not reach a server", which is PENDING + retry, not a raise --
        # unless the deadline timer is what tore the socket down, in which
        # case the truth is `stored_timeout`.
        if code is None:
            code = Code.STORED_TIMEOUT if deadline.fired else Code.STORED_UNREACHABLE
            reason = _exc_reason(exc)
    finally:
        deadline.disarm()
        client.close()

    latency_ms = (time.monotonic() - started) * 1000.0
    metrics.counter("msgs_sent_total", err_code=str(code))
    metrics.observe("send_latency_ms", latency_ms)

    fields = dict(
        producer_id=env.producer_id,
        msg_id=env.msg_id,
        service_name=service_name,
        err_code=str(code),
        status=status,
        latency_ms=round(latency_ms, 3),
        # size_bytes, never the payload (§10.4). The envelope size is the one
        # max_msg_bytes talks about; wire_bytes is what actually went out.
        size_bytes=len(envelope_bytes),
        wire_bytes=len(wire),
        gzipped="content-encoding" in headers,
        # A prefix, never the key (§10.4).
        key_prefix=redact_key(api_key),
    )
    if attempts is not None:
        fields["attempts"] = attempts
    if retry_after_s is not None:
        fields["retry_after_s"] = retry_after_s
    if code is Code.OK:
        if reason:
            fields["reason"] = reason
        _logger.info("msg_sent", **fields)
    else:
        _logger.warning("msg_sent", reason=reason, **fields)
    return SendResult(code, reason, retry_after_s)


def _exc_reason(exc: Exception) -> str:
    """Exception type + message, bounded. httpx's messages name the host and
    the failure, never the request body."""
    return f"{type(exc).__name__}: {exc}"[:_MAX_REASON_CHARS]
