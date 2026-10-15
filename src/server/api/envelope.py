"""The DASH §8.1 response envelope, implemented once so every endpoint carries it identically
(dashboard-api-implementation plan A1). Written before any endpoint uses it: retrofitting this
after the fact means touching every response body, and the drift is invisible until the UI
renders the wrong state for the wrong reason.

Three rules, all binding on every endpoint:

1. Every response carries mode -- `mode_block` (session-scoped) or `aggregate_mode_block`
   (aggregate). The UI cannot choose between `unavailable` and cold-start without this.
2. Every number resolved through the precedence chain (`precedence.py`) reports which source
   won -- `sourced`. A figure whose provenance the UI cannot name is one it cannot caption
   honestly.
3. A capability is absent, not empty -- `unavailable`, vs. plain `None` for cold-start
   (a subsystem that isn't computing yet, but will) vs. a real empty value for empty (the
   subsystem ran, there's nothing in range). These are three different truths and this module
   keeps their shapes distinct on purpose:

     * cold-start  -> `None`
     * empty       -> `[]` / `0` / `{}` -- whatever the real empty value is
     * unavailable -> `unavailable(block, requires)`'s shape, never `None` and never empty
"""
from __future__ import annotations

from typing import Any


def sourced(value: Any, source: str) -> dict:
    """Rule 2. Wraps a precedence-resolved figure with the source that produced it. Never
    return a bare number for anything `precedence.resolve()` touched."""
    return {"value": value, "source": source}


def unavailable(block: str, requires: str) -> dict:
    """Rule 3. `block` names the capability that can't be computed (e.g. "by_query_source",
    "provenance"); `requires` names what would make it available (e.g. "otel", "jsonl") --
    modes doc §5 rule 1: name the source, not the user. Never `0`, never `[]` -- those are
    measurements, and this is the absence of one."""
    return {"unavailable": {"block": block, "requires": requires}}


def mode_block(datasource: str | None, vendor: str) -> dict:
    """Rule 1, session-scoped responses (A5, and each row of A4's list)."""
    return {"datasource": datasource, "vendor": vendor}


def aggregate_mode_block(modes_present: list[str], sessions_by_mode: dict[str, int]) -> dict:
    """Rule 1, aggregate responses (A3, A7) -- a timeframe can span sessions in more than one
    mode at once, so there is no single `datasource` to report; the UI needs the full set."""
    return {"modes_present": modes_present, "sessions_by_mode": sessions_by_mode}
