"""The precedence resolver (modes doc §3.3, moved here from PM.6 -- nothing in PM consumed it,
and config with no reader drifts from the code it's meant to govern).

Token and cost sources are ordered chains, resolved **per session**, first available wins.
`resolve()` takes one session's already-fetched figures and a chain name from
`config/api.yaml`'s `dashboard.precedence.*`, and returns `(value, source)` -- never a bare
number (envelope rule 2: a figure whose provenance can't be named can't be captioned honestly).

**`session_row`** is anything with these attributes (a `SessionInfo` ORM instance, a manager
scope-fetch row, or an equivalent mapping): `otel` (the true-up's block, or `{}`) OR its
extracted scalar `otel_vendor_cost_usd_micros` (api-latency plan D3 -- most manager rows carry
only the scalar, post-Phase-C), `models` (the JSONL fold's `{model: {input, output, cache_read,
cache_write}}`), `cost_effective_usd_micros`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Callable
from zoneinfo import ZoneInfo

from server.storage import session_scalars

#: Sentinel distinguishing "row has no such attribute" from "row has the attribute, set to
#: None" -- `getattr(row, name, _ABSENT)` below needs the former to fall through to the dict
#: path, not treat a genuinely-None scalar as absent.
_ABSENT = object()

#: canonical measure -> the ledger record field carrying the same quantity. Two different
#: vocabularies for the same four numbers: `otel.tokens`/`measures` uses the metrics fold's
#: names (otel_trueup.py TOKEN_MEASURES), `otel.requests[]` uses the ledger's own field names.
_MEASURE_FIELDS = ("tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_creation")
_LEDGER_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")


def _otel_measures_tokens(session_row: Any) -> int | None:
    """Sum of the metrics fold's token measures -- available in every mode that has OTel
    metrics at all, including `otel_only` with zero request rows (a metrics-only fleet).

    Availability is keyed on `otel["measures"]` (the sparse dict `_sum_by_measure` produced --
    empty when the metrics fold never ran for this session), **not** `otel["tokens"]`: that key
    is always present in the block with zero-defaults for all four measures
    (`build_cost_block`'s `{m: _fmt(measures.get(m, Decimal(0))) for m in TOKEN_MEASURES}`), so
    its mere presence -- or even a string value of `"0"`, which is truthy -- says nothing about
    whether the metrics fold ran at all.
    """
    otel = getattr(session_row, "otel", None) or {}
    if not otel.get("measures"):
        return None
    tokens = otel.get("tokens") or {}
    return sum(int(Decimal(str(tokens.get(m) or 0))) for m in _MEASURE_FIELDS)


def _otel_ledger_tokens(session_row: Any) -> int | None:
    """Sum across the per-request ledger -- available whenever OTel saw at least one request,
    metrics-only fleets excepted (they have no ledger, only `_otel_measures_tokens`)."""
    otel = getattr(session_row, "otel", None) or {}
    requests = otel.get("requests")
    if not requests:
        return None
    return sum(int(r.get(f) or 0) for r in requests for f in _LEDGER_FIELDS)


def _jsonl_models_tokens(session_row: Any) -> int | None:
    """Sum across the JSONL fold's per-model rollup -- the only source in `jsonl_only`."""
    models = getattr(session_row, "models", None) or {}
    if not models:
        return None
    return sum(
        int(m.get("input") or 0) + int(m.get("output") or 0)
        + int(m.get("cache_read") or 0) + int(m.get("cache_write") or 0)
        for m in models.values()
    )


def _otel_vendor_cost(session_row: Any) -> int | None:
    """Available only when **every** priced request in the session is vendor-reported --
    `cost_usd_micros` (the vendor-only sum) is the true total exactly in that case. A session
    with even one estimated or unknown-cost request needs the blended figure instead
    (`_estimated_cost`), because the vendor-only sum would silently omit those requests.

    Reads the row's own `otel_vendor_cost_usd_micros` scalar (api-latency plan D3) when the
    query supplied it -- every manager scope fetch does, post-Phase-C. Falls back to
    `session_scalars.otel_vendor_cost` over the raw `otel` dict for `dashboard.session_detail`
    and the other single-session reads that still select `otel` directly. Both paths share one
    definition (`session_scalars.otel_vendor_cost`)."""
    v = getattr(session_row, "otel_vendor_cost_usd_micros", _ABSENT)
    if v is not _ABSENT:
        return v
    return session_scalars.otel_vendor_cost(getattr(session_row, "otel", None))


def _estimated_cost(session_row: Any) -> int | None:
    """The fallback rung. `cost_effective_usd_micros` -- not `cost_est_usd_micros` alone --
    because the true-up already blends vendor and estimated figures per request (MODES §3.1);
    reporting only the estimate-only sum here would double-omit the vendor-priced requests a
    mixed session also has. The `source` name still communicates "some or all of this total
    came from the estimator", which is the caption's job."""
    return getattr(session_row, "cost_effective_usd_micros", None)


def _otel_measures_active_time(session_row: Any) -> float | None:
    """Z1's "Active time" (DASH §3.1): the OTel `active_time.total` counter, exact. Same
    availability trap as `_otel_measures_tokens` -- keyed on `otel["measures"]`, since
    `otel["active_time_s"]` (PM.4's top-level key) is always present with a zero default."""
    otel = getattr(session_row, "otel", None) or {}
    if "active_time_s" not in (otel.get("measures") or {}):
        return None
    return float(otel.get("active_time_s") or 0)


def _turn_duration_active_time(session_row: Any) -> float | None:
    """The JSONL fallback: Σ turn durations. Exact only with OTel; this is wall-clock spent in
    turns, a different (looser) quantity DASH §3.1 accepts as the fallback."""
    turns = getattr(session_row, "turns", None) or []
    if not turns:
        return None
    total_ms = sum(t.get("duration_ms") or 0 for t in turns)
    return total_ms / 1000 if total_ms else None


#: Not in `config/api.yaml` -- MODES §3.3 only names `tokens`/`cost` as configurable chains.
#: Active time's two rungs are fixed by DASH §3.1 itself, so the chain is a constant here
#: rather than a config knob nothing else would ever want to override.
ACTIVE_TIME_CHAIN = ["otel_measures_active_time", "turn_duration"]

_RESOLVERS: dict[str, Callable[[Any], int | float | None]] = {
    "otel_measures": _otel_measures_tokens,
    "otel_ledger": _otel_ledger_tokens,
    "jsonl_models": _jsonl_models_tokens,
    "otel_vendor": _otel_vendor_cost,
    "estimated": _estimated_cost,
    "otel_measures_active_time": _otel_measures_active_time,
    "turn_duration": _turn_duration_active_time,
}


def resolve(session_row: Any, chain: list[str]) -> tuple[int | float, str] | None:
    """First available wins. `None` if nothing in the chain has a value for this session --
    callers should not normally see that for a touched session, but a precondition violation
    should surface as "nothing resolved", not a wrong number."""
    for source in chain:
        resolver = _RESOLVERS[source]
        value = resolver(session_row)
        if value is not None:
            return value, source
    return None


def resolve_aggregate(session_rows: list[Any], chain: list[str]) -> tuple[int | float, list[str]]:
    """Aggregates resolve **per session, then sum** (A2's own rule) -- resolving once for a
    whole timeframe would pick one source for a mixed-mode range and silently drop the
    sessions that source cannot see. The reported source is therefore the *set* that actually
    contributed, not a single scalar."""
    total = 0
    sources: set[str] = set()
    for row in session_rows:
        resolved = resolve(row, chain)
        if resolved is None:
            continue
        value, source = resolved
        total += value
        sources.add(source)
    return total, sorted(sources)


def _parse_ts(value: str | None) -> datetime | None:
    """Same parse the JSONL fold itself uses (`fold/turns.py`'s `_parse_ts`) -- re-declared
    rather than imported across the worker/API boundary those two processes otherwise share
    no code across."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _turn_started_hours(session_row: Any, tz: ZoneInfo) -> list[int] | None:
    """R3's first rung: one point per turn, from `turns[].started`. Structurally root-session
    only -- `session_info.turns` is empty on a sub-agent's own row
    (`schema/session_info.py:20`) -- which is what scopes this rung to a user's own interactive
    activity rather than delegated sub-agent work, with no extra filtering needed here.

    Hours are in `tz` (the caller's resolved `users.timezone`, UTC when unset) -- stored
    timestamps are UTC throughout, `tz` only changes which wall-clock hour a UTC instant reads
    as."""
    turns = getattr(session_row, "turns", None) or []
    hours = [ts.astimezone(tz).hour for t in turns
            if (ts := _parse_ts(t.get("started"))) is not None]
    return hours or None


def _session_started_hour(session_row: Any, tz: ZoneInfo) -> list[int] | None:
    """R3's fallback rung, for `otel_only`: per-request OTel timestamps don't survive the
    fold -- the true-up's ledger drops `event_ts` once it has priced the request against it
    (FEAS §3) -- so one point per session, from `started_at`, is the coarsest honest signal
    left. A 4-hour session starting at 9pm local contributes only to the 9pm bucket.

    See `_turn_started_hours` for `tz`."""
    started_at = getattr(session_row, "started_at", None)
    if started_at is None:
        return None
    return [started_at.astimezone(tz).hour]


#: R3's own chain, fixed here rather than `config/api.yaml` -- same reasoning as
#: `ACTIVE_TIME_CHAIN`: DASH's two rungs are fixed by design, not a config knob a deployment
#: would ever want to reorder.
PEAK_HOUR_CHAIN = ["turn_started", "session_started"]

_HOUR_RESOLVERS: dict[str, Callable[[Any, ZoneInfo], list[int] | None]] = {
    "turn_started": _turn_started_hours,
    "session_started": _session_started_hour,
}


def resolve_hour_histogram(session_rows: list[Any], tz: ZoneInfo,
                          chain: list[str] = PEAK_HOUR_CHAIN) -> tuple[list[int], list[str]]:
    """Same per-session, first-available-wins resolution as `resolve_aggregate`, but the
    payload is one or more hour buckets rather than a single number to sum -- returns a
    24-length histogram (index = hour in `tz`) and the set of rungs that actually contributed.
    A session with turns contributes one point per turn; a session that falls back to
    `session_started` contributes exactly one."""
    histogram = [0] * 24
    sources: set[str] = set()
    for row in session_rows:
        for source in chain:
            hours = _HOUR_RESOLVERS[source](row, tz)
            if hours is None:
                continue
            for h in hours:
                histogram[h] += 1
            sources.add(source)
            break
    return histogram, sorted(sources)
