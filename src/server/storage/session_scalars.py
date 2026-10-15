"""Per-session scalars derived from session_info's JSON (api-latency plan D3/D6). THE
definition of each value: the SQL mirrors in config/queries/*.yaml (Phase C) and the backfill
in the Phase E migration must agree with these; tests/server/storage/test_session_scalars.py
pins that.

Pure functions, no DB access -- importable from `api`, `worker` and `storage` alike. `api`
already imports `server.storage`; this module must never import `api` back.
"""
from __future__ import annotations


def priced_total(block: dict | None, key: str) -> int:
    """A request-level bucket's fold-priced total. The fold's own total is all-or-nothing
    (`None` the moment any event is unpriced), so an unpriced block contributes nothing rather
    than a partial sum. Moved verbatim from `manager/aggregate.py::_priced_total`."""
    return (block or {}).get(key) or 0


def recoverable_buckets(info: dict | None) -> dict[str, int]:
    """The three recoverable-spend buckets (efficiency note §2), UNCAPPED (D4) -- the
    per-session cap against `cost_effective_usd_micros` stays a read-time concern
    (`manager/aggregate.py::_session_buckets`), since that cap mixes this fold-owned value
    with the true-up-owned cost column and there is no single write-time point where both are
    current."""
    return {
        "resume": priced_total(info.get("resume_cost") if info else None, "total_recoverable_usd_micros"),
        "cache_bust": priced_total(info.get("cache_busts") if info else None, "total_usd_micros"),
        "interrupted": priced_total(info.get("interrupted") if info else None, "total_usd_micros"),
    }


def reasoning_tokens(info: dict | None) -> int | None:
    """`None` means "never estimated", not "estimated as zero" -- read defensively rather than
    defaulted, same posture as `manager/aggregate.py::reasoning_share`'s own docstring."""
    return (info or {}).get("reasoning_tokens")


def has_files(files: dict | None) -> bool:
    return bool(files or {})


def segment_turns(seg: dict) -> int:
    """A segment's `turns` as a count (D8): already a number on data written after the
    api-latency plan's Phase 0 fix, still a list of turn indices on anything folded or
    generated before it -- `len()` reads the same information either way. Any other shape
    (missing, non-numeric) is 0, never an error."""
    turns = seg.get("turns")
    if isinstance(turns, list):
        return len(turns)
    if isinstance(turns, (int, float)):
        return int(turns)
    return 0


def dominant_segment_label(segments: list | None) -> str | None:
    """Reduces a session's `info.segments` to one representative type: the label of the
    segment with the most turns (the phase the session mostly consisted of) -- the phase the
    session mostly consisted of. Turns, not cost, so the pick doesn't depend on pricing being
    available. `max()`'s own tie-break (first max wins) matches the SQL mirror's `ORDER BY
    ... DESC, ordinality`. Moved from `manager/aggregate.py`."""
    if not segments:
        return None
    return max(segments, key=segment_turns).get("label")


def otel_vendor_cost(otel: dict | None) -> int | None:
    """Available only when **every** priced request in the session is vendor-reported --
    `cost_usd_micros` (the vendor-only sum) is the true total exactly in that case. Moved
    verbatim from `precedence.py::_otel_vendor_cost`'s body."""
    basis = (otel or {}).get("cost_basis") or {}
    if not basis or basis.get("estimated", 0) or basis.get("unknown", 0):
        return None
    return (otel or {}).get("cost_usd_micros")


def fold_scalars(info: dict | None, files: dict | None) -> dict:
    """The fold-owned column values (Phase E): everything `persist_session_info` and the
    synthetic loader write, derived from `info`/`files` alone -- never touched by the true-up
    (`otel_vendor_cost_usd_micros` is the true-up's own column, set separately)."""
    buckets = recoverable_buckets(info)
    return {
        "recoverable_resume_usd_micros": buckets["resume"],
        "recoverable_cache_bust_usd_micros": buckets["cache_bust"],
        "recoverable_interrupted_usd_micros": buckets["interrupted"],
        "reasoning_tokens": reasoning_tokens(info),
        "has_files": has_files(files),
        "session_type": dominant_segment_label((info or {}).get("segments") or []),
    }
