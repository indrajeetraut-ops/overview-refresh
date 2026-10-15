"""OTLP attribute flattening -- the shape everything downstream of the wire speaks.

OTLP represents every attribute as a `KeyValue{key, value: AnyValue}`, where `AnyValue` is a
one-of. Nothing above the wire wants that: the mapping config, the series hash and the
residue blob all speak plain `{key: python value}` dicts. This module is the one place the
one-of is unwrapped, so protobuf and JSON bodies converge on the same dict before any vendor
logic runs.

Both encodings land here. In OTLP/JSON an int64 arrives as a *string* (JSON has no int64),
which is why `_coerce` in mapping.py parses through float rather than trusting the type.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

#: AnyValue one-of field -> whether it needs further unwrapping. Order matters only for
#: readability; a well-formed AnyValue sets exactly one.
_SCALAR_FIELDS = ("stringValue", "intValue", "doubleValue", "boolValue", "bytesValue")


def attr_value(any_value: Any) -> Any:
    """One OTLP `AnyValue` (JSON form or protobuf message) -> a python value.

    Unknown or empty variants yield None rather than raising: a payload we cannot read one
    attribute of is still a payload whose datapoints are worth storing (§4.1 -- intake
    availability over strictness).
    """
    if any_value is None:
        return None
    if not isinstance(any_value, dict):        # protobuf message: read through its fields
        any_value = _protobuf_any_value(any_value)
        if any_value is None:
            return None
    for field in _SCALAR_FIELDS:
        if field in any_value:
            value = any_value[field]
            # The JSON decoder parses floats as Decimal so that datapoint *values* keep their
            # exact decimal text (see decode._decode_json). Attributes are labels — nothing
            # sums them, they land in a JSONB column psycopg cannot serialize a Decimal into,
            # and the protobuf path produces plain floats. Normalizing here is what keeps the
            # two encodings' residue byte-identical.
            return float(value) if isinstance(value, Decimal) else value
    if "arrayValue" in any_value:
        return [attr_value(v) for v in (any_value["arrayValue"] or {}).get("values", [])]
    if "kvlistValue" in any_value:
        return flatten_attrs((any_value["kvlistValue"] or {}).get("values", []))
    return None


def _protobuf_any_value(message: Any) -> dict | None:
    """A `opentelemetry.proto.common.v1.AnyValue` message -> the JSON-shaped dict above.

    Read via `WhichOneof` rather than by probing attributes: protobuf returns a zero value
    for every unset scalar field, so `message.int_value` on a string attribute is 0, not
    absent -- probing would turn every string into an integer.
    """
    which = getattr(message, "WhichOneof", None)
    if which is None:
        return None
    field = which("value")
    if field is None:
        return {}
    value = getattr(message, field)
    json_name = {
        "string_value": "stringValue", "int_value": "intValue", "double_value": "doubleValue",
        "bool_value": "boolValue", "bytes_value": "bytesValue",
        "array_value": "arrayValue", "kvlist_value": "kvlistValue",
    }[field]
    if field == "array_value":
        return {"arrayValue": {"values": list(value.values)}}
    if field == "kvlist_value":
        return {"kvlistValue": {"values": list(value.values)}}
    return {json_name: value}


def flatten_attrs(attributes: Any) -> dict[str, Any]:
    """An OTLP `KeyValue` list -> `{key: value}`.

    Later keys win on a duplicate, which OTLP forbids but nothing enforces on the wire.
    """
    out: dict[str, Any] = {}
    for kv in attributes or []:
        key = kv.get("key") if isinstance(kv, dict) else getattr(kv, "key", None)
        if not key:
            continue
        raw = kv.get("value") if isinstance(kv, dict) else getattr(kv, "value", None)
        out[key] = attr_value(raw)
    return out
