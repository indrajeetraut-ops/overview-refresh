"""OTLP wire decoding: two encodings, one dict shape (otel-intake design §4.1 steps 2–3).

Claude Code exports `http/protobuf` by default and `http/json` on request, and an org's
fronting collector may re-encode either way — so both are served (D-OT5). Everything above
this module sees the **OTLP/JSON dict shape** regardless of what arrived: protobuf bodies are
converted through `MessageToDict`, which emits exactly that shape (camelCase names, int64 as
decimal strings).

Converting rather than walking the protobuf messages natively costs one extra pass per body
and buys a single parser downstream. At the reference org's <1 MB/s of protobuf that pass is
invisible next to the decode it follows (§6.2), and one parser is one place for a mapping bug
to live instead of two.

Note both encodings omit default-valued fields — an unset `asInt` means zero, not absent.
"""
from __future__ import annotations

import gzip
import zlib
from typing import Any

from ..errors import IngestError

#: OTLP/HTTP signal -> the protobuf request message that carries it.
_SIGNALS = ("metrics", "logs")


class OtlpDecodeError(IngestError):
    """A body that cannot be read at all -> 400 (§4.1: malformed is 400, unmapped is 200)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class OtlpTooLargeError(IngestError):
    """Body over `max_body_bytes` -> 413."""

    def __init__(self, size: int, limit: int, exact: bool = True) -> None:
        self.size = size
        self.limit = limit
        self.exact = exact
        super().__init__(f"body {'is' if exact else 'exceeds'} {size} bytes, limit {limit}")


def _request_class(signal: str):
    """Imported lazily and per call: `opentelemetry-proto` is a hard dependency of the OTLP
    routes but not of the ingest process as a whole, and a JSON-only deployment should not
    fail to boot because the protobuf bindings are missing."""
    if signal == "metrics":
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
            ExportMetricsServiceRequest,
        )
        return ExportMetricsServiceRequest
    if signal == "logs":
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
            ExportLogsServiceRequest,
        )
        return ExportLogsServiceRequest
    raise OtlpDecodeError(f"unsupported signal {signal!r}")


def gunzip(raw: bytes, limit: int) -> bytes:
    """Decompress, refusing to materialise more than `limit` bytes.

    Checked while inflating, not after: a gzip bomb's *compressed* size says nothing about
    what it expands to, so a limit applied to the result would be a limit applied after the
    memory was already spent.
    """
    obj = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    out: list[bytes] = []
    total = 0
    try:
        for start in range(0, len(raw), 65536):
            piece = obj.decompress(raw[start:start + 65536], limit + 1 - total)
            total += len(piece)
            if total > limit:
                raise OtlpTooLargeError(total, limit, exact=False)
            out.append(piece)
        tail = obj.flush()
    except zlib.error as exc:
        raise OtlpDecodeError(f"gzip: {exc}") from exc
    total += len(tail)
    if total > limit:
        raise OtlpTooLargeError(total, limit, exact=False)
    out.append(tail)
    return b"".join(out)


def decode(body: bytes, signal: str, content_type: str | None,
           content_encoding: str | None, max_body_bytes: int) -> dict[str, Any]:
    """Wire bytes -> the OTLP/JSON dict shape.

    Raises `OtlpTooLargeError` (413) and `OtlpDecodeError` (400). Never raises for records it
    merely cannot *map* — that is the receiver's job to count into `partialSuccess`.
    """
    if signal not in _SIGNALS:
        raise OtlpDecodeError(f"unsupported signal {signal!r}")

    if (content_encoding or "").lower() == "gzip":
        body = gunzip(body, max_body_bytes)
    elif len(body) > max_body_bytes:
        raise OtlpTooLargeError(len(body), max_body_bytes)

    media_type = (content_type or "").split(";")[0].strip().lower()
    if media_type == "application/x-protobuf":
        return _decode_protobuf(body, signal)
    if media_type == "application/json":
        return _decode_json(body)
    # An OTLP client always sets one of the two. Anything else is a misconfiguration worth
    # naming rather than a body worth guessing at.
    raise OtlpDecodeError(f"unsupported content-type {media_type or '(none)'!r}")


def _decode_protobuf(body: bytes, signal: str) -> dict[str, Any]:
    from google.protobuf.json_format import MessageToDict

    message = _request_class(signal)()
    try:
        message.ParseFromString(body)
    except Exception as exc:      # protobuf raises DecodeError and friends
        raise OtlpDecodeError(f"protobuf: {exc}") from exc
    return MessageToDict(message)


def _decode_json(body: bytes) -> dict[str, Any]:
    import json
    from decimal import Decimal

    try:
        # `parse_float=Decimal`: a cost of 0.9870844999999999 must survive as those digits,
        # not as the nearest binary double. Datapoint values are summed into money downstream
        # and the column they land in is NUMERIC for exactly this reason.
        data = json.loads(body, parse_float=Decimal)
    except (ValueError, UnicodeDecodeError) as exc:
        raise OtlpDecodeError(f"json: {exc}") from exc
    if not isinstance(data, dict):
        raise OtlpDecodeError("json: body is not an object")
    return data


def encode_protobuf(payload: dict[str, Any], signal: str) -> bytes:
    """The inverse, for tests and for a gateway that re-encodes. Kept beside `decode` so the
    two stay in step: the protobuf path is only tested honestly if the test bodies are real
    protobuf, not JSON wearing a protobuf Content-Type."""
    from google.protobuf.json_format import ParseDict

    message = ParseDict(payload, _request_class(signal)())
    return message.SerializeToString()


def gzip_bytes(raw: bytes) -> bytes:
    """Compress, for tests and callers building a gzipped body."""
    return gzip.compress(raw)
