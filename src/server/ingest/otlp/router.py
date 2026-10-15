"""The HTTP skin for the OTLP receiver: two routes and the guards around them
(otel-intake design §4.1).

```
POST /otlp/v1/{metrics|logs}
  1. shed        in-flight over max_inflight -> 429 + Retry-After, before anything costly
  2. auth        Bearer org token -> OrgPrincipal | 401
  3. throttle    per-org token bucket -> 429 + Retry-After
  4. read        streamed, capped -> 413
  5. decode      protobuf | json, gzip honored -> 400
  6. ingest      detect -> parse -> filter -> write, one transaction per body
  7. respond     200 + ExportServiceResponse; partialSuccess counts for unmapped records
```

**Why shed before auth**, where §4.1 lists auth first: auth's cache miss is a database read,
so admitting a request past the semaphore in order to authenticate it would let overload
consume the very connection pool the JSONL path shares. Shedding is a comparison and an
integer; it costs nothing and protects everything behind it. Cumulative metrics are
self-healing (§4.1), so a shed interval costs freshness and never totals, and a shed
`api_request` at worst leaves that request `jsonl_est`.

**Why the work runs in a thread:** the storage layer is synchronous, and this router shares
its event loop with the msging SSE channel. Decoding and writing on the loop would stall
JSONL delivery behind an OTel batch — the shared-fate failure D-OT1 accepts a topology to
avoid.

Logging is content-free (arch §13): identifiers, counts and reasons only. No attribute value
ever reaches a log line from here.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from server.storage.engine import session_scope

from ..config import OtlpConfig
from .auth import OrgAuth, OrgAuthError
from .decode import OtlpDecodeError, OtlpTooLargeError, decode
from .mapping import UNKNOWN_VENDOR, MappingRegistry
from .receiver import IngestResult, OtlpReceiver

log = structlog.get_logger(module="ingest.otlp")

#: OTLP signal -> the `partialSuccess` field naming its rejected records (spec).
_REJECTED_FIELD = {"metrics": "rejectedDataPoints", "logs": "rejectedLogRecords"}


class _Inflight:
    """A counting semaphore with a non-blocking acquire.

    A plain `asyncio.Semaphore` would queue the excess instead of shedding it, which turns
    overload into unbounded latency — the one failure mode backpressure exists to prevent.
    An int is safe without a lock here: the check and the increment do not `await`, and the
    event loop is single-threaded.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0

    def try_acquire(self) -> bool:
        if self.limit and self.count >= self.limit:
            return False
        self.count += 1
        return True

    def release(self) -> None:
        self.count = max(0, self.count - 1)


