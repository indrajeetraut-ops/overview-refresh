"""Derives the normalized-event shape from real normalizer output.

The published contract is *derived*, never hand-written. A hand-written schema is wrong the
moment someone adds a field and forgets the doc; a derived one can only be wrong if nobody
regenerates it, which is exactly what the drift guard catches.

Two consumers, one implementation:

  * `tests/collector/normalizer/test_shape_contract.py` derives from the fixture corpus and
    fails when the committed artifacts stop matching -- the staleness alarm.
  * `cli_publish_payload.py` regenerates the committed artifacts, and can also derive a shape
    from any real normalized dump to compare real data against the published contract.

This module knows nothing about fixtures or files. It takes sessions and returns dicts.
"""
from __future__ import annotations

from typing import Iterable

from .descriptions import EVENT_DOCS, FIELD_DOCS, OUTER_DOCS

__all__ = ["OPAQUE_KEYS", "derive_shape", "diff_shape", "worked_example"]

# Keys whose value is an open/vendor structure: record the value's type, do not walk into it.
# `raw` is the lossless original record and `input` is a vendor tool's arbitrary argument
# object -- both are open by design, so descending into them would pin the
# vendor's shape as if it were ours and make the guard fire on their changes, not ours.
OPAQUE_KEYS = {"raw", "input", "server_tool_use"}


def _type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):  # before int -- bool is an int subclass
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _collect(path: str, key: str, value, out: dict[str, set[str]]) -> None:
    out.setdefault(path, set()).add(_type_name(value))
    if key in OPAQUE_KEYS:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _collect(f"{path}.{k}", k, v, out)
    elif isinstance(value, list):
        for item in value:
            _collect(f"{path}[]", key, item, out)  # element shape shares the list's key


def derive_shape(sessions: Iterable, *, schema_version: str) -> dict:
    """{event_type -> {payload key-path -> sorted value types}}, unioned over `sessions`.

    Union, not intersection: a field that is `null` in one session and `str` in another is
    recorded as both, which is the honest description of what a consumer must handle."""
    outer: dict[str, set[str]] = {}
    payload_by_type: dict[str, dict[str, set[str]]] = {}
    for session in sessions:
        for event in session.events:
            for field in ("event_type", "record_id", "parent_id", "event_ts"):
                outer.setdefault(field, set()).add(_type_name(getattr(event, field)))
            per_type = payload_by_type.setdefault(event.event_type, {})
            for k, v in event.payload.items():
                _collect(k, k, v, per_type)
    return {
        "schema_version": schema_version,
        "outer": {
            k: {"types": sorted(v), "description": OUTER_DOCS.get(k)}
            for k, v in sorted(outer.items())
        },
        "payload_by_type": {
            etype: {
                "description": EVENT_DOCS.get(etype),
                "fields": {
                    path: {
                        "types": sorted(types),
                        "description": FIELD_DOCS.get(etype, {}).get(path),
                    }
                    for path, types in sorted(paths.items())
                },
            }
            for etype, paths in sorted(payload_by_type.items())
        },
    }


def diff_shape(expected: dict, actual: dict) -> list[str]:
    """Human-readable, actionable differences. Empty list means the shapes agree."""
    problems: list[str] = []

    if expected.get("schema_version") != actual.get("schema_version"):
        problems.append(
            f"schema_version {expected.get('schema_version')} -> {actual.get('schema_version')}"
        )

    exp_types = set(expected["payload_by_type"])
    act_types = set(actual["payload_by_type"])
    for missing in sorted(exp_types - act_types):
        problems.append(f"event_type '{missing}' no longer produced")
    for added in sorted(act_types - exp_types):
        problems.append(f"new event_type '{added}'")

    for etype in sorted(exp_types & act_types):
        exp_paths = expected["payload_by_type"][etype]["fields"]
        act_paths = actual["payload_by_type"][etype]["fields"]
        for path in sorted(set(exp_paths) - set(act_paths)):
            problems.append(f"{etype}.{path}: field removed (was {exp_paths[path]['types']})")
        for path in sorted(set(act_paths) - set(exp_paths)):
            problems.append(f"{etype}.{path}: new field (types {act_paths[path]['types']})")
        for path in sorted(set(exp_paths) & set(act_paths)):
            # Types and prose are reported apart: a stale description is a real drift, but it
            # is a different fix from a changed type and should not read as one.
            if exp_paths[path]["types"] != act_paths[path]["types"]:
                problems.append(
                    f"{etype}.{path}: types {exp_paths[path]['types']} -> {act_paths[path]['types']}"
                )
            elif exp_paths[path]["description"] != act_paths[path]["description"]:
                problems.append(f"{etype}.{path}: description changed -- regenerate")
        if expected["payload_by_type"][etype]["description"] != actual["payload_by_type"][etype]["description"]:
            problems.append(f"{etype}: event description changed -- regenerate")

    if expected["outer"] != actual["outer"]:
        problems.append(f"outer event fields changed: {expected['outer']} -> {actual['outer']}")
    return problems


def worked_example(sessions: Iterable, *, schema_version: str) -> dict:
    """One real event per event_type, for a reader who wants to see values rather than types.

    Complements the schema, does not replace it: an example only shows the fields it happens
    to contain, so a `null` status or an absent `tool_denial_kind` reads as "does not exist".
    The schema is the contract; this is the illustration.

    `raw` is elided to a note -- it is the full vendor record, routinely kilobytes, and
    reproducing it here would bury the fields the example exists to show."""
    chosen: dict[str, dict] = {}
    for session in sessions:
        for event in session.events:
            if event.event_type in chosen:
                continue
            payload = dict(event.payload)
            if "raw" in payload:
                payload["raw"] = "<full vendor record, elided>"
            chosen[event.event_type] = {
                "event_type": event.event_type,
                "record_id": event.record_id,
                "parent_id": event.parent_id,
                "event_ts": event.event_ts,
                "payload": payload,
            }
    return {
        "schema_version": schema_version,
        "note": (
            "One representative event per event_type, from the normalizer's own fixture corpus. "
            "Illustration only -- normalized_schema.json is the contract, and covers fields this "
            "sample happens not to exercise. payload.raw is elided: it is the full vendor record, "
            "routinely kilobytes, and would bury the fields this example exists to show."
        ),
        "events_by_type": {etype: chosen[etype] for etype in sorted(chosen)},
    }
