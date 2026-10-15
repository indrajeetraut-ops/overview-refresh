"""Canonical JSON helpers and stable series identities."""

from __future__ import annotations
import hashlib, json, re, unicodedata


def canonical_bytes(value) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode()


def identity(org_id, scope_type, scope_id, model):
    raw = json.dumps(
        [org_id, scope_type, scope_id, model], separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def slug(value) -> str:
    value = (
        unicodedata.normalize("NFKD", str(value))
        .encode("ascii", "ignore")
        .decode()
        .lower()
    )
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")[:48].rstrip("-")
    return value or "unnamed"


def path_name(org_id, scope_type, scope_id, scope_name, model):
    sid = identity(org_id, scope_type, scope_id, model)
    return f"{slug(scope_type)}-{slug(scope_name)}__{slug(model)}__{sid[:16]}.npy"