class _OrgRate:
    """Per-org token bucket, keyed on the authenticated org (§4.1).

    Keyed on the org and not the client address: every OTLP request is attributable by
    construction, so unlike the JSONL registration path there is no need to fall back to an
    address a proxy would collapse. Bounded LRU so a long-lived process cannot accumulate one
    bucket per org id it has ever seen.
    """

    def __init__(self, rate_per_s: float, burst: float, max_orgs: int = 1024) -> None:
        self.rate = rate_per_s
        self.burst = burst
        self.max_orgs = max_orgs
        self._buckets: "OrderedDict[str, tuple[float, float]]" = OrderedDict()

    def allow(self, org_id: str) -> bool:
        if self.rate <= 0:
            return True
        now = time.monotonic()
        tokens, last = self._buckets.get(org_id, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1.0:
            self._buckets[org_id] = (tokens, now)
            self._buckets.move_to_end(org_id)
            return False
        self._buckets[org_id] = (tokens - 1.0, now)
        self._buckets.move_to_end(org_id)
        while len(self._buckets) > self.max_orgs:
            self._buckets.popitem(last=False)
        return True


class OtlpGuards:
    """The two isolation guards, together, so a test can hold them and a deployment can see
    them in one place."""

    def __init__(self, config: OtlpConfig) -> None:
        self.inflight = _Inflight(config.max_inflight)
        self.rate = _OrgRate(config.org_rate_per_s, config.org_burst)


async def _read_body(request: Request, limit: int) -> bytes:
    """The wire body, capped while it streams.

    `await request.body()` materialises the whole thing before any cap can apply, which makes
    `max_body_bytes` a validation rule and not a memory bound — `Transfer-Encoding: chunked`
    carries no Content-Length to short-circuit on. Aborting mid-stream keeps the resident cost
    at the cap plus one chunk however much the sender actually sends.

    The cap applies to the *wire* bytes; a gzipped body is capped again on the way out of the
    decompressor (`decode.gunzip`), which is where a compression bomb would otherwise land.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise OtlpTooLargeError(int(declared), limit)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            raise OtlpTooLargeError(total, limit, exact=False)
        chunks.append(chunk)
    return b"".join(chunks)


def _partial_success(result: IngestResult, signal: str) -> dict:
    """The OTLP response body.

    `partialSuccess` reports records the server **could not process** — a datapoint with no
    timestamp, a request event with no request id. It deliberately does *not* report the
    ~99% of log records the accept filter drops (§6.2): those are refused on purpose, by
    config, and reporting them as rejections every batch would tell a correctly-behaving
    client it is misconfigured. The drop distribution goes to our logs instead, per event
    name, which is where an operator asking "what is this fleet sending?" can query it.
    """
    if not result.rejected:
        return {}
    return {"partialSuccess": {
        _REJECTED_FIELD[signal]: result.rejected,
        "errorMessage": ", ".join(f"{k}={v}" for k, v in sorted(result.reject_reasons.items())),
    }}


def _ok(body: dict, signal: str, as_protobuf: bool) -> Response:
    """200 + an `ExportServiceResponse`, in the encoding the request used.

    Spec compliance is load-bearing here rather than cosmetic: it is what lets an org's own
    OTel Collector sit in front of us as a gateway with zero Talium code (D-OT1).
    """
    if not as_protobuf:
        return JSONResponse(status_code=200, content=body)
    from google.protobuf.json_format import ParseDict

    if signal == "metrics":
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
            ExportMetricsServiceResponse as Resp,
        )
    else:
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
            ExportLogsServiceResponse as Resp,
        )
    return Response(status_code=200, content=ParseDict(body, Resp()).SerializeToString(),
                    media_type="application/x-protobuf")


def _retry(status: int, code: str, retry_after_s: int) -> JSONResponse:
    """429/503 + `Retry-After`. The OTel SDK honors it — backpressure is the protocol's, not
    ours to invent (§4.1)."""
    return JSONResponse(status_code=status, content={"code": code},
                        headers={"Retry-After": str(retry_after_s)})


def make_router(config: OtlpConfig, auth: OrgAuth | None = None,
                registry: MappingRegistry | None = None,
                guards: OtlpGuards | None = None) -> APIRouter:
    """The `/otlp` router, ready to mount on the ingest app.

    The mapping registry loads **here, at construction**: a mapping file that does not parse
    must stop the process from booting, not degrade real traffic to `vendor=unknown` one
    request at a time.
    """
    registry = registry or MappingRegistry.load(config.configs_dir)
    auth = auth or OrgAuth()
    receiver = OtlpReceiver(registry)
    guards = guards or OtlpGuards(config)
    inflight, rate = guards.inflight, guards.rate

    router = APIRouter(prefix="/otlp/v1", tags=["otlp"])

    def _ingest(signal: str, payload: dict, org_id: str) -> IngestResult:
        """Decode-to-rows in one transaction. Runs in a worker thread."""
        with session_scope() as s:
            if signal == "metrics":
                return receiver.ingest_metrics(payload, org_id, s)
            return receiver.ingest_logs(payload, org_id, s)

    async def _handle(request: Request, signal: str) -> Response:
        if not inflight.try_acquire():
            log.warning("otlp_shed", signal=signal, inflight=inflight.count,
                        limit=inflight.limit)
            return _retry(429, "shed", config.retry_after_s)
        try:
            authorization = request.headers.get("authorization")
            try:
                principal = await auth.verify_async(authorization)
            except OrgAuthError as exc:
                # No key material, not even a prefix: this is the one place an unauthenticated
                # caller controls the input, and a prefix is still key material (§13).
                log.warning("otlp_auth_failed", signal=signal, reason=exc.reason)
                return JSONResponse(status_code=401, content={"code": "unauthorized"})

            if not rate.allow(principal.org_id):
                log.warning("otlp_throttled", signal=signal, org_id=principal.org_id)
                return _retry(429, "rate_limited", config.retry_after_s)

            content_type = request.headers.get("content-type")
            as_protobuf = (content_type or "").split(";")[0].strip().lower() == \
                "application/x-protobuf"
            try:
                raw = await _read_body(request, config.max_body_bytes)
                payload = decode(raw, signal, content_type,
                                 request.headers.get("content-encoding"),
                                 config.max_body_bytes)
            except OtlpTooLargeError as exc:
                log.warning("otlp_body_too_large", signal=signal, org_id=principal.org_id,
                            size=exc.size, limit=exc.limit)
                return JSONResponse(status_code=413, content={"code": "body_too_large"})
            except OtlpDecodeError as exc:
                log.warning("otlp_malformed", signal=signal, org_id=principal.org_id,
                            reason=exc.reason)
                return JSONResponse(status_code=400, content={"code": "malformed"})

            result = await asyncio.to_thread(_ingest, signal, payload, principal.org_id)

            if result.vendor == UNKNOWN_VENDOR:
                # Alarm, not an error: metrics are stored and prunable, events were dropped.
                # A real fleet arriving unclaimed means a mapping file is missing (§4.6).
                log.warning("otlp_unknown_vendor", signal=signal, org_id=principal.org_id,
                            events_dropped=result.events_dropped_total,
                            points=result.points_written)
            log.info("otlp_batch_accepted", signal=signal, vendor=result.vendor,
                     org_id=principal.org_id, series=result.series_seen,
                     points=result.points_written, events_kept=result.events_kept,
                     events_dropped=dict(result.events_dropped), rejected=result.rejected)
            return _ok(_partial_success(result, signal), signal, as_protobuf)
        finally:
            inflight.release()

    @router.post("/metrics")
    async def post_metrics(request: Request) -> Response:
        return await _handle(request, "metrics")

    @router.post("/logs")
    async def post_logs(request: Request) -> Response:
        return await _handle(request, "logs")

    return router
