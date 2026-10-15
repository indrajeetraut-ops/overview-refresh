"""Framework-free business logic for the **single-user** dashboard read surface (ground rule
1; the manager/aggregation surface lives in `server/api/manager/`). Every function here takes
plain arguments (`Session`, `QueryCatalog`, ids, config) and returns plain dicts -- no FastAPI
types cross into this module, which is what makes it testable without an HTTP client
(`server/uploader/service.py` is the precedent).

Endpoint bodies land here as they're implemented; `app.py`'s route handlers stay thin wrappers
that call into this module and translate `errors.py` exceptions to HTTP.

TODO(api-split): this file is large -- the `_insight_*` family (~half of it) can move to a
`single_user/insights.py` sibling once the manager tracks stop churning shared helpers.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session as DbSession

from common import git_ident
from server import directory

from .. import precedence
from ..config import ApiConfig
from ..envelope import aggregate_mode_block, mode_block, sourced, unavailable
from ..errors import BadRequestError, NotFoundError
from ..manager import aggregate as manager_aggregate
from ..manager import budget_cycles
from ..manager import budgets as budgets_service

#: The `by_query_source`/ledger sentinel for "no query_source" (otel_trueup.py's
#: `QUERY_SOURCE_INTERACTIVE`) -- re-declared rather than imported across the process boundary
#: between the worker and the API, which otherwise share no code.
_QUERY_SOURCE_INTERACTIVE = "interactive"


def _resolve_tz(tz_name: str | None) -> ZoneInfo:
    """`users.timezone` -> a usable zone, UTC when unset or unrecognized -- the exact behavior
    every bucketing call site had before this column existed, so a user with no preference
    recorded yet sees no change at all."""
    if not tz_name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _timezone_for(s: DbSession, catalog, user_id: str) -> ZoneInfo:
    row = s.execute(catalog.get("dashboard", "get_user_timezone"), {"user_id": user_id}).first()
    return _resolve_tz(row.timezone if row is not None else None)


def _fetch_sessions_in_range(s: DbSession, catalog, user_id: str,
                             frm: datetime, to: datetime) -> list:
    # TODO(single-user-slim-fetch): `sessions_in_range` still selects turns/files/subagents/
    # otel/models in full for peak hours, repo file stats and the tokens/active-time chains --
    # real otel is the biggest column even at one user's row count. See
    # docs/plan/2026-09-26-api-latency.md §8.
    return s.execute(catalog.get("dashboard", "sessions_in_range"),
                     {"user_id": user_id, "from": frm, "to": to}).all()


def _jsonl_capable(datasource: str | None) -> bool:
    """Whether a session_info row's JSONL-derived fields (tools/models/skills/files/info.*)
    are structurally present, vs. `unavailable`.

    **Not** `datasource in ("jsonl_only", "both")` -- a session the JSONL fold has folded but
    the true-up has never ticked for has `datasource is None` (PM ground rule 2: "a row
    created before any tick has no mode yet, and None is the honest value"), and its JSONL
    fields are fully populated regardless. Only `datasource == "otel_only"` -- a row the
    true-up's own stub path created (`_ensure_session_row`), which the JSONL fold never
    touched at all -- structurally lacks them. Every other value, including `None`, means the
    JSONL fold ran.
    """
    return datasource != "otel_only"


def _has_jsonl_capable_session(rows: list) -> bool:
    return any(_jsonl_capable(r.datasource) for r in rows)


def _files_touched(rows: list) -> dict:
    created = edited = 0
    for r in rows:
        for info in (r.files or {}).values():
            if info.get("created"):
                created += 1
            else:
                edited += 1
    return {"created": created, "edited": edited, "total": created + edited}


def _distinct_repos(rows: list) -> list[str]:
    """Distinct `repo_id`s (dashboard plan §2/§3), not the fragile display-only `repo_key` --
    two worktrees or two differently-pathed clones of the same remote now collapse into one
    entry instead of inflating the count. `repo_id` is `None` for a session with no evidence
    anywhere (never guessed), so those contribute nothing here, same as before."""
    return sorted({r.repo_id for r in rows if getattr(r, "repo_id", None)})


def _is_root(row: Any) -> bool:
    """FEAS §4/R0: a sub-agent session carries the same `user_id` as its parent and gets its
    own full `sessions` row (`fold_handler.py:57`), so a *count* of sessions must exclude them
    or a fan-out session reads as N+1. Every *sum* (tokens, cost, tools, ...) stays over every
    row -- own-session fields are not rolled up into the parent anywhere in the system (R0's
    note in the implementation plan has the full reasoning)."""
    return row.parent_session_id is None


def _money(usd_micros: int | None, sources: list[str]) -> dict:
    """The `Money` envelope shape (`schemas/envelope.py`) -- duplicated from
    `manager/service.py::_money` rather than imported (that module is the manager surface's
    own private helper, and this one has no other manager coupling; ground rule 1 keeps this
    file framework-free and import-light). `None` (not `0`) when there is nothing to report."""
    if usd_micros is None or not sources:
        return {"usd_micros": None, "cost_basis": None, "cost_complete": None}
    return {
        "usd_micros": usd_micros,
        "cost_basis": ",".join(sources),
        "cost_complete": "estimated" not in sources,
    }


def _z1_headline(rows: list, pr_rows: list, config: ApiConfig) -> dict:
    """DASH §3.1. Cost/tokens/sessions/active-time are Mode: all -- computed regardless.
    Sub-agents/turns/files/repos are Mode: jsonl,both -- `unavailable` as a whole when the
    range has no jsonl-capable session at all (an otel_only-only range), per the doc's own
    "the strip renders its first four rows and drops the rest" rule. When the range is mixed,
    a real sum is correct as-is: an otel_only session's session_info row carries zero-valued
    defaults for these fields (never folded), so it contributes nothing without special-casing.

    `sessions`/`active_sessions` count roots only (`_is_root`); every other figure here sums
    over all rows, including sub-agent children -- that asymmetry is intentional, not a bug.

    `merged_prs`/`cost_per_pr` (U5-myusage): this person's own merged PRs in range, the same
    `manager_aggregate.merged_pr_count`/`blended_cost_per_pr_usd_micros` pair `person_detail`
    already uses (A5) -- reused here rather than re-derived, just scoped to one user's own
    `pr_rows` instead of a team's.
    """
    tokens_value, tokens_sources = precedence.resolve_aggregate(rows, config.dashboard.precedence.tokens)
    cost_value, cost_sources = precedence.resolve_aggregate(rows, config.dashboard.precedence.cost)
    active_time = precedence.resolve_aggregate(rows, precedence.ACTIVE_TIME_CHAIN) if rows else (0, [])
    merged_prs = manager_aggregate.merged_pr_count(pr_rows)
    cost_per_pr = manager_aggregate.blended_cost_per_pr_usd_micros(cost_value, merged_prs)

    z1: dict[str, Any] = {
        "tokens": sourced(tokens_value, ",".join(tokens_sources) or None),
        "cost_usd_micros": sourced(cost_value, ",".join(cost_sources) or None),
        "sessions": sum(1 for r in rows if _is_root(r)),
        "active_sessions": sum(1 for r in rows if r.status == "live" and _is_root(r)),
        "active_time_s": sourced(active_time[0], ",".join(active_time[1]) or None),
        "merged_prs": merged_prs,
        "cost_per_pr": _money(cost_per_pr, cost_sources),
    }

    if _has_jsonl_capable_session(rows):
        # Summed over every row, children included -- not a miss. `subagents_spawned` reads
        # each root's own `subagents` map (a child's own map, if it has grandchildren, is a
        # separate count); `turns`/`files_touched`/`repos` are own-session fields nowhere else
        # rolled up, so summing all rows is the only way to get the true total (FEAS §4/R0).
        z1["subagents_spawned"] = sum(len(r.subagents or {}) for r in rows)
        z1["turns"] = sum(r.turn_count or 0 for r in rows)
        z1["long_turns"] = sum(r.long_turn_count or 0 for r in rows)
        z1["files_touched"] = _files_touched(rows)
        z1["repos"] = _distinct_repos(rows)
    else:
        reason = unavailable("z1_structure", "jsonl")
        z1["subagents_spawned"] = reason
        z1["turns"] = reason
        z1["long_turns"] = reason
        z1["files_touched"] = reason
        z1["repos"] = reason

    return z1


def _by_query_source_aggregate(rows: list) -> dict | None:
    """DASH §3.2's background-call split, summed across the range and collapsed into exactly
    two buckets: `interactive` and `sdk`. The worker's per-session block still names the exact
    OTel `query_source` value (`sdk`, `agent:builtin:Explore`, `prompt_suggestion`, ...) --
    finer than a user can act on, since which subsystem issued a background call is not
    something they control. Folded here, at presentation time, not in the stored block, so no
    refold is needed to change the grouping again later.

    `interactive` is correct by construction, not just for OTel-observed requests: a JSONL-only
    request is *always* interactive (background calls never reach a JSONL transcript at all --
    Claude Code only writes the user-facing conversation there), so a JSONL-only session's
    entire `by_query_source` (unconditionally the `interactive` sentinel, `otel_trueup.py`'s
    `QUERY_SOURCE_INTERACTIVE`) belongs in this bucket, not a separate "unknown" one.

    `sdk`, by contrast, is a lower bound, not a total: it can only ever count what an OTel-
    observed request reported, and JSONL-only sessions contribute nothing to it even if they
    made background calls -- there is structurally no way to know. `None` (caller wraps as
    `unavailable`) when no session in range has any OTel evidence at all -- `jsonl_only`, per
    PM.3's own rule that this key is absent, not empty, in that mode."""
    merged: dict[str, dict] = {}
    any_present = False
    for r in rows:
        bqs = (r.otel or {}).get("by_query_source")
        if not bqs:
            continue
        any_present = True
        for source, stats in bqs.items():
            key = source if source == _QUERY_SOURCE_INTERACTIVE else "sdk"
            bucket = merged.setdefault(key, {"requests": 0, "cost_usd_micros": 0,
                                             "cost_est_usd_micros": 0,
                                             "cost_effective_usd_micros": 0})
            bucket["requests"] += stats.get("requests", 0)
            bucket["cost_usd_micros"] += stats.get("cost_usd_micros", 0)
            bucket["cost_est_usd_micros"] += stats.get("cost_est_usd_micros", 0)
            # Vendor, else estimate, per request -- never both. `cost_usd_micros` +
            # `cost_est_usd_micros` above are kept for transparency but must never be summed
            # together for display: `cost_est_usd_micros` is populated even on a request that
            # already has a real vendor cost (otel_trueup.py's `_join_requests`), so adding them
            # double-counts every vendor-priced request's spend.
            bucket["cost_effective_usd_micros"] += stats.get("cost_effective_usd_micros", 0)
    return merged if any_present else None


def _otel_coverage(rows: list) -> dict:
    """How many sessions in range actually carry OTel request-level telemetry (`datasource`
    `both`/`otel_only`) vs. the range's total. The `sdk` bucket above is only ever a lower
    bound -- this is what lets the UI say so with real counts instead of a generic caveat."""
    return {
        "sessions_with_otel": sum(1 for r in rows if r.datasource in ("both", "otel_only")),
        "sessions_total": len(rows),
    }


def _z2_consumption(rows: list) -> dict:
    by_query_source = _by_query_source_aggregate(rows)
    return {
        "model_split": manager_aggregate.model_split(rows),
        "by_query_source": by_query_source if by_query_source is not None
                           else unavailable("by_query_source", "otel"),
        # Only meaningful alongside a real split -- absent (not zeroed) when by_query_source
        # itself is unavailable, so the UI never captions a split that doesn't exist.
        "by_query_source_coverage": _otel_coverage(rows) if by_query_source is not None else None,
        # Provenance decomposition (DASH §5) is not computed by any phase yet -- cold-start,
        # not mode-gated: no deployment mode makes this available today.
        "provenance_mix": None,
    }


def _ranked(agg: dict[str, dict], rank_key: str, id_key: str, limit: int) -> list[dict]:
    ranked = sorted(agg.items(), key=lambda kv: -kv[1][rank_key])[:limit]
    return [{id_key: name, **stats} for name, stats in ranked]


def _top_tools(rows: list, limit: int) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        for name, info in (r.tools or {}).items():
            bucket = agg.setdefault(name, {"count": 0, "failed": 0})
            bucket["count"] += info.get("count", 0)
            bucket["failed"] += info.get("failed", 0)
    return _ranked(agg, "count", "name", limit)


def _top_skills(rows: list, limit: int) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        for skill in (r.skills or []):
            name = skill.get("skill_name")
            if not name:
                continue
            bucket = agg.setdefault(name, {"count": 0, "tokens": 0})
            bucket["count"] += skill.get("count", 1)
            bucket["tokens"] += skill.get("tokens") or 0
    return _ranked(agg, "count", "name", limit)


def _top_mcp_servers(rows: list, limit: int) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        by_server = ((r.info or {}).get("mcp_servers") or {}).get("by_server") or {}
        for server, stats in by_server.items():
            bucket = agg.setdefault(server, {"calls": 0, "bytes_read_total": 0})
            bucket["calls"] += stats.get("calls", 0)
            bucket["bytes_read_total"] += stats.get("bytes_read_total", 0)
    return _ranked(agg, "calls", "name", limit)


def _top_repos(rows: list, limit: int) -> list[dict]:
    """Counts root sessions per `repo_id` -- same fan-out exclusion as `_z1_headline`'s
    `sessions` (FEAS §4/R0): a fan-out session's sub-agents share its repo and would otherwise
    multiply that repo's count by however many children it spawned. Grouped by the stable
    `repo_id` (dashboard plan §2/§3), not the fragile display-only `repo_key` -- two worktrees
    or two clones of the same remote collapse into one entry instead of inflating the count.
    `repo_key` in the returned shape is now a display label derived from `repo_id`
    (`git_ident.describe`), kept as the field name so the frontend needs no change."""
    agg: dict[str, int] = {}
    for r in rows:
        if not _is_root(r):
            continue
        repo_id = getattr(r, "repo_id", None)
        if repo_id:
            agg[repo_id] = agg.get(repo_id, 0) + 1
    ranked = sorted(agg.items(), key=lambda kv: -kv[1])[:limit]
    return [{"repo_key": git_ident.describe(repo_id)[1], "sessions": count} for repo_id, count in ranked]


def _z3_usage_patterns(rows: list, limit: int = 10) -> dict:
    """DASH §3.3. Mode: jsonl,both -- `unavailable` as a whole in an otel_only-only range,
    same rule as Z1's structural fields (there is no `tools`/`skills`/`info.repo` at all
    without a fold)."""
    if not _has_jsonl_capable_session(rows):
        reason = unavailable("z3_usage_patterns", "jsonl")
        return {"top_tools": reason, "top_skills": reason,
               "top_mcp_servers": reason, "top_repos": reason}
    return {
        "top_tools": _top_tools(rows, limit),
        "top_skills": _top_skills(rows, limit),
        "top_mcp_servers": _top_mcp_servers(rows, limit),
        "top_repos": _top_repos(rows, limit),
    }


def _peak_hour(rows: list, tz: ZoneInfo) -> dict:
    """S1 rhythm zone, R3. Range-scoped (not lifetime, FEAS §6) and zero new queries -- both
    rungs read fields `_fetch_sessions_in_range` already fetched. **Not** an `unavailable`
    capability -- `resolve_hour_histogram` always returns a real number with a named rung, so
    this is a precedence fallback, not a structural absence (MODES §5 does not apply, ground
    rule 2). Renders the full 24-bucket histogram alongside the peak, since the tile draws a
    micro-histogram and cannot re-derive one from a single hour.

    Buckets are hours in `tz` (the caller's already-resolved `users.timezone`, UTC when unset)
    -- see `precedence.PEAK_HOUR_CHAIN`'s own resolvers for why that used to be hardcoded UTC.
    """
    histogram, sources = precedence.resolve_hour_histogram(rows, tz)
    if not any(histogram):
        return {"peak_hour": sourced(None, None), "peak_hour_histogram": histogram}
    # Ties pick the lowest hour: `max` keeps the first maximum it sees over an ascending range,
    # never overwriting on a later equal count. Documented here because it's not obvious from
    # the call site alone.
    peak = max(range(24), key=lambda h: histogram[h])
    return {"peak_hour": sourced(peak, ",".join(sources) or None),
           "peak_hour_histogram": histogram}


def _favorite_model(model_split: dict[str, dict]) -> dict | None:
    """S1 rhythm zone, R3. Top model by tokens from the Z2 split the caller already computed
    (`manager_aggregate.model_split`, called once and shared -- recomputing here is the kind of
    duplication that drifts). Zero-token entries (Claude Code's internal `<synthetic>` marker
    requests) are
    excluded before ranking, the same filter Z2's bar already applies client-side
    (`overview.mjs`'s `renderZ2`) -- now enforced once, server-side, instead of twice."""
    non_zero = {model: stats for model, stats in model_split.items() if stats["tokens"] > 0}
    ranked = _ranked(non_zero, "tokens", "model", 1)
    return ranked[0] if ranked else None


def _my_seat(s: DbSession, catalog, config: ApiConfig, user_id: str, spend_usd_micros: int,
             frm: datetime, to: datetime) -> dict | None:
    """U5-myusage: the viewer's own seat (cost-based seats plan §2) -- their primary team's monthly
    budget / members, scaled to the viewed window and prorated from their seat start, with their
    window spend against it. Replaces the old proxy that showed the WHOLE team's monthly budget
    as "your limit" against one person's window spend. `None` with no team, or a team with no
    budget (plan D4) -- never a fabricated limit. Unauthenticated (ground rules §2): a plain
    read, no `gate` involved."""
    team_ids = directory.team_of(s, catalog, user_id)
    if not team_ids:
        return None
    team_id = team_ids[0]
    active = budget_cycles.active(s, catalog, config.directory.default_org_id, config.budget_period)
    budget = budgets_service.get_budget(s, catalog, "team", team_id, active.label)
    budget_micros = manager_aggregate.budget_usd_micros(budget["amount_usd"])
    members = len(directory.members_of(s, catalog, team_id, include_subteams=False))
    if not budget_micros or not members:
        return None
    seat_monthly = budget_micros // members
    seat_start = manager_aggregate.fetch_seat_starts(s, catalog, [user_id]).get(user_id)
    allowance = manager_aggregate.seat_allowance_usd_micros(seat_monthly, seat_start, frm, to,
                                                            active)
    pct = manager_aggregate.usage_pct(spend_usd_micros, allowance)
    grace = allowance is not None and manager_aggregate.in_grace(
        seat_start, to, config.utilization.new_seat_grace_days)
    status = manager_aggregate.seat_usage_status(pct, seat_start, to, config.utilization)

    def budget_money(micros: int | None) -> dict:
        # A budget-derived figure, not measured spend: basis "budget", completeness n/a.
        if micros is None:
            return {"usd_micros": None, "cost_basis": None, "cost_complete": None}
        return {"usd_micros": micros, "cost_basis": "budget", "cost_complete": None}

    return {
        "team_id": team_id,
        "seat_monthly": budget_money(seat_monthly),
        "allowance": budget_money(allowance),
        "usage_pct": pct,
        "status": status,
        "in_grace": grace,
    }


def overview(s: DbSession, catalog, config: ApiConfig, user_id: str,
            frm: datetime, to: datetime) -> dict:
    """`GET /v1/users/{id}/overview?from&to` (dashboard-api-implementation plan A3)."""
    tz = _timezone_for(s, catalog, user_id)
    rows = _fetch_sessions_in_range(s, catalog, user_id, frm, to)
    pr_rows = manager_aggregate.fetch_scope_merged_prs(s, catalog, [user_id], frm, to)
    modes_present = sorted({r.datasource for r in rows if r.datasource})
    sessions_by_mode = dict(Counter(r.datasource for r in rows if r.datasource))
    z2 = _z2_consumption(rows)
    cost_value, cost_sources = precedence.resolve_aggregate(rows, config.dashboard.precedence.cost)
    recoverable = manager_aggregate.recoverable(rows)

    return {
        **aggregate_mode_block(modes_present, sessions_by_mode),
        "z0": {**_peak_hour(rows, tz), "favorite_model": _favorite_model(z2["model_split"])},
        "z1": _z1_headline(rows, pr_rows, config),
        "z2": z2,
        "z3": _z3_usage_patterns(rows),
        # Practice scorecard (PRACT §4.4): not computed this pass, in any mode -- cold-start,
        # not the mode-gated `unavailable` (that's reserved for "this deployment structurally
        # cannot produce it"; this is "we haven't built it yet").
        "z5_scorecard": None,
        # U5-myusage: recoverable spend (X3) and "my" seat (cost-based seats plan §3.5) -- the
        # two MyUsage headline pieces `z1`/`z2`/`z3` don't otherwise carry.
        "recoverable": _money(recoverable["recoverable_usd_micros"], cost_sources),
        "seat": _my_seat(s, catalog, config, user_id, int(cost_value), frm, to),
    }


# ------------------------------------------------------------------ repositories (dashboard plan §3)

def _repo_rollup(rows: list) -> list[dict]:
    """Per-`repo_id` rollup for `GET /v1/users/{id}/repos`: sessions counted over roots only
    (FEAS §4/R0, the same asymmetry `_z1_headline` uses), cost/tokens/files/lines summed over
    every row -- own-session fields, never rolled up into the parent anywhere else in the
    system. `kind`/`label` are fully derivable from `repo_id` alone (`git_ident.describe`),
    the same scheme the `repositories` table itself was populated with -- no join needed."""
    agg: dict[str, dict] = {}
    for r in rows:
        repo_id = getattr(r, "repo_id", None)
        if not repo_id:
            continue
        bucket = agg.setdefault(repo_id, {
            "sessions": 0, "cost_usd_micros": 0, "tokens_total": 0,
            "files_created": 0, "files_edited": 0, "lines_added": 0, "lines_removed": 0,
        })
        if _is_root(r):
            bucket["sessions"] += 1
        bucket["cost_usd_micros"] += r.cost_effective_usd_micros or 0
        bucket["tokens_total"] += r.tokens_total or 0
        for finfo in (r.files or {}).values():
            if finfo.get("created"):
                bucket["files_created"] += 1
            else:
                bucket["files_edited"] += 1
            bucket["lines_added"] += finfo.get("lines_added") or 0
            bucket["lines_removed"] += finfo.get("lines_removed") or 0

    result = []
    for repo_id, bucket in agg.items():
        kind, label = git_ident.describe(repo_id)
        result.append({"repo_id": repo_id, "kind": kind, "label": label, **bucket})
    result.sort(key=lambda r: -r["cost_usd_micros"])
    return result


def repos(s: DbSession, catalog, user_id: str, frm: datetime, to: datetime) -> dict:
    """`GET /v1/users/{id}/repos?from&to` (dashboard plan §3): spend/sessions/files/lines per
    repo, keyed on the stable `repo_id` (`git_ident.py`'s evidence ladder), not the fragile
    display-only `repo_key`. `repo_id` is JSONL-fold-only -- `metrics.py`'s `repo_info` never
    runs for an `otel_only` stub, which the true-up worker writes directly -- so this is
    structurally `unavailable`, not an empty list, for a range with no jsonl-capable session
    at all, the same mode-gating rule Z1/Z3 already apply. A path-tier-only repo (no remote,
    no PR) is still included: check `kind`/the per-repo entry's absence of a `github:`/`git:`
    prefix on `repo_id` before treating a label as an authoritative identity, same caution the
    UI already applies to an `estimated` cost figure."""
    rows = _fetch_sessions_in_range(s, catalog, user_id, frm, to)
    if not _has_jsonl_capable_session(rows):
        return {"repos": unavailable("repos", "jsonl")}
    return {"repos": _repo_rollup(rows)}


# ------------------------------------------------------------------ R2: S1 rhythm zone

#: `weeks` bounds for `GET /rhythm` -- 1 week floor (a window with nothing to walk is still
#: valid), 2 years ceiling. Not config: this is a request-shape guard, not a deployment knob.
_MIN_RHYTHM_WEEKS = 1
_MAX_RHYTHM_WEEKS = 104


def _rhythm_window(weeks: int, now: datetime, tz: ZoneInfo) -> tuple[date, date, datetime]:
    """The window is `weeks * 7` whole local days (in `tz`) ending "today" in that zone,
    inclusive on both ends. `frm` -- the UTC instant the range query's WHERE clause filters
    on -- is local midnight of `from_day` converted to UTC, so a session just before local
    midnight on the boundary day is neither dropped nor double-counted."""
    to_day = now.astimezone(tz).date()
    from_day = to_day - timedelta(days=weeks * 7 - 1)
    frm = datetime.combine(from_day, time.min, tzinfo=tz).astimezone(timezone.utc)
    return from_day, to_day, frm


def _daily_rows(s: DbSession, catalog, user_id: str, frm: datetime, tz: ZoneInfo) -> dict[date, dict]:
    rows = s.execute(catalog.get("dashboard", "activity_by_day"),
                     {"user_id": user_id, "from": frm, "tz": tz.key}).all()
    return {r.day: {"sessions": r.sessions, "tokens": int(r.tokens)} for r in rows}


def _fill_calendar(by_day: dict[date, dict], from_day: date, to_day: date) -> list[dict]:
    """Every day in the window, including zero days (R2's own rule) -- the front-end would
    otherwise have to reconstruct the calendar itself and would get the padding wrong."""
    days = []
    d = from_day
    while d <= to_day:
        found = by_day.get(d, {"sessions": 0, "tokens": 0})
        days.append({"day": d, "sessions": found["sessions"], "tokens": found["tokens"]})
        d += timedelta(days=1)
    return days


def _streaks(days: list[dict]) -> tuple[int, dict | None]:
    """`active_days`: how many days in the window had any session, order-independent.
    `longest_streak`: the longest run of *consecutive* active days, walked once over the full
    calendar (including the zero days `_fill_calendar` padded in, which is what lets a run
    ending on the window's last day count in full rather than being cut short by a missing
    day past the edge). `None`, not `{"days": 0, ...}`, when there are no active days at all --
    the UI needs to tell "no data yet" from "a one-day streak" apart (envelope rule 3).

    `days` (from `_fill_calendar`) is already bucketed in the caller's chosen timezone
    (`activity_by_day`'s `:tz` param) -- this function itself has no UTC/local distinction to
    make, it just walks whatever calendar it's handed."""
    active_days = sum(1 for d in days if d["sessions"] > 0)

    best_len = 0
    best: dict | None = None
    run_start: date | None = None
    run_len = 0
    for d in days:
        if d["sessions"] > 0:
            if run_start is None:
                run_start = d["day"]
            run_len += 1
            if run_len > best_len:
                best_len = run_len
                best = {"days": run_len, "from": run_start, "to": d["day"]}
        else:
            run_start = None
            run_len = 0

    return active_days, best


def rhythm(s: DbSession, catalog, user_id: str, weeks: int = 52,
          now: datetime | None = None) -> dict:
    """`GET /v1/users/{id}/rhythm?weeks=` (S1 rhythm zone, R2). Its own endpoint, not a block
    on `/overview`: streaks and the heatmap are lifetime-scoped by definition (FEAS §6) -- a
    response carrying both a `from`/`to` range and a 52-week window would leave
    `aggregate_mode_block` describing which one? Deliberately no `from`/`to` params, for the
    same reason: accepting them would invite exactly the confusion this split exists to avoid.
    """
    if not (_MIN_RHYTHM_WEEKS <= weeks <= _MAX_RHYTHM_WEEKS):
        raise BadRequestError(
            f"weeks must be between {_MIN_RHYTHM_WEEKS} and {_MAX_RHYTHM_WEEKS}, got {weeks}")

    tz = _timezone_for(s, catalog, user_id)
    from_day, to_day, frm = _rhythm_window(weeks, now or datetime.now(timezone.utc), tz)

    by_day = _daily_rows(s, catalog, user_id, frm, tz)
    days = _fill_calendar(by_day, from_day, to_day)
    active_days, longest_streak = _streaks(days)

    mode_rows = s.execute(catalog.get("dashboard", "rhythm_modes_in_window"),
                          {"user_id": user_id, "from": frm}).all()
    sessions_by_mode = {r.datasource: r.sessions for r in mode_rows if r.datasource}
    modes_present = sorted(sessions_by_mode)

    return {
        **aggregate_mode_block(modes_present, sessions_by_mode),
        "active_days": active_days,
        "days_in_window": len(days),
        "longest_streak": {"days": longest_streak["days"],
                           "from": longest_streak["from"].isoformat(),
                           "to": longest_streak["to"].isoformat()} if longest_streak else None,
        "days": [{"day": d["day"].isoformat(), "tokens": d["tokens"], "sessions": d["sessions"]}
                for d in days],
    }


# --------------------------------------------------------------- A4: sessions list

#: `sort` -> the one named query that produces that order (config/queries/dashboard.yaml).
#: A sort key with no entry here is rejected in `sessions_list` before the catalog is ever
#: consulted -- an unknown sort silently falling back to a default looks like broken data
#: (A4's own rule), so it is a 400, not a KeyError surfacing as a 500.
_SORT_QUERIES = {
    "recent": "sessions_list_by_recent",
    "cost": "sessions_list_by_cost",
    "tokens": "sessions_list_by_tokens",
    "duration": "sessions_list_by_duration",
    "compactions": "sessions_list_by_compactions",
    "subagents": "sessions_list_by_subagents",
    "errors": "sessions_list_by_errors",
}


def _duration_seconds(row: Any) -> float | None:
    end = row.ended_at or row.last_event_at
    if row.started_at is None or end is None:
        return None
    return (end - row.started_at).total_seconds()


def _flag_badges(row: Any, config: ApiConfig) -> list[str]:
    """Sessions-list badges (DASH §3.4), derived at query time from scalar columns + config
    thresholds -- never stored, so a threshold change never needs a refold. The "costly" rule's
    p90-of-trailing-30d half is cross-session history this pass doesn't compute; only the
    absolute-floor half ships here, which is independently well-defined per the spec's "OR"."""
    flags_cfg = config.dashboard.flags
    badges: list[str] = []

    cost = row.cost_effective_usd_micros
    if cost is not None and cost >= flags_cfg.costly_usd_floor * 1_000_000:
        badges.append("costly")

    duration_s = _duration_seconds(row)
    if duration_s is not None and duration_s >= flags_cfg.long_running_duration_s:
        badges.append("long_running")

    if (row.compaction_count or 0) >= flags_cfg.compaction_heavy_count:
        badges.append("compaction_heavy")

    if (row.subagent_count or 0) >= flags_cfg.fan_out_subagent_count:
        badges.append("fan_out")

    if (row.error_count or 0) >= flags_cfg.error_retry_count:
        badges.append("error_retry")

    hit_ratio = ((row.info or {}).get("cache") or {}).get("hit_ratio")
    if hit_ratio is not None and hit_ratio < flags_cfg.cold_cache_hit_ratio:
        badges.append("cold_cache")

    return badges


def _session_row_view(row: Any, config: ApiConfig) -> dict:
    """One sessions-list row (A4). `otel_only` rows carry no glyph/title/repo/badges --
    distinguished by `datasource` in the mode block, never by the absence itself (a JSONL
    session that merely has no title must not look like a reduced row)."""
    jsonl_capable = _jsonl_capable(row.datasource)
    info = row.info or {}
    story = info.get("story") if jsonl_capable else None

    return {
        "session_id": row.session_id,
        **mode_block(row.datasource, row.agent),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "duration_s": _duration_seconds(row),
        "tokens_total": row.tokens_total,
        "cost_usd_micros": row.cost_effective_usd_micros,
        "jsonl_lag_s": row.jsonl_lag_s,
        "glyph": {"curve": story.get("curve"), "shape": story.get("shape")} if story else None,
        "title": info.get("title") if jsonl_capable else None,
        "repo": (info.get("repo") or {}).get("repo_key") if jsonl_capable else None,
        "badges": _flag_badges(row, config) if jsonl_capable else [],
    }


def sessions_list(s: DbSession, catalog, config: ApiConfig, user_id: str, frm: datetime,
                  to: datetime, sort: str = "recent", vendor: str | None = None,
                  repo_id: str | None = None, flags: list[str] | None = None,
                  limit: int = 20, offset: int = 0) -> dict:
    """`GET /v1/users/{id}/sessions` (dashboard-api-implementation plan A4). `repo_id` (R1.4)
    filters against the promoted `session_info.repo_id` column in SQL, not in Python after the
    fact -- unlike `flags` (computed from badges, applied post-LIMIT), `repo_id` is a stored,
    indexed column, so pushing it into the WHERE clause keeps pagination exact."""
    query_name = _SORT_QUERIES.get(sort)
    if query_name is None:
        raise BadRequestError(f"unknown sort key {sort!r}; valid: {sorted(_SORT_QUERIES)}")

    # Fetch one extra row to detect "more available" without a second COUNT query.
    rows = s.execute(catalog.get("dashboard", query_name), {
        "user_id": user_id, "from": frm, "to": to, "vendor": vendor, "repo_id": repo_id,
        "limit": limit + 1, "offset": offset,
    }).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    views = [_session_row_view(r, config) for r in rows]
    if flags:
        # Applied after the DB LIMIT, since badges are computed, not stored (a threshold
        # change must not need a refold or a different query). A flags-filtered page can
        # therefore return fewer than `limit` rows even when more exist -- acceptable for a
        # v1 that has no stored badge to filter on in SQL.
        requested = set(flags)
        views = [v for v in views if requested & set(v["badges"])]

    return {"sessions": views, "has_more": has_more, "limit": limit, "offset": offset}


# ---------------------------------------------------------------- A5: session detail

def _channel_figures(channel: dict | None) -> tuple[int, int | None]:
    channel = channel or {}
    return channel.get("tokens", 0), channel.get("usd_micros")


def _cost_by_channel_block(info: dict, jsonl_capable: bool) -> Any:
    """R7.2: `{carry, recache, output, input}`, each `sourced({tokens, usd_micros, share},
    ...)`. `carry` reads `info.carry` (R2's own carry_usd_micros/cache_read_tokens -- one
    source of truth, never recomputed here); the other three read `info.channel_cost` (the
    new R7.2 rollup covering the channels `carry_rollup` doesn't). Cold-start (`None`) when
    the fold hasn't run either rollup yet; `unavailable` when not jsonl-capable."""
    if not jsonl_capable:
        return unavailable("by_channel", "jsonl")
    if "channel_cost" not in info or "carry" not in info:
        return None
    carry = info.get("carry") or {}
    channel_cost = info.get("channel_cost") or {}
    raw = {
        "carry": ("carry_rollup", carry.get("cache_read_tokens", 0), carry.get("carry_usd_micros")),
        "recache": ("channel_cost_rollup", *_channel_figures(channel_cost.get("recache"))),
        "output": ("channel_cost_rollup", *_channel_figures(channel_cost.get("output"))),
        "input": ("channel_cost_rollup", *_channel_figures(channel_cost.get("input"))),
    }
    usd_values = [usd for _, _, usd in raw.values()]
    total_usd = sum(usd_values) if all(v is not None for v in usd_values) else None
    return {
        name: sourced(
            {"tokens": tokens, "usd_micros": usd,
             "share": (usd / total_usd) if usd is not None and total_usd else None},
            source,
        )
        for name, (source, tokens, usd) in raw.items()
    }


def _cost_block(row: Any, jsonl_capable: bool) -> dict:
    """DASH §4.2's cost fields, built by explicit allowlist rather than passing `otel` through
    -- `calibration` is internal (MODES §3.1) and this is the one place that guarantees it
    never reaches a response body: nothing here even reads the key, let alone forwards it."""
    otel = row.otel or {}
    by_query_source = otel.get("by_query_source")
    return {
        "cost_usd_micros": otel.get("cost_usd_micros"),
        "cost_est_usd_micros": otel.get("cost_est_usd_micros"),
        "cost_effective_usd_micros": row.cost_effective_usd_micros,
        "cost_basis": otel.get("cost_basis"),
        "cost_complete": otel.get("cost_complete"),
        "by_query_source": by_query_source if by_query_source is not None
                          else unavailable("by_query_source", "otel"),
        "by_channel": _cost_by_channel_block(row.info or {}, jsonl_capable),
    }


def _repos_block(info: dict, jsonl_capable: bool) -> Any:
    """R7.1: `[{repo_id, repo_key, display, refs, share, primary, cost_usd_micros}]`, ranked,
    from `info.repo.repos[]` (R1.3). `repo_id` is included alongside the plan's own
    `{repo_key, display, refs, share, primary}` list -- R8.2's repo chips link to the sessions
    list filtered by repo (R1.4), and that filter (`GET .../sessions?repo_id=`) joins on
    `repo_id`; `repo_key` is documented everywhere else as a display label only, never a join
    key (`metrics.repo_info`'s own docstring), so omitting `repo_id` here would leave R8.2 with
    nothing stable to link through. Empty list -- not `unavailable` -- when jsonl-capable and no
    repo resolved: a session with no file work legitimately has no repo. Cold-start (`None`)
    when R1's fold fields haven't landed on this session at all yet.

    `cost_usd_micros` (segment-level-pricing plan, per-session cost split): the real dollar
    split of *this* session's own spend across every repo it touched, not the `share` field's
    file-touch-count proxy -- `manager_aggregate.segment_cost_by_repo_key`, keyed on the same
    `repo_key` a segment's own per-request pricing (Phase 1) already carries. `None` when
    segments haven't priced this repo (cold-start, or an unpriced request) -- distinct in
    intent from `share`'s "no file activity at all" `None`, both render the same on the wire."""
    if not jsonl_capable:
        return unavailable("repos", "jsonl")
    if "repo" not in info:
        return None
    repos = ((info.get("repo") or {}).get("repos")) or []
    total_refs = sum(r["refs"] for r in repos)
    cost_by_repo_key = manager_aggregate.segment_cost_by_repo_key(info)
    return [
        {
            "repo_id": r["repo_id"], "repo_key": r["repo_key"], "display": r["repo_key"],
            "refs": r["refs"], "share": (r["refs"] / total_refs) if total_refs else None,
            "primary": r["primary"], "cost_usd_micros": cost_by_repo_key.get(r["repo_key"]),
        }
        for r in repos
    ]


_SEGMENT_FIELDS = ("index", "label", "confidence", "t0", "t1", "turns", "files", "repo_key",
                  "beats", "carry_usd_micros", "cost_usd_micros")


def _segments_block(info: dict, jsonl_capable: bool) -> Any:
    """R7.3: `[{index, label, confidence, t0, t1, turns, files, repo_key, beats,
    carry_usd_micros, cost_usd_micros}]`, reshaped straight from `info.segments` (R4+R5,
    already assembled per-field at fold time). Cold-start (`None`, not `[]`) when the R4/R5
    engine hasn't run on this session yet -- distinct from "it ran and found one segment"."""
    if not jsonl_capable:
        return unavailable("segments", "jsonl")
    if "segments" not in info:
        return None
    return [{k: seg.get(k) for k in _SEGMENT_FIELDS} for seg in info["segments"]]


def _finding(finding_id: str, kind: str, observation: str, action: str | None,
            usd_micros: int | None, basis: str) -> dict:
    return {"finding_id": finding_id, "kind": kind, "observation": observation,
           "action": action, "usd_micros": usd_micros, "basis": basis}


def _resume_finding(info: dict, last_event_at: datetime | None, now: datetime,
                    idle_min_s: int) -> dict | None:
    """R3.3, deferred to R7: the one live, wall-clock-dependent finding. The dollar figure is
    entirely fold-computed (`info.resume_cost.projected`, metrics.py) -- this only decides
    *whether* to surface it, from `now - last_event_at` (a time subtraction, never a dollar
    derivation, ground rule 4)."""
    projected = (info.get("resume_cost") or {}).get("projected")
    if not projected or last_event_at is None:
        return None
    context_tokens = projected.get("context_tokens") or 0
    if context_tokens <= 0:
        return None
    idle_s = (now - last_event_at).total_seconds()
    if idle_s < idle_min_s:
        return None
    idle_hours = idle_s / 3600
    observation = (f"This session has been idle {idle_hours:.0f}h and carries "
                  f"{context_tokens:,} tokens of context.")
    return _finding("resume", "cost", observation,
                    "Resuming reloads that context — starting fresh is often cheaper",
                    projected.get("usd_micros"), "recoverable")


def _read_then_discard_finding(info: dict) -> dict | None:
    """R6.1, per-session: `info.read_then_discard` stays token-estimate only (no
    `Pricer`, per its own docstring) -- `usd_micros` is always `None` here, the
    `recoverable`/unpriced case R7.4 distinguishes from `observed`."""
    total_est_tokens = (info.get("read_then_discard") or {}).get("total_est_tokens") or 0
    if total_est_tokens <= 0:
        return None
    return _finding(
        "read_then_discard", "cost",
        f"This session read about {total_est_tokens:,} tokens shortly before a compaction "
        "summarized it away.",
        "Reading right before a compaction rarely survives it — read after, or ask for a "
        "summary instead of the full file",
        None, "recoverable")


def _long_tail_session_finding(turns: list, config: ApiConfig) -> dict | None:
    """R6.2, per-session: reuses `_long_tail_finding` (the same function the range-level
    insight 30 already calls) directly against this session's own `turns` -- it was never
    surfaced per-session before this. `usd_micros` stays `None`: pricing just the trailing
    share would mean re-deriving dollars from a token ratio at the API layer (forbidden);
    insight 30's whole-session-cost proxy is a coarser aggregate claim that doesn't translate
    to a specific per-finding dollar without misrepresenting it."""
    thresh = config.insights
    f = _long_tail_finding(turns or [], thresh.long_tail_min_trailing_turns,
                           thresh.long_tail_min_trailing_token_share)
    if f is None:
        return None
    return _finding(
        "long_tail", "cost",
        f"This session kept spending tokens for {f['trailing_turns']} turns after its last "
        f"file change — {f['trailing_tokens']:,} tokens with nothing landed.",
        "When file changes stop but the session keeps going, starting fresh is often cheaper "
        "than continuing to dig",
        None, "recoverable")


#: Shared by the per-session finding and insight 31 -- one wording for one bucket.
_INTERRUPTED_ACTION = "A clearer first prompt or a quick plan can help the first answer land"


def _interrupted_finding(info: dict) -> dict | None:
    """R6.3, per-session: mirrors insight 31. Recoverable spend (efficiency note §2, bucket 3,
    decision 2026-09-16 -- reverses the earlier "observed, not recoverable" call): the discarded
    response was billed in full. `usd_micros` is the fold's own all-or-nothing
    `interrupted.total_usd_micros`, `None` when unpriced (never re-derived from tokens here)."""
    interrupted = info.get("interrupted") or {}
    count = interrupted.get("count") or 0
    if count <= 0:
        return None
    total_tokens = interrupted.get("total_tokens") or 0
    plural = count != 1
    return _finding(
        "interrupted", "cost",
        f"{count} request{'s' if plural else ''} in this session "
        f"{'were' if plural else 'was'} interrupted after paying for the response — "
        f"{total_tokens:,} tokens billed for output that was discarded.",
        _INTERRUPTED_ACTION, interrupted.get("total_usd_micros"), "recoverable")


def _session_findings(row: Any, config: ApiConfig, jsonl_capable: bool, now: datetime) -> Any:
    """R7.4: `[{finding_id, kind, observation, action, usd_micros, basis}]`, ranked by
    recoverable spend (unpriced findings last). `[]` (real, empty) when jsonl-capable and none
    of R3/R6's four sources fire -- R8.6's "nothing stood out" good-empty state. Cold-start
    (`None`) only when the session predates R2/R3's own fold fields; `unavailable` when not
    jsonl-capable at all."""
    if not jsonl_capable:
        return unavailable("findings", "jsonl")
    info = row.info or {}
    if "carry" not in info or "resume_cost" not in info:
        return None
    candidates = [
        _resume_finding(info, row.last_event_at, now, config.insights.resume_idle_min_s),
        _read_then_discard_finding(info),
        _long_tail_session_finding(row.turns or [], config),
        _interrupted_finding(info),
    ]
    findings = [f for f in candidates if f is not None]
    findings.sort(key=lambda f: (f["usd_micros"] is None, -(f["usd_micros"] or 0)))
    return findings


def _metrics_panel(row: Any, jsonl_capable: bool) -> dict:
    """DASH §4.2's metrics panel, from the already-shipped P1 rollups. `unavailable` as a
    whole for an `otel_only` session -- there is no fold output to read at all, not an empty
    one (same structural-absence rule as A3/A4)."""
    if not jsonl_capable:
        reason = unavailable("metrics", "jsonl")
        return {"models": reason, "tools": reason, "skills": reason, "subagents": reason,
               "files": reason, "turn_count": reason, "long_turn_count": reason,
               "compactions": reason, "cache": reason, "errors": reason}
    info = row.info or {}
    return {
        "models": row.models,
        "tools": row.tools,
        "skills": row.skills,
        "subagents": row.subagents,
        "files": row.files,
        "turn_count": row.turn_count,
        "long_turn_count": row.long_turn_count,
        "compactions": info.get("compactions", []),
        "cache": info.get("cache"),
        "errors": info.get("errors"),
    }


def _segment_view_with_fit(seg: dict) -> dict:
    """The same `_SEGMENT_FIELDS` allowlist `_segments_block` reshapes to, plus X4's `fit` --
    `seg` here is one of `aggregate.model_fit`'s own output segments (the raw fold dict with
    `fit` merged in), not `info["segments"]` directly."""
    return {**{k: seg.get(k) for k in _SEGMENT_FIELDS}, "fit": seg.get("fit")}


def _session_lifecycle(s: DbSession, catalog, session_id: str, session_started_at) -> dict | None:
    """D1-lifecycle: build/review/ship milestone timestamps for a session, joined from the PR(s)
    the correlator tied this session's commits to (`session_git_links`) plus those PRs' own
    open/first-review/merge timestamps. Raw timestamps only (schemas/README strip-rule -- the
    client lays out the spans from these, same posture as `FeatureLifecycle`).

    `None` (cold-start) when the session is linked to no PR yet -- there is no delivery timeline
    to draw, honestly absent rather than a fabricated span off the session's own start alone.
    `reopened_at` is the (possibly empty) list of reopen-event timestamps from the PR
    state-transition log (`git_artifact_events`) -- each anchors a rework span; empty when the
    PR never reopened or predates that log."""
    pr_ids = [r.artifact_id for r in
              s.execute(catalog.get("pr_class", "links_for_session"), {"session_id": session_id})]
    if not pr_ids:
        return None
    prs = list(s.execute(catalog.get("manager", "pr_basic_facts"), {"pr_ids": pr_ids}))
    if not prs:
        return None
    reviews = list(s.execute(catalog.get("manager", "pr_review_children"), {"pr_ids": pr_ids}))
    reopens = list(s.execute(catalog.get("manager", "pr_reopen_events"), {"pr_ids": pr_ids}))
    opened = [p.created_at for p in prs if p.created_at is not None]
    merges = [p.merged_at for p in prs if p.merged_at is not None]
    review_times = [r.created_at for r in reviews if r.created_at is not None]
    return {
        "session_started_at": session_started_at.isoformat() if session_started_at else None,
        "first_pr_opened_at": min(opened).isoformat() if opened else None,
        "first_review_at": min(review_times).isoformat() if review_times else None,
        "reopened_at": [r.occurred_at.isoformat() for r in reopens if r.occurred_at is not None],
        "last_merged_at": max(merges).isoformat() if merges else None,
    }


def _session_economics_block(s: DbSession, catalog, session_id: str, row: Any, info: dict,
                             jsonl_capable: bool, config: ApiConfig, pricing) -> Any:
    """D1: the manager-economics view of a session (MVP plan Track D) -- `?view=economics`
    on the same `GET /v1/sessions/{id}` route, not a new route, since it reads the same
    `session_info` row R7's other blocks already do.

    `model_fit`/segment `fit` (X4), `channels` (R7.2's own `_cost_by_channel_block`, reused
    rather than recomputed), `context_curve` (D1 -- the per-turn context-window-size series
    `graph_fold.context_curve_of` folds into `info`), and `lifecycle` (D1 -- build/review/ship
    milestones joined from the session's linked PR(s) via `_session_lifecycle`) are all real
    today. `lifecycle` is `None` when the correlator hasn't linked this session to a PR;
    `context_curve` is `None` for a session folded before D1-context-curve shipped (the key is
    simply absent from `info`) -- both honestly cold-start, never a fabricated empty value.
    """
    if not jsonl_capable:
        reason = unavailable("economics", "jsonl")
        return {"model_fit": reason, "segments": reason, "channels": reason,
               "practices": reason, "lifecycle": reason, "context_curve": reason,
               "models": reason}
    fit_result = manager_aggregate.model_fit(row, config, pricing)
    return {
        "model_fit": ({"verdict": fit_result["verdict"], "note": fit_result["note"],
                       "savings_usd_micros": fit_result["savings_usd_micros"]}
                      if fit_result else None),
        "segments": ([_segment_view_with_fit(seg) for seg in fit_result["segments"]]
                     if fit_result else _segments_block(info, jsonl_capable)),
        "channels": _cost_by_channel_block(info, jsonl_capable),
        "practices": info.get("practices"),
        "lifecycle": _session_lifecycle(s, catalog, session_id, row.started_at),  # D1-lifecycle
        "context_curve": info.get("context_curve"),  # D1-context-curve (fold-derived)
        # QW1 (mvp-gap-closure-plan): per-model token share, one entry per model, category
        # `tier` not colour (schemas/README strip-rule 1). `model_split` can in fact answer
        # this from the OTel ledger alone on an `otel_only` session too -- gated on
        # `jsonl_capable` here anyway to match every sibling field in this block; loosen this
        # gate specifically if an `otel_only` "Models used" turns out to matter.
        "models": manager_aggregate.model_usage_breakdown(manager_aggregate.model_split([row])),
    }


def session_detail(s: DbSession, catalog, config: ApiConfig, session_id: str,
                   pricing=None, now: datetime | None = None, view: str | None = None) -> dict:
    """`GET /v1/sessions/{id}` (dashboard-api-implementation plan A5; R7 extends it with
    `repos`, `cost.by_channel`, `segments`, `findings`). `now` is injectable, same pattern as
    `rhythm`'s own `now` kwarg, for `findings`' one wall-clock-dependent figure (R3.3).

    `view="economics"` (D1) adds the manager-economics block on top of the same payload --
    the base shape is unchanged for every other caller. `pricing` (the same `Pricer`
    `app.py` loads once and threads into every route that needs one) only feeds that block's
    `model_fit` savings estimate (X4 §3.4); every other field needs no pricing table."""
    row = s.execute(catalog.get("dashboard", "session_detail"),
                    {"session_id": session_id}).first()
    if row is None:
        raise NotFoundError(f"no session {session_id!r}")

    jsonl_capable = _jsonl_capable(row.datasource)
    info = row.info or {}
    now = now or datetime.now(timezone.utc)

    result = {
        "session_id": row.session_id,
        **mode_block(row.datasource, row.agent),
        "agent_version": row.agent_version,
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "last_event_at": row.last_event_at.isoformat() if row.last_event_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "title": info.get("title") if jsonl_capable else None,
        "repo": (info.get("repo") or {}).get("repo_key") if jsonl_capable else None,
        "tokens_total": row.tokens_total,
        "jsonl_lag_s": row.jsonl_lag_s,
        "metrics": _metrics_panel(row, jsonl_capable),
        "cost": _cost_block(row, jsonl_capable),
        "repos": _repos_block(info, jsonl_capable),
        "segments": _segments_block(info, jsonl_capable),
        "findings": _session_findings(row, config, jsonl_capable, now),
        # Token provenance decomposition (DASH §5): not computed by any phase yet -- cold-start
        # in every mode, same as A3's Z2 provenance mix and Z5 scorecard.
        "provenance": None,
    }
    if view == "economics":
        result["economics"] = _session_economics_block(s, catalog, session_id, row, info,
                                                        jsonl_capable, config, pricing)
    elif view is not None:
        raise BadRequestError(f"unknown view {view!r}; expected 'economics'")
    return result


# ------------------------------------------------------------------- A7: insights

#: Card shape reconciled this session (see the plan's Context section): DASH §6's literal JSON
#: is written for the *persisted* record (`first_seen`/`status`, P5's dismiss/track store, not
#: built yet); this pass computes on read and drops those two. `mode` and `observation` are
#: kept from the source plan's own A7 task -- `mode` is cheap catalog metadata, `observation`
#: is the server-generated sentence BLUE §5's card anatomy calls for, template-built and
#: lexicon-checked the same way `action_text` already has to be.
def _card(insight_id: int, kind: str, mode: str, observation: str, evidence: dict,
         action_text: str | None, est_tokens: int | None, est_usd: float,
         sessions: list[str]) -> dict:
    return {
        "insight_id": insight_id, "kind": kind, "mode": mode, "observation": observation,
        "evidence": evidence, "action_text": action_text, "est_tokens": est_tokens,
        "est_usd": round(est_usd, 2), "sessions": sessions,
    }


def _insight_14_unverified_changes(rows: list, config: ApiConfig) -> dict | None:
    """PRACT §3 #14: file changes with no verifier run in the same session. `verified` is
    this session's own new field (`practices.py`); no need to re-derive it from
    `verifier_runs` here."""
    candidates = [r for r in rows if _jsonl_capable(r.datasource) and r.files
                 and not ((r.info or {}).get("practices") or {}).get("verified", False)]
    if not candidates:
        return None

    sample_size = sum(1 for r in rows if _jsonl_capable(r.datasource))
    lines_changed = sum(
        sum((info.get("lines_added") or 0) + (info.get("lines_removed") or 0)
           for info in (r.files or {}).values())
        for r in candidates
    )
    est_usd = sum((r.cost_effective_usd_micros or 0) for r in candidates) / 1_000_000

    if sample_size < config.insights.min_sessions:
        observation = (f"Anthropic recommends verifying changes with a runnable check; "
                       f"we detected its absence in {len(candidates)} sessions.")
        evidence = {"unverified_sessions": len(candidates)}
    else:
        observation = (f"{len(candidates)} of your sessions changed files without running "
                       "a verifier.")
        evidence = {"unverified_sessions": len(candidates), "lines_changed": lines_changed}

    return _card(14, "practice", "jsonl", observation, evidence,
                "Add a runnable check — ask for tests/build in the same prompt",
                None, est_usd, [r.session_id for r in candidates])


def _insight_18_vague_prompt_exploration_tax(rows: list, config: ApiConfig) -> dict | None:
    """PRACT §3 #18: a short, unscoped first prompt followed by heavy read-tool activity
    before any edit. "Turn-1 exploration cost" is approximated here as the session's total
    read-tool bytes (`tools[*].bytes_read_total`) -- a session-wide proxy, not strictly
    turn-1-scoped; the practices vector does not carry a turn-1-only breakdown."""
    threshold_chars = 40
    read_bytes_threshold = 50_000

    def _read_bytes(r) -> int:
        return sum(info.get("bytes_read_total") or 0 for info in (r.tools or {}).values())

    candidates = []
    for r in rows:
        if not _jsonl_capable(r.datasource):
            continue
        practices = (r.info or {}).get("practices") or {}
        chars = practices.get("first_prompt_chars")
        at_refs = practices.get("at_refs") or 0
        if chars is None or chars >= threshold_chars or at_refs > 0:
            continue
        if _read_bytes(r) < read_bytes_threshold:
            continue
        candidates.append(r)
    if not candidates:
        return None

    sample_size = sum(1 for r in rows if _jsonl_capable(r.datasource))
    est_usd = sum((r.cost_effective_usd_micros or 0) for r in candidates) / 1_000_000
    est_tokens = sum(_read_bytes(r) // 4 for r in candidates)   # the same ÷4 estimator DASH §5.3 uses

    if sample_size < config.insights.min_sessions:
        observation = (f"Anthropic recommends scoping prompts with file names and "
                       f"constraints; we detected {len(candidates)} vague-start sessions.")
    else:
        observation = (f"{len(candidates)} sessions started with a short, unscoped prompt "
                       "and read heavily before the first edit.")

    return _card(18, "practice", "jsonl", observation,
                {"vague_start_sessions": len(candidates)},
                "Name the files and constraints up front; or delegate the investigation to a subagent",
                est_tokens, est_usd, [r.session_id for r in candidates])


def _insight_22_passive_context_management(rows: list) -> dict | None:
    """PRACT §3 #22: every compaction across the range was auto-triggered, none manual --
    a single range-level card, not one per session (the lapse rule is a property of the whole
    history, not any one session). No min_sessions gate: PRACT gates on *session count*, and
    this insight is gated on *compaction count* being nonzero instead -- there is nothing to
    correlate against, only a fact to report."""
    manual = auto = 0
    contributing: list[str] = []
    for r in rows:
        if not _jsonl_capable(r.datasource):
            continue
        practices = (r.info or {}).get("practices") or {}
        m, a = practices.get("manual_compactions") or 0, practices.get("auto_compactions") or 0
        if m or a:
            contributing.append(r.session_id)
        manual += m
        auto += a
    if auto == 0 or manual > 0:
        return None

    est_usd = sum((r.cost_effective_usd_micros or 0) for r in rows
                 if r.session_id in contributing) / 1_000_000
    return _card(22, "practice", "jsonl",
                f"All {auto} compactions across your sessions were auto-triggered; none were manual.",
                {"auto_compactions": auto, "manual_compactions": manual},
                "Compact deliberately at phase boundaries with instructions — "
                "auto-compaction picks what survives, you should",
                None, est_usd, contributing)


def _insight_26_background_call_spend(rows: list) -> dict | None:
    """DASH §6 #26: K's share of session cost, exact from the ledger. No action_text unless a
    settings pointer exists (doc's own qualifier) -- none does, so it stays `None`."""
    by_source = _by_query_source_aggregate(rows)
    if not by_source:
        return None
    background = {k: v for k, v in by_source.items() if k != _QUERY_SOURCE_INTERACTIVE}
    # Vendor, else estimate, per request -- summing `cost_usd_micros` and `cost_est_usd_micros`
    # here double-counts every vendor-priced request (see `_by_query_source_aggregate`).
    total_micros = sum(v.get("cost_effective_usd_micros", 0) for v in background.values())
    if total_micros <= 0:
        return None

    sessions = [r.session_id for r in rows if (r.otel or {}).get("by_query_source")]
    return _card(26, "cost", "otel",
                f"Background calls (title generation, prompt suggestions, and similar) "
                f"account for ${total_micros / 1_000_000:.2f} of your spend in this range.",
                {"by_query_source": background}, None, None, total_micros / 1_000_000, sessions)


def _insight_27_model_mix_by_request_kind(rows: list) -> dict | None:
    """DASH §6 #27: interactive and background calls sharing the same model. Simplified for
    this pass: the $ estimate is the background spend on the overlapping model(s), a proxy
    for DASH's "price delta × background tokens" -- computing an actual cheaper-model delta
    needs a price-table comparison this pass doesn't build; flagged here rather than silently
    approximated as something it is not."""
    overlap_micros = 0
    overlap_models: set[str] = set()
    sessions: list[str] = []
    for r in rows:
        requests = (r.otel or {}).get("requests")
        if not requests:
            continue
        interactive_models = {req.get("model") for req in requests
                              if req.get("query_source") is None}
        background_models = {req.get("model") for req in requests
                             if req.get("query_source") is not None}
        shared = interactive_models & background_models
        shared.discard(None)
        if not shared:
            continue
        overlap_models |= shared
        for req in requests:
            if req.get("query_source") is not None and req.get("model") in shared:
                cost = req.get("cost_usd_micros")
                overlap_micros += cost if cost is not None else (req.get("cost_est_usd_micros") or 0)
        sessions.append(r.session_id)
    if not sessions:
        return None

    model_list = ", ".join(sorted(overlap_models))
    return _card(27, "cost", "otel",
                f"Your interactive turns and your background calls both ran on {model_list}.",
                {"shared_models": sorted(overlap_models)},
                "Background calls could run on a cheaper model without affecting interactive quality",
                None, overlap_micros / 1_000_000, sessions)


def _insight_28_context_weight(rows: list) -> dict | None:
    """R2.4: the carry engine's own insight -- "what sat in context, for how many requests,
    and what did that cost" (the reframe R2 exists for), not a per-source breakdown (R2.3
    stays parked provenance). One range-level card, not per-session: carry is a property of
    how sessions get run in general, not any single session's fault -- same posture as
    insight 22's range-level framing.

    `mean_context` is weighted by each session's own request count so a handful of long
    sessions don't get diluted by many short ones. Suppressed entirely (not a $0.00 card)
    when nothing in range has a priced carry figure -- an unpriced total misread as "this
    cost you nothing" is worse than no card, the same reasoning insight 26 already applies."""
    carry_rows = [
        (r, carry) for r in rows
        if _jsonl_capable(r.datasource) and (carry := (r.info or {}).get("carry")) and carry.get("requests")
    ]
    if not carry_rows:
        return None

    total_requests = sum(c["requests"] for _, c in carry_rows)
    mean_context = round(sum(c["mean_context"] * c["requests"] for _, c in carry_rows) / total_requests)
    cache_read_tokens = sum(c["cache_read_tokens"] for _, c in carry_rows)

    priced = [c["carry_usd_micros"] for _, c in carry_rows if c.get("carry_usd_micros") is not None]
    if not priced:
        return None
    est_usd = sum(priced) / 1_000_000

    return _card(28, "cost", "jsonl",
                f"Your context averaged {mean_context:,} tokens over {total_requests} requests; "
                f"that carry cost ${est_usd:.2f}.",
                {"mean_context": mean_context, "requests": total_requests,
                 "cache_read_tokens": cache_read_tokens},
                "Keep the context smaller, or keep sessions shorter — carry is what makes both a dollar figure",
                None, est_usd, [r.session_id for r, _ in carry_rows])


def _insight_4_resume_after_break(rows: list) -> dict | None:
    """R3.4: `info.cache.decay_events` (a raw signal, never surfaced as a card before this)
    promoted to a priced finding -- subsumes catalog insight #4 (cache decay). One range-level
    card, matching insight 22/28's aggregation posture: post-break reloads are a property of
    how sessions get resumed in general, not any one session's fault.

    Reuses `info.resume_cost` (R3.1/R3.2's engine, `metrics.resume_reload_rollup`) rather than
    re-deriving anything from `info.cache.decay_events` directly -- the stricter, priced
    trigger there (a gap past TTL *and* a reload covering >=50% of what was live) is what
    makes this a finding instead of noise; a bare decay event is a much weaker signal alone.

    The per-session, in-the-moment framing R3.3 describes ("this session has been idle 15h,
    resuming costs ~$4") is deliberately not built here -- it needs the *current* wall clock
    against a *specific* session's live state, which this range-aggregate endpoint has no
    business computing. That belongs on the session page as one of R7's `findings`, once R4-R6
    give it siblings to assemble against.

    Same suppression rule as insight 28: no card, not a $0.00 one, when nothing in range has a
    priced reload."""
    reload_rows = [
        (r, rc) for r in rows
        if _jsonl_capable(r.datasource) and (rc := (r.info or {}).get("resume_cost")) and rc.get("count")
    ]
    if not reload_rows:
        return None

    total_events = sum(rc["count"] for _, rc in reload_rows)
    priced = [rc["total_usd_micros"] for _, rc in reload_rows if rc.get("total_usd_micros") is not None]
    if not priced:
        return None
    est_usd = sum(priced) / 1_000_000

    return _card(4, "cost", "jsonl",
                f"{total_events} post-break context reloads across {len(reload_rows)} sessions "
                f"re-paid ${est_usd:.2f} to reload expired context.",
                {"reload_events": total_events, "sessions": len(reload_rows)},
                "When a session has sat idle a long time, starting fresh is often cheaper than "
                "resuming — the context reloads either way",
                None, est_usd, [r.session_id for r, _ in reload_rows])


def _insight_5_retry_burn(rows: list) -> dict | None:
    """R9 (housekeeping, unblocked): catalog insight #5, DASH §6 -- `info.errors`: retry_count
    x turn tokens on 429/529. The signal already exists and was unused --
    `metrics.error_rollup`'s `retried_tokens_est` (`metrics.py:227-253`) approximates the
    context size of a retried request from the tokens of the assistant_message immediately
    before the `api_error`, since an error carries no token fields of its own. Whole-session
    cost attribution for qualifying sessions, the same blunt-but-consistent convention insight
    18/29 already use for their own token-estimate signals -- not a share proportional to the
    retried tokens specifically."""
    candidates = [
        r for r in rows
        if _jsonl_capable(r.datasource) and ((r.info or {}).get("errors") or {}).get("retried_tokens_est")
    ]
    if not candidates:
        return None

    est_tokens = sum((r.info or {}).get("errors", {}).get("retried_tokens_est") or 0 for r in candidates)
    est_usd = sum((r.cost_effective_usd_micros or 0) for r in candidates) / 1_000_000

    return _card(5, "cost", "jsonl",
                f"{len(candidates)} sessions hit rate limits or overload errors and retried — "
                f"about {est_tokens:,} tokens re-sent.",
                {"sessions": len(candidates), "est_tokens": est_tokens},
                "Rate limits are recoverable — stagger parallel subagents or check your org's "
                "rate limits",
                est_tokens, est_usd, [r.session_id for r in candidates])


def _insight_11_model_by_type(rows: list) -> dict | None:
    """R5.5: model x type reporting -- unlocks catalog insight #11. "Model overkill is
    intractable in the abstract and straightforward per type" once every session has one
    (R5's own framing): spend by (type, model) is a plain GROUP BY over already-folded fields
    once `manager_aggregate.dominant_segment_label` gives each session a type.

    One card, for the type with the largest total spend, naming its most-used model and that
    model's token share -- not an "overkill" verdict (this pass has no price-tier ranking to
    judge which model a type "needs"), just the concentration fact and a neutral suggestion
    to compare, the same tone insight 27 already uses for its own model-choice observation."""
    by_type: dict[str, dict[str, int]] = {}       # type -> model -> tokens
    cost_by_type: dict[str, int] = {}
    sessions_by_type: dict[str, set[str]] = {}
    for r in rows:
        if not _jsonl_capable(r.datasource):
            continue
        label = manager_aggregate.dominant_segment_label((r.info or {}).get("segments") or [])
        if not label:
            continue
        for model, usage in (r.models or {}).items():
            by_type.setdefault(label, {}).setdefault(model, 0)
            by_type[label][model] += sum(usage.values())
        cost_by_type[label] = cost_by_type.get(label, 0) + (r.cost_effective_usd_micros or 0)
        sessions_by_type.setdefault(label, set()).add(r.session_id)

    if not by_type:
        return None
    top_type = max(cost_by_type, key=cost_by_type.get)
    models = by_type[top_type]
    total_tokens = sum(models.values())
    if total_tokens == 0:
        return None
    dominant_model = max(models, key=models.get)
    share = models[dominant_model] / total_tokens
    est_usd = cost_by_type[top_type] / 1_000_000

    return _card(11, "cost", "jsonl",
                f"{len(sessions_by_type[top_type])} {top_type} sessions ran {share:.0%} of their "
                f"tokens on {dominant_model}, totaling ${est_usd:.2f}.",
                {"type": top_type, "dominant_model": dominant_model, "share": round(share, 4),
                 "by_model": models, "sessions": len(sessions_by_type[top_type])},
                f"Compare {dominant_model} against a lighter model for {top_type}-shaped work, "
                "if the same quality holds",
                None, est_usd, sorted(sessions_by_type[top_type]))


def _insight_29_read_then_discard(rows: list) -> dict | None:
    """R6.1: `info.read_then_discard` -- large reads shortly before a compaction, paid to
    load and then dropped when the compaction summarizes that context away. Token estimate
    only (the rollup itself takes no `PricingConfig`, R6's own lighter-weight posture); the
    dollar figure attributes a qualifying session's *whole* cost, the same blunt-but-consistent
    attribution insight 18 already uses for its own read-bytes-based estimate, not a share
    proportional to the discarded bytes specifically."""
    candidates = [
        r for r in rows
        if _jsonl_capable(r.datasource) and ((r.info or {}).get("read_then_discard") or {}).get("total_bytes_read")
    ]
    if not candidates:
        return None

    est_tokens = sum((r.info or {}).get("read_then_discard", {}).get("total_est_tokens") or 0 for r in candidates)
    est_usd = sum((r.cost_effective_usd_micros or 0) for r in candidates) / 1_000_000

    return _card(29, "cost", "jsonl",
                f"{len(candidates)} sessions read large amounts of content shortly before a "
                f"compaction summarized it away — about {est_tokens:,} tokens.",
                {"sessions": len(candidates), "est_tokens": est_tokens},
                "Reading right before a compaction rarely survives it — read after, or ask for "
                "a summary instead of the full file",
                est_tokens, est_usd, [r.session_id for r in candidates])


def _long_tail_finding(turns: list, min_trailing_turns: int, min_trailing_token_share: float) -> dict | None:
    """R6.2: the point where token spend keeps climbing while `files_changed` flattens --
    sharper than compaction churn (the old catalog's #3, an existing-but-weaker proxy for the
    same phenomenon). `turns` is `session_info.turns` (F3-F5's per-turn records, already a
    stored column -- this needs no fold pass of its own). The last turn that changed any file
    marks "productive work stopped here"; everything after it is the candidate tail."""
    if not turns:
        return None
    total_tokens = sum(t.get("tokens") or 0 for t in turns)
    if total_tokens == 0:
        return None
    last_productive = max((t["index"] for t in turns if (t.get("files_changed") or 0) > 0), default=0)
    trailing = [t for t in turns if t["index"] > last_productive]
    if len(trailing) < min_trailing_turns:
        return None
    trailing_tokens = sum(t.get("tokens") or 0 for t in trailing)
    share = trailing_tokens / total_tokens
    if share < min_trailing_token_share:
        return None
    return {"trailing_turns": len(trailing), "trailing_tokens": trailing_tokens, "share": share}


def _insight_30_long_tail_sessions(rows: list, config: ApiConfig) -> dict | None:
    """R6.2: pairs naturally with R3 (resume-after-break) -- the action for both is *start
    fresh*. One range-level card; the per-session finding this generalizes belongs on the
    session page (R7's `findings`), same posture as R3.4's own insight/finding split."""
    thresh = config.insights
    findings = [
        (r, f) for r in rows
        if _jsonl_capable(r.datasource)
        and (f := _long_tail_finding(r.turns or [], thresh.long_tail_min_trailing_turns,
                                     thresh.long_tail_min_trailing_token_share))
    ]
    if not findings:
        return None

    total_trailing_tokens = sum(f["trailing_tokens"] for _, f in findings)
    est_usd = sum((r.cost_effective_usd_micros or 0) for r, _ in findings) / 1_000_000

    return _card(30, "cost", "jsonl",
                f"{len(findings)} sessions kept spending tokens well after their last file "
                f"change — {total_trailing_tokens:,} tokens with nothing landed.",
                {"sessions": len(findings), "trailing_tokens": total_trailing_tokens},
                "When file changes stop but the session keeps going, starting fresh is often "
                "cheaper than continuing to dig",
                None, est_usd, [r.session_id for r, _ in findings])


def _insight_31_interrupted_requests(rows: list) -> dict | None:
    """R6.3: `info.interrupted` -- input paid, output discarded, when a user interrupts a
    turn. Recoverable spend (efficiency note §2, bucket 3, decision 2026-09-16 -- reverses the
    earlier "observed, not recoverable" call): `est_usd` sums each session's fold-priced
    `interrupted.total_usd_micros`, skipping a session whose total is unpriced (`None`)."""
    candidates = [
        r for r in rows
        if _jsonl_capable(r.datasource) and ((r.info or {}).get("interrupted") or {}).get("count")
    ]
    if not candidates:
        return None

    total_events = sum((r.info or {}).get("interrupted", {}).get("count") or 0 for r in candidates)
    total_tokens = sum((r.info or {}).get("interrupted", {}).get("total_tokens") or 0 for r in candidates)
    total_usd_micros = sum((r.info or {}).get("interrupted", {}).get("total_usd_micros") or 0 for r in candidates)

    return _card(31, "cost", "jsonl",
                f"{total_events} requests across {len(candidates)} sessions were interrupted "
                f"mid-turn — {total_tokens:,} tokens generated before the interruption.",
                {"interruptions": total_events, "sessions": len(candidates), "tokens": total_tokens},
                _INTERRUPTED_ACTION, total_tokens, total_usd_micros / 1_000_000,
                [r.session_id for r in candidates])


def insights(s: DbSession, catalog, config: ApiConfig, user_id: str,
            frm: datetime, to: datetime) -> dict:
    """`GET /v1/users/{id}/insights?from&to` (dashboard-api-implementation plan A7, reduced
    catalog: ids 4, 5, 11, 14, 18, 22, 26, 27 -- the rest need provenance or revision-loop
    detection, both parked this session; plus 28-31, R2.4/R6's new cards, outside the old
    catalog's numbering entirely). #5 (R9) is the catalog's own retry-burn card, unblocked
    housekeeping -- #3 (compaction churn) deliberately stays unbuilt, its slot retired in
    favor of insight 30 (R6.2's own docstring). Filtered by mode and by this pass's reduced
    catalog alike: an insight this pass cannot compute is omitted, never returned as "0 found"
    (DASH §6's own rule, generalized)."""
    rows = _fetch_sessions_in_range(s, catalog, user_id, frm, to)
    modes_present = sorted({r.datasource for r in rows if r.datasource})
    sessions_by_mode = dict(Counter(r.datasource for r in rows if r.datasource))

    cards = [
        _insight_4_resume_after_break(rows),
        _insight_5_retry_burn(rows),
        _insight_11_model_by_type(rows),
        _insight_14_unverified_changes(rows, config),
        _insight_18_vague_prompt_exploration_tax(rows, config),
        _insight_22_passive_context_management(rows),
        _insight_26_background_call_spend(rows),
        _insight_27_model_mix_by_request_kind(rows),
        _insight_28_context_weight(rows),
        _insight_29_read_then_discard(rows),
        _insight_30_long_tail_sessions(rows, config),
        _insight_31_interrupted_requests(rows),
    ]
    cards = [c for c in cards if c is not None]
    cards.sort(key=lambda c: -(c["est_usd"] or 0))

    return {
        **aggregate_mode_block(modes_present, sessions_by_mode),
        "insights": cards,
    }


# ------------------------------------------------------------ A8: pricing (display only)

def _per_million_str(rate: Decimal | None) -> str | None:
    """USD per token -> USD per million tokens, exact, at least two decimal places."""
    if rate is None:
        return None
    per_m = rate * 1_000_000
    exp = per_m.normalize().as_tuple().exponent
    return format(per_m.quantize(Decimal(1).scaleb(-max(2, -exp))), "f")


def pricing_models(pricing, at: date | None = None) -> dict:
    """`GET /v1/pricing/models` (A8). The rates in effect `at` (default: today) for each
    anthropic model on the org's channel (`Pricer.models`, keyed by registry id), as USD per
    million tokens in plain strings of at least two decimal places -- never `float`, so the
    wire encoding can't introduce a binary-fraction error. `effective_from` is the
    `start_date` of the row in effect. Display only: rule 4 forbids the API re-deriving
    dollars from this table for anything it serves elsewhere.

    `pricing` (a `common.model_pricing.Pricer`) may be `None` (no catalog on disk) -- the same
    "survivable absence" posture `otel_trueup.py`'s own loader takes; an empty table is the
    honest response, not a 500.
    """
    at = at or date.today()
    models: dict[str, dict] = {}
    if pricing is not None:
        for model, rates in pricing.models(at).items():
            effective = pricing.effective_from(model, at)
            if effective is None:
                continue
            models[model] = {"effective_from": effective.isoformat(),
                             **{kind: _per_million_str(rate) for kind, rate in rates.items()}}
    return {"as_of": at.isoformat(), "models": models}


# --------------------------------------------------------------- TZ track: timezone preference

def get_timezone(s: DbSession, catalog, user_id: str) -> dict:
    """`GET /v1/users/{id}/timezone`. `timezone: null` covers two cases the caller doesn't need
    to tell apart: no preference recorded yet, or no `users` row for this id at all (the
    single-user surface's own `id` has no guarantee one exists -- same tolerance every other
    route here already has for an unknown/quiet `user_id`, ground rule 2). Every bucketing
    call site already treats null the same as an explicit `"UTC"`, so the browser's
    detect-and-prompt flow only needs to act when this differs from the zone it just detected
    locally."""
    row = s.execute(catalog.get("dashboard", "get_user_timezone"), {"user_id": user_id}).first()
    return {"user_id": user_id, "timezone": row.timezone if row is not None else None}


def set_timezone(s: DbSession, catalog, user_id: str, tz_name: str) -> dict:
    """`PUT /v1/users/{id}/timezone`. Validated against the system's real IANA database
    (`zoneinfo`), not a hand-maintained allowlist -- a browser sends exactly what
    `Intl.DateTimeFormat().resolvedOptions().timeZone` returns, which is already an IANA key by
    construction, so a rejection here means the request is malformed, not that the zone is
    merely unusual.

    Upserts rather than requiring an existing `users` row (`set_user_timezone`'s own comment):
    this is often the very first write for a single-user id that telemetry ingest or directory
    sync has never seen -- the placeholder viewer id `"me"` (viewer.tsx) being the common case
    today, pending real auth."""
    if not tz_name:
        raise BadRequestError("timezone is required")
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        raise BadRequestError(f"unknown timezone {tz_name!r}")
    row = s.execute(catalog.get("dashboard", "set_user_timezone"),
                    {"user_id": user_id, "timezone": tz_name}).first()
    return {"user_id": user_id, "timezone": row.timezone}


# --------------------------------------------------------------- A8/R9: feedback

_VALID_VERDICTS = ("expected", "dismissed")


def submit_feedback(s: DbSession, catalog, session_id: str, beat_ref: str, verdict: str) -> dict:
    """`POST /v1/sessions/{id}/feedback` (A8). `beat_ref` is a free-text reference to what the
    feedback is about within the session; see `storage/schema/feedback.py`'s own docstring for
    why it isn't a real foreign key yet (nothing ships a stable per-beat id this pass)."""
    if not beat_ref:
        raise BadRequestError("beat_ref is required")
    if verdict not in _VALID_VERDICTS:
        raise BadRequestError(f"unknown verdict {verdict!r}; valid: {sorted(_VALID_VERDICTS)}")
    if s.execute(catalog.get("dashboard", "session_exists"), {"session_id": session_id}).first() is None:
        raise NotFoundError(f"no session {session_id!r}")

    row = s.execute(catalog.get("dashboard", "insert_feedback"),
                    {"session_id": session_id, "beat_ref": beat_ref, "verdict": verdict}).first()
    return {"id": row.id, "session_id": session_id, "beat_ref": beat_ref, "verdict": verdict,
           "created_at": row.created_at.isoformat()}
