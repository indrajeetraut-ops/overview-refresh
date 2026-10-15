"""Track A aggregation endpoints (MVP plan 2026-09-09). Framework-free (ground rule 1):
`app.py`'s routes are thin wrappers that call these and return the dict FastAPI validates
against the matching `schemas.manager.Envelope[...]` response_model.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import structlog
from sqlalchemy.orm import Session as DbSession

from common import git_ident
from server import directory
from server.ingest.git import features as git_features
from server.storage.queries import QueryCatalog

from .. import precedence
from .. import forecast
from ..config import ApiConfig
from ..errors import BadRequestError, NotFoundError
from . import aggregate
from . import budget_cycles
from . import budgets as budgets_service
from . import forecast_store
from . import quality

_log = structlog.get_logger(module="api.manager.service")

_NO_MONEY = {"usd_micros": None, "cost_basis": None, "cost_complete": None}


def _money(usd_micros: int | None, sources: list[str]) -> dict:
    """Wrap a precedence-resolved aggregate as `schemas.envelope.Money` -- `cost_basis` is
    the set of sources that actually contributed (ground rule 3: aggregates resolve
    precedence per session, then sum); `cost_complete` is false once any of them is the
    estimator, true for an all-vendor figure. `None` (not `0`) when there is nothing to
    report -- an empty scope, or a metric (like $/PR with no merged PRs) that has no value."""
    if usd_micros is None or not sources:
        return dict(_NO_MONEY)
    return {
        "usd_micros": usd_micros,
        "cost_basis": ",".join(sources),
        "cost_complete": "estimated" not in sources,
    }


def _repo_money(usd_micros: int) -> dict:
    """F3's repo rollup sums `cost_effective_usd_micros` directly (same convention the
    single-user repo rollup uses), not a per-session precedence walk -- `cost_complete` is
    unknown at this basis, never asserted true by omission."""
    return {"usd_micros": usd_micros, "cost_basis": "cost_effective", "cost_complete": None}


def _usd(usd_micros: int) -> float:
    return usd_micros / 1_000_000


def _budget_money(usd_micros: int | None) -> dict:
    """A budget-derived figure (a window-scaled budget or a seat allowance) as `Money`. Not
    measured spend, so `cost_basis` is `"budget"` and completeness does not apply (`None`), same
    honest-basis precedent as `_repo_money`."""
    if usd_micros is None:
        return dict(_NO_MONEY)
    return {"usd_micros": usd_micros, "cost_basis": "budget", "cost_complete": None}


def _active_cycle(s: DbSession, catalog: QueryCatalog, config: ApiConfig) -> budget_cycles.ActiveCycle:
    """The org's budget cycle, resolved ONCE per request at a service entry point and passed to
    everything below it (one single-row read; with no saved cycle, a recurring calendar month
    under `config.budget_period`)."""
    return budget_cycles.active(s, catalog, config.directory.default_org_id, config.budget_period)


def _window_budgets(budgets: dict, frm: datetime, to: datetime,
                    active: budget_cycles.ActiveCycle) -> dict[str, int | None]:
    """`budgets_for_scopes`'s per-period amounts, each measured against the viewed window (a
    whole period or period-to-date: the full budget; otherwise prorated by days, plan D9)."""
    return {
        scope_id: aggregate.window_budget_usd_micros(aggregate.budget_usd_micros(amount), frm, to,
                                                     active)
        for scope_id, amount in budgets.items()
    }


def _seat_monthly(budget_amount, members: int) -> int | None:
    """A seat's per-period allowance: its team's per-period budget / direct members (plan D2). `None`
    with no budget or no members -- never a divide-by-zero, never a guessed share."""
    micros = aggregate.budget_usd_micros(budget_amount)
    if not micros or not members:
        return None
    return micros // members


def _seat_allowances(member_ids: list[str], seat_monthly: int | None, seat_starts: dict[str, datetime],
                     frm: datetime, to: datetime,
                     active: budget_cycles.ActiveCycle) -> dict[str, int | None]:
    """Each member's allowance for the window, prorated from their seat start (plan §2)."""
    return {
        uid: aggregate.seat_allowance_usd_micros(seat_monthly, seat_starts.get(uid), frm, to,
                                                 active)
        for uid in member_ids
    }


def _cost(rows: list, config: ApiConfig) -> tuple[int, list[str]]:
    return precedence.resolve_aggregate(rows, config.dashboard.precedence.cost)


def _agent_spend(rows: list, config: ApiConfig) -> list[dict]:
    """The spend-by-agent donut: each agent's rows resolved through the same cost chain as the
    headline (`_cost`), so `sum(agent spend) == headline.spend` exactly -- one cost source. Each
    agent's `Money` carries that group's own `cost_basis`. Largest first, name as the tie-break
    so the order is deterministic."""
    out = []
    for agent, group in aggregate.group_rows_by_agent(rows).items():
        value, sources = _cost(group, config)
        out.append((agent, value, sources))
    out.sort(key=lambda t: (-t[1], t[0]))
    return [{"agent": agent, "spend": _money(value, sources)} for agent, value, sources in out]


def _user_team_map(s: DbSession, catalog: QueryCatalog, user_ids: list[str]) -> dict[str, str | None]:
    if not user_ids:
        return {}
    rows = s.execute(catalog.get("manager", "user_primary_teams"), {"user_ids": user_ids})
    return {r.user_id: r.team_id for r in rows}


def _teams_meta(s: DbSession, catalog: QueryCatalog, team_ids: list[str]) -> dict[str, dict]:
    if not team_ids:
        return {}
    names = {r.team_id: r.name for r in
            s.execute(catalog.get("manager", "teams_by_id"), {"team_ids": team_ids})}
    counts = {r.team_id: r.member_count for r in
             s.execute(catalog.get("manager", "team_member_counts"), {"team_ids": team_ids})}
    return {tid: {"name": names.get(tid, tid), "members": counts.get(tid, 0)} for tid in team_ids}


def _team_summary(team_id: str, meta: dict, rows: list, pr_rows: list,
                  window_budget_usd_micros: int | None, config: ApiConfig) -> dict:
    """`window_budget_usd_micros` is the team's monthly budget already scaled to the viewed
    window (`_window_budgets`), so `budget_pct`/`over_budget` compare like with like."""
    cost_value, cost_sources = _cost(rows, config)
    merged = aggregate.merged_pr_count(pr_rows)
    cost_per_pr = aggregate.blended_cost_per_pr_usd_micros(cost_value, merged)
    budget_pct = aggregate.budget_pct(cost_value, window_budget_usd_micros)
    over_budget = budget_pct is not None and budget_pct > 1.0
    return {
        "team_id": team_id,
        "name": meta["name"],
        "members": meta["members"],
        "spend": _money(cost_value, cost_sources),
        "budget_pct": budget_pct,
        "merged_prs": merged,
        "cost_per_pr": _money(cost_per_pr, cost_sources),
        "rework_rate": aggregate.rework_rate(pr_rows),
        "over_budget": over_budget,
    }


def _worth_a_look(teams: list[dict]) -> list[dict]:
    """A minimal, real, DB-backed set of findings. Each carries a typed entity ref, never a
    `screen`/`screenParam` (plan section 1 -- the client owns routing).

    TODO(A1-findings): richer findings (seat under-utilization, a person's recoverable
    spend) land once A2/A5 exist to source them from; this covers what A1's own inputs
    (team spend vs budget, merged-PR cost) can already say honestly.
    """
    findings = []
    for t in teams:
        if t["over_budget"]:
            findings.append({
                "id": f"over-budget-{t['team_id']}",
                "text": f"{t['name']} is over its budget this period.",
                "action": f"View {t['name']}",
                "severity": "red",
                "entity": {"kind": "team", "id": t["team_id"]},
            })
    priced = [t for t in teams if t["merged_prs"] > 0 and t["cost_per_pr"]["usd_micros"] is not None]
    if priced:
        best = min(priced, key=lambda t: t["cost_per_pr"]["usd_micros"])
        findings.append({
            "id": f"best-value-{best['team_id']}",
            "text": (f"{best['name']} shipped {best['merged_prs']} merged PRs at "
                    f"${round(_usd(best['cost_per_pr']['usd_micros']))} each — the best "
                    "cost-per-PR of any team."),
            "action": f"View {best['name']}",
            "severity": "green",
            "entity": {"kind": "team", "id": best["team_id"]},
        })
    return findings


def _months_ago(dt: datetime, months: int) -> datetime:
    year = dt.year
    month = dt.month - months
    while month <= 0:
        month += 12
        year -= 1
    return dt.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def org_overview(s: DbSession, catalog: QueryCatalog, config: ApiConfig,
                 frm: datetime, to: datetime, now: datetime | None = None) -> dict:
    """`GET /v1/org/overview` (A1). `now` (default: the current instant) bounds
    `daily_spend` to the window's elapsed days; tests pin it."""
    now = now or datetime.now(timezone.utc)
    org_id = config.directory.default_org_id
    active = _active_cycle(s, catalog, config)
    user_ids = directory.users_in_scope(s, catalog, "org", org_id)

    rows = aggregate.fetch_scope_sessions(s, catalog, user_ids, frm, to, shape="session")
    pr_rows = aggregate.fetch_scope_merged_prs(s, catalog, user_ids, frm, to)

    modes_present = sorted({r.datasource for r in rows if r.datasource})
    sessions_by_mode: dict[str, int] = {}
    for r in rows:
        if r.datasource:
            sessions_by_mode[r.datasource] = sessions_by_mode.get(r.datasource, 0) + 1

    cost_value, cost_sources = _cost(rows, config)
    prev_frm, prev_to = aggregate.previous_window(frm, to)
    # Only the cost chain is read over the previous window (vs_last_pct) -- no recoverable/
    # reasoning/session-type scalars needed, so this one stays the lighter "cost" shape.
    prev_rows = aggregate.fetch_scope_sessions(s, catalog, user_ids, prev_frm, prev_to, shape="cost")
    prev_cost, _prev_sources = _cost(prev_rows, config)

    recoverable = aggregate.recoverable(rows)
    org_budget = budgets_service.get_budget(s, catalog, "org", org_id, active.label)
    org_window_budget = aggregate.window_budget_usd_micros(
        aggregate.budget_usd_micros(org_budget["amount_usd"]), frm, to, active)

    # per-team breakdown, off the same org-wide fetch (one query pair, not one per team).
    user_team = _user_team_map(s, catalog, user_ids)
    by_team = aggregate.group_rows_by_team(rows, user_team)
    by_team_prs = aggregate.group_rows_by_team(pr_rows, user_team)
    team_ids = sorted(by_team.keys() | by_team_prs.keys())
    meta = _teams_meta(s, catalog, team_ids)
    team_budgets = _window_budgets(
        budgets_service.budgets_for_scopes(s, catalog, "team", team_ids, active.label),
        frm, to, active)

    teams = [
        _team_summary(tid, meta[tid], by_team.get(tid, []), by_team_prs.get(tid, []),
                     team_budgets.get(tid), config)
        for tid in team_ids
    ]
    teams.sort(key=lambda t: -(t["spend"]["usd_micros"] or 0))
    teams_over_budget = sum(1 for t in teams if t["over_budget"])

    merged_total = aggregate.merged_pr_count(pr_rows)
    cost_per_pr = aggregate.blended_cost_per_pr_usd_micros(cost_value, merged_total)

    # api-latency plan §4.4: the model mix is its own SQL aggregate (~8 rows back), not a
    # Python walk over every session's otel/models JSON -- `rows` (shape="session") no longer
    # carries either column.
    model_split_rows = (s.execute(catalog.get("manager", "scope_model_split"),
                                  {"user_ids": user_ids, "from": frm, "to": to}).all()
                        if user_ids else [])
    model_mix = aggregate.model_usage_breakdown(aggregate.model_split_from_rows(model_split_rows))

    data = {
        "headline": {
            "spend": _money(cost_value, cost_sources),
            "budget_pct": aggregate.budget_pct(cost_value, org_window_budget),
            "vs_last_pct": aggregate.pct_change(cost_value, prev_cost),
            "could_save": _money(recoverable["recoverable_usd_micros"], cost_sources),
            "dominant_cause": recoverable["dominant_cause"],
        },
        "highlights": {
            "active_teams": len(by_team),
            "active_devs": len({r.user_id for r in rows if r.user_id}),
            "tokens": sum(r.tokens_total or 0 for r in rows),
            "active_repos": len(aggregate.repo_rollup_canonical(rows)),
            "merged_prs": merged_total,
            "cost_per_pr": _money(cost_per_pr, cost_sources),
            "teams_over_budget": teams_over_budget,
            "rework_rate": aggregate.rework_rate(pr_rows),
            # QW2 (mvp-gap-closure-plan): already computed and wired into /v1/teams and
            # /v1/projects (below) -- just never threaded into this endpoint's own highlights.
            "avg_time_to_pr_days": aggregate.avg_time_to_merge_days(pr_rows),
            # QW3 (mvp-gap-closure-plan): same per-model split QW1 surfaced on session/PR
            # detail, folded org-wide instead of per-session.
            "model_mix": model_mix,
            # F5: same call org_usage() already makes -- reusable as-is now that the fold
            # populates info.reasoning_tokens.
            "reasoning_share": aggregate.reasoning_share(rows),
        },
        "teams": teams,
        "agent_spend": _agent_spend(rows, config),
        "agent_task_mix": aggregate.agent_task_mix(rows),
        "daily_spend": aggregate.daily_spend(rows, config.dashboard.precedence.cost, frm, to, now),
        "worth_a_look": _worth_a_look(teams),
    }
    return {
        "data": data,
        "mode": {"modes_present": modes_present, "sessions_by_mode": sessions_by_mode},
        "unavailable": None,
    }


def list_scopes(s: DbSession, catalog: QueryCatalog,
                visible_teams: list[str], visible_repos: list[str]) -> dict:
    """`GET /v1/scopes` (U7-econ-picker) -- names only, no timeframe param, no `limit`: the
    scope picker's full enumeration of what the viewer can see (`viewer.visible_teams`/
    `visible_repos`, already the F2-gated set). Unlike `teams_table`/`projects_table`, this
    never touches session rows -- team names come straight off `teams`, repo names are
    derived from the `repo_id` string itself (`git_ident.describe`, no DB join), so the
    result does not vary with the caller's timeframe."""
    names = {r.team_id: r.name for r in
             s.execute(catalog.get("manager", "teams_by_id"), {"team_ids": visible_teams})} \
        if visible_teams else {}
    teams = sorted(
        ({"id": tid, "name": names.get(tid, tid)} for tid in visible_teams),
        key=lambda t: t["name"],
    )
    projects = sorted(
        ({"id": rid, "name": git_ident.describe(rid)[1]} for rid in visible_repos),
        key=lambda p: p["name"],
    )
    return {"teams": teams, "projects": projects}


# ---------------------------------------------------------------------------- A3: tables

#: public sort key -> the (possibly dotted, into a Money sub-object) field it sorts by.
_TEAMS_SORT_FIELDS = {
    "spend": "spend.usd_micros", "budget_pct": "budget_pct", "members": "members",
    "merged_prs": "merged_prs", "cost_per_pr": "cost_per_pr.usd_micros",
    "avg_time_to_pr_days": "avg_time_to_pr_days", "rework_rate": "rework_rate",
    "last_active_at": "last_active_at",
}
_PROJECTS_SORT_FIELDS = {
    "spend": "spend.usd_micros", "merged_prs": "merged_prs", "contributors": "contributors",
    "avg_time_to_pr_days": "avg_time_to_pr_days", "rework_rate": "rework_rate",
    "last_active_at": "last_active_at",
}


def _table_page(rows: list[dict], sort: str | None, sort_fields: dict[str, str], q: str | None,
                limit: int, offset: int) -> dict:
    if sort is not None and sort not in sort_fields:
        raise BadRequestError(f"unknown sort key {sort!r}; expected one of {sorted(sort_fields)}")
    filtered = aggregate.search_by_name(rows, q)
    ordered = aggregate.sort_desc_none_last(filtered, sort_fields[sort]) if sort else filtered
    return {"items": aggregate.paginate(ordered, limit, offset), "total": len(filtered)}


def teams_table(s: DbSession, catalog: QueryCatalog, config: ApiConfig, frm: datetime, to: datetime,
               sort: str | None, q: str | None, limit: int, offset: int) -> dict:
    """`GET /v1/teams` (A3) -- the flat, searchable/sortable table replacing a sidebar
    enumeration that doesn't scale (iteration-plan C)."""
    org_id = config.directory.default_org_id
    active = _active_cycle(s, catalog, config)
    user_ids = directory.users_in_scope(s, catalog, "org", org_id)
    rows = aggregate.fetch_scope_sessions(s, catalog, user_ids, frm, to, shape="cost")
    pr_rows = aggregate.fetch_scope_merged_prs(s, catalog, user_ids, frm, to)

    user_team = _user_team_map(s, catalog, user_ids)
    by_team = aggregate.group_rows_by_team(rows, user_team)
    by_team_prs = aggregate.group_rows_by_team(pr_rows, user_team)
    team_ids = sorted(by_team.keys() | by_team_prs.keys())
    meta = _teams_meta(s, catalog, team_ids)
    budgets = _window_budgets(
        budgets_service.budgets_for_scopes(s, catalog, "team", team_ids, active.label),
        frm, to, active)

    # No separate utilization column: a team's utilization IS its window-scaled budget_pct
    # (cost-based seats plan §2), so a second, differently-computed number would only disagree.
    items = []
    for tid in team_ids:
        t_rows, t_prs = by_team.get(tid, []), by_team_prs.get(tid, [])
        summary = _team_summary(tid, meta[tid], t_rows, t_prs, budgets.get(tid), config)
        last_active = aggregate.last_active_at(t_rows)
        items.append({
            **summary,
            "avg_time_to_pr_days": aggregate.avg_time_to_merge_days(t_prs),
            "last_active_at": last_active.isoformat() if last_active else None,
        })

    page = _table_page(items, sort or "spend", _TEAMS_SORT_FIELDS, q, limit, offset)
    return {"data": page, "mode": {"modes_present": [], "sessions_by_mode": {}}, "unavailable": None}


def projects_table(s: DbSession, catalog: QueryCatalog, config: ApiConfig, frm: datetime, to: datetime,
                   sort: str | None, q: str | None, limit: int, offset: int) -> dict:
    """`GET /v1/projects` (A3) -- one row per canonical repo (REPO section 1), org-wide."""
    org_id = config.directory.default_org_id
    user_ids = directory.users_in_scope(s, catalog, "org", org_id)
    rows = aggregate.fetch_scope_sessions(s, catalog, user_ids, frm, to, shape="cost")
    pr_rows = aggregate.fetch_scope_merged_prs(s, catalog, user_ids, frm, to)

    repo_rollup = aggregate.repo_rollup_canonical(rows)
    contributors = aggregate.repo_contributors(rows)
    prs_by_repo = aggregate.merged_prs_by_repo(pr_rows)
    # git_artifacts.repo_id (prs_by_repo's keys) is git-source's own numeric scheme, not the
    # session-side one repo_rollup groups on -- resolve the bridge once for the whole page
    # rather than once per row (aggregate.resolve_git_repo_ids's own docstring has the why).
    git_repo_ids = aggregate.resolve_git_repo_ids(s, catalog, [b["repo_id"] for b in repo_rollup])
    last_active_by_repo: dict[tuple[str, str | None], datetime] = {}
    for r in rows:
        repo_id = getattr(r, "repo_id", None)
        if not repo_id or not getattr(r, "last_event_at", None):
            continue
        provenance = getattr(r, "repo_id_provenance", None)
        key = (repo_id, r.user_id if provenance == "path" else None)
        if key not in last_active_by_repo or r.last_event_at > last_active_by_repo[key]:
            last_active_by_repo[key] = r.last_event_at

    items = []
    for bucket in repo_rollup:
        key = (bucket["repo_id"], bucket["local_to_user"])
        _kind, label = git_ident.describe(bucket["repo_id"])
        t_prs = prs_by_repo.get(git_repo_ids.get(bucket["repo_id"]), [])
        last_active = last_active_by_repo.get(key)
        items.append({
            "repo_id": bucket["repo_id"],
            "local_to_user": bucket["local_to_user"],
            "name": label,
            "spend": _repo_money(bucket["cost_usd_micros"]),
            "merged_prs": aggregate.merged_pr_count(t_prs),
            "contributors": len(contributors.get(key, ())),
            "avg_time_to_pr_days": aggregate.avg_time_to_merge_days(t_prs),
            "rework_rate": aggregate.rework_rate(t_prs),
            "last_active_at": last_active.isoformat() if last_active else None,
        })

    page = _table_page(items, sort or "spend", _PROJECTS_SORT_FIELDS, q, limit, offset)
    return {"data": page, "mode": {"modes_present": [], "sessions_by_mode": {}}, "unavailable": None}


# ------------------------------------------------------------------------ A4: team detail

def _users_meta(s: DbSession, catalog: QueryCatalog, user_ids: list[str]) -> dict[str, dict]:
    if not user_ids:
        return {}
    rows = s.execute(catalog.get("manager", "users_by_id"), {"user_ids": user_ids})
    return {r.user_id: {"display_name": r.display_name or r.user_id} for r in rows}


def _person_ranked(user_id: str, meta: dict, rows: list, pr_rows: list,
                   seat_allowance_usd_micros: int | None, seat_start: datetime | None,
                   frm: datetime, to: datetime, config: ApiConfig) -> dict:
    """One row of A4's ranked people. Seat usage is spend over the person's own prorated seat
    allowance (cost-based seats plan §2); `status` is the headline verdict (room to save, then
    the seat band -- `person_headline_status`), `usage_status` the seat band on its own so an
    over-used seat stays visible when room to save outranks it. `recoverable_pct` and
    `seat_allowance` ride along so the UI can show why a person is (or isn't) flagged."""
    cost_value, cost_sources = _cost(rows, config)
    recoverable = aggregate.recoverable(rows)
    merged = aggregate.merged_pr_count(pr_rows)
    cost_per_pr = aggregate.blended_cost_per_pr_usd_micros(cost_value, merged)
    pct = aggregate.usage_pct(cost_value, seat_allowance_usd_micros)
    grace = (seat_allowance_usd_micros is not None
             and aggregate.in_grace(seat_start, to, config.utilization.new_seat_grace_days))
    seat_band = aggregate.seat_usage_status(pct, seat_start, to, config.utilization)
    room = aggregate.person_status(recoverable["recoverable_usd_micros"], cost_value,
                                   frm, to, config.utilization)
    return {
        "user_id": user_id,
        "name": meta["display_name"],
        "spend": _money(cost_value, cost_sources),
        "merged_prs": merged,
        "cost_per_pr": _money(cost_per_pr, cost_sources),
        "recoverable": _money(recoverable["recoverable_usd_micros"], cost_sources),
        "recoverable_pct": recoverable["recoverable_pct"],
        "status": aggregate.person_headline_status(room, seat_band),
        "usage_status": seat_band,
        "usage_pct": pct,
        "low_use": aggregate.is_low_use(pct, seat_start, to, config.utilization),
        "seat_allowance": _budget_money(seat_allowance_usd_micros),
        "in_grace": grace,
    }


def team_detail(s: DbSession, catalog: QueryCatalog, config: ApiConfig, team_id: str,
                frm: datetime, to: datetime) -> dict:
    """`GET /v1/teams/{id}` (A4). Caller has already passed `gate.require_team`."""
    active = _active_cycle(s, catalog, config)
    member_ids = directory.members_of(s, catalog, team_id, include_subteams=False)
    rows = aggregate.fetch_scope_sessions(s, catalog, member_ids, frm, to, shape="session")
    pr_rows = aggregate.fetch_scope_merged_prs(s, catalog, member_ids, frm, to)

    modes_present = sorted({r.datasource for r in rows if r.datasource})
    sessions_by_mode: dict[str, int] = {}
    for r in rows:
        if r.datasource:
            sessions_by_mode[r.datasource] = sessions_by_mode.get(r.datasource, 0) + 1

    meta = _teams_meta(s, catalog, [team_id])[team_id]
    budget = budgets_service.get_budget(s, catalog, "team", team_id, active.label)
    window_budget = aggregate.window_budget_usd_micros(
        aggregate.budget_usd_micros(budget["amount_usd"]), frm, to, active)
    team_summary = _team_summary(team_id, meta, rows, pr_rows, window_budget, config)

    seat_starts = aggregate.fetch_seat_starts(s, catalog, member_ids)
    seat_allowances = _seat_allowances(member_ids, _seat_monthly(budget["amount_usd"], meta["members"]),
                                       seat_starts, frm, to, active)

    by_user = aggregate.group_rows_by_user(rows)
    by_user_prs = aggregate.group_rows_by_user(pr_rows)
    # Every member, not only those active in the window: an idle seat is the clearest low-use
    # case and used to be invisible here (cost-based seats plan §0.6).
    user_ids = sorted(set(member_ids) | by_user.keys() | by_user_prs.keys())
    users_meta = _users_meta(s, catalog, user_ids)

    people = [
        _person_ranked(uid, users_meta.get(uid, {"display_name": uid}), by_user.get(uid, []),
                       by_user_prs.get(uid, []), seat_allowances.get(uid), seat_starts.get(uid),
                       frm, to, config)
        for uid in user_ids
    ]
    people.sort(key=lambda p: -(p["recoverable"]["usd_micros"] or 0))

    room_to_save = [p for p in people if p["status"] == "room-to-save"]
    low_use = [p for p in people if p["low_use"]]

    data = {
        "team": team_summary,
        "people": people,
        "highlights": {
            "members": meta["members"],
            "room_to_save_count": len(room_to_save),
            "low_use_count": len(low_use),
            "merged_prs": team_summary["merged_prs"],
            "cost_per_pr": team_summary["cost_per_pr"],
            "rework_rate": team_summary["rework_rate"],
            # QW2 (mvp-gap-closure-plan): same aggregate `/v1/teams` already uses, just never
            # threaded into this endpoint's own highlights.
            "avg_time_to_pr_days": aggregate.avg_time_to_merge_days(pr_rows),
        },
        "worth_a_look": [
            {
                "id": f"room-to-save-{p['user_id']}",
                "text": f"{p['name']} has recoverable spend this period.",
                "action": f"View {p['name']}",
                "severity": "amber",
                "entity": {"kind": "person", "id": p["user_id"]},
            }
            for p in room_to_save[:3]
        ],
    }
    return {
        "data": data,
        "mode": {"modes_present": modes_present, "sessions_by_mode": sessions_by_mode},
        "unavailable": None,
    }


# ------------------------------------------------------------------------ A5: person detail

def resolve_person_team(s: DbSession, catalog: QueryCatalog, person_id: str) -> str | None:
    """The person's primary team, for the route to gate on before doing any real work."""
    teams = directory.team_of(s, catalog, person_id)
    return teams[0] if teams else None


def _peer_cost_per_loc(s: DbSession, catalog: QueryCatalog, config: ApiConfig, team_id: str,
                       frm: datetime, to: datetime) -> dict[str, dict]:
    """A5-peer-task-mix: every teammate's `aggregate.linked_work_volume` in one pass --
    `aggregate.cheapest_peer`'s candidate pool, the subject included. Merged PRs in the window,
    each with its linked sessions' cost (`session_links_for_prs`). Same-team restriction is
    enforced by `directory.members_of(..., include_subteams=False)`."""
    member_ids = directory.members_of(s, catalog, team_id, include_subteams=False)
    pr_rows = aggregate.fetch_scope_merged_prs(s, catalog, member_ids, frm, to)
    if not pr_rows:
        return {}
    sessions_by_pr: dict[str, list[str]] = {}
    session_cost: dict[str, int] = {}
    for link in s.execute(catalog.get("manager", "session_links_for_prs"),
                          {"pr_ids": [r.artifact_id for r in pr_rows]}):
        sessions_by_pr.setdefault(link.artifact_id, []).append(link.session_id)
        session_cost[link.session_id] = link.cost_effective_usd_micros or 0
    return {
        uid: aggregate.linked_work_volume(prs, sessions_by_pr, session_cost, config.peer)
        for uid, prs in aggregate.group_rows_by_user(pr_rows).items()
    }


def _person_recent_sessions(s: DbSession, catalog: QueryCatalog, config: ApiConfig,
                            session_rows: list, limit: int = 10) -> list[dict]:
    """G1 (mvp-gap-closure-plan): a person's most recent sessions, each carrying which PR (if
    any) it fed and that PR's quality label -- `quality.quality_label`, computed the same way
    this endpoint's own `work_quality` already does (`quality_counts_for_prs`), just per
    session instead of per-label count.

    `session_git_links` can carry commit artifacts alongside the owning PR for one session
    (`SessionGitLink`'s own docstring); `pr_basic_facts` (`kind='pr'`) is what narrows the link
    rows down to actual PRs -- a session's commit links never survive that join.

    `session_rows` (from `fetch_scope_sessions`) deliberately includes subagent rows -- the
    person's aggregate spend/token totals must count their subagents' work. This display list
    is different: a Task-tool subagent is not a session this person ever started themselves
    (same reasoning as the A4 dashboard list's own `parent_session_id IS NULL` filter,
    dashboard.yaml), so it's excluded here rather than showing up as if it were one."""
    roots = [r for r in session_rows if r.parent_session_id is None]
    recent = sorted(roots, key=lambda r: r.started_at, reverse=True)[:limit]
    if not recent:
        return []
    session_ids = [r.session_id for r in recent]
    link_rows = s.execute(catalog.get("pr_class", "links_for_sessions"),
                          {"session_ids": session_ids}).all()
    candidate_ids = sorted({r.artifact_id for r in link_rows})
    pr_basics = ({r.artifact_id: r for r in
                 s.execute(catalog.get("manager", "pr_basic_facts"), {"pr_ids": candidate_ids})}
                if candidate_ids else {})
    prs_by_session: dict[str, list[str]] = {}
    for r in link_rows:
        if r.artifact_id in pr_basics:
            prs_by_session.setdefault(r.session_id, []).append(r.artifact_id)

    pr_ids = sorted(pr_basics)
    acceptance = quality.pr_acceptance_metrics(s, catalog, pr_ids)
    making = quality.making_cost_high(s, catalog, config, pr_ids)
    quality_by_pr = {
        pr_id: quality.quality_label(making[pr_id], acceptance.get(pr_id, {}), config)
        for pr_id in pr_ids
    }

    out = []
    for r in recent:
        linked = prs_by_session.get(r.session_id, [])
        merged = [pid for pid in linked if pr_basics[pid].merged_at is not None]
        # a finished (merged) outcome speaks to quality more than an open one still in review
        primary = merged[0] if merged else (linked[0] if linked else None)
        end = r.ended_at or r.last_event_at
        duration = ((end - r.started_at).total_seconds() / 60
                   if end and r.started_at else None)
        out.append({
            "session_id": r.session_id,
            # See PRSessionRef's docstring: session_info.info.title, already computed by the
            # causal graph fold. Read off the row's own `title` scalar (api-latency plan D3/
            # D7) -- `person_session_facts` carries it, not raw `info` (which this shape no
            # longer selects at all).
            "title": r.title,
            "repo_id": r.repo_id,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "duration_minutes": duration,
            "cost_usd_micros": r.cost_effective_usd_micros,
            "merged_prs": len(merged),
            "quality": quality_by_pr.get(primary) if primary else None,
            "reasoning_tokens": r.reasoning_tokens,
            "tokens_total": r.tokens_total,
        })
    return out


def person_detail(s: DbSession, catalog: QueryCatalog, config: ApiConfig, person_id: str,
                  frm: datetime, to: datetime) -> dict:
    """`GET /v1/people/{id}` (A5). Caller has already resolved the person's team and passed
    it to `gate.require_team`."""
    team_id = resolve_person_team(s, catalog, person_id)
    rows = aggregate.fetch_scope_sessions(s, catalog, [person_id], frm, to, shape="person")
    pr_rows = aggregate.fetch_scope_merged_prs(s, catalog, [person_id], frm, to)

    modes_present = sorted({r.datasource for r in rows if r.datasource})
    sessions_by_mode: dict[str, int] = {}
    for r in rows:
        if r.datasource:
            sessions_by_mode[r.datasource] = sessions_by_mode.get(r.datasource, 0) + 1

    cost_value, cost_sources = _cost(rows, config)
    recoverable = aggregate.recoverable(rows)
    merged = aggregate.merged_pr_count(pr_rows)
    cost_per_pr = aggregate.blended_cost_per_pr_usd_micros(cost_value, merged)
    meta = _users_meta(s, catalog, [person_id]).get(person_id, {"display_name": person_id})

    recent_pr_rows = s.execute(catalog.get("manager", "user_prs_recent"),
                               {"user_id": person_id, "limit": 10}).all()
    recent_prs = [
        {"artifact_id": r.artifact_id, "repo_id": r.repo_id, "state": r.state, "title": r.title,
         "created_at": r.created_at.isoformat(),
         "merged_at": r.merged_at.isoformat() if r.merged_at else None,
         "closed_at": r.closed_at.isoformat() if r.closed_at else None}
        for r in recent_pr_rows
    ]

    velocity_rows = s.execute(catalog.get("manager", "user_merged_prs_weekly"),
                              {"user_id": person_id, "since": _months_ago(to, 3)}).all()

    peer_compare = None
    if team_id:
        candidates = _peer_cost_per_loc(s, catalog, config, team_id, frm, to)
        best = aggregate.cheapest_peer(candidates, person_id, config.peer)
        if best:
            peer_name = _users_meta(s, catalog, [best["user_id"]]).get(
                best["user_id"], {"display_name": best["user_id"]})["display_name"]
            peer_compare = {**best, "name": peer_name}

    # X2: one WorkQualityCount per label, over this person's merged PRs in range. None (not
    # []) only when there are no merged PRs to classify at all -- an empty list is still the
    # honest "classified, nothing landed in every quadrant" answer once there IS at least one.
    pr_ids = [r.artifact_id for r in pr_rows]
    work_quality = quality.quality_counts_for_prs(s, catalog, config, pr_ids) if pr_ids else None

    # G2 (mvp-gap-closure-plan): "where the spend went" by channel, over this person's own
    # sessions in scope -- the same `aggregate.channel_totals` D2's `PRCost.channels` already
    # uses, just over `rows` (this person's sessions) instead of one PR's linked ones.
    channels = aggregate.channel_totals(rows)
    channels_out = (
        {**{f"{k}_usd_micros": v for k, v in channels["totals_usd_micros"].items()},
         "complete": channels["complete"]}
        if channels else None
    )

    data = {
        "person": {
            "user_id": person_id,
            "name": meta["display_name"],
            "team_id": team_id,
            "spend": _money(cost_value, cost_sources),
            "merged_prs": merged,
            "cost_per_pr": _money(cost_per_pr, cost_sources),
            "recoverable": _money(recoverable["recoverable_usd_micros"], cost_sources),
            "habits": aggregate.habits_from_recoverable(recoverable),
            "work_quality": work_quality,
            "peer_compare": peer_compare,
            "channels": channels_out,
        },
        "recent_prs": recent_prs,
        "recent_sessions": _person_recent_sessions(s, catalog, config, rows),
        "velocity": aggregate.velocity_weekly(velocity_rows),
    }
    return {
        "data": data,
        "mode": {"modes_present": modes_present, "sessions_by_mode": sessions_by_mode},
        "unavailable": None,
    }


# ------------------------------------------------------------------------ A6: project detail

def project_detail(s: DbSession, catalog: QueryCatalog, config: ApiConfig, repo_id: str,
                   frm: datetime, to: datetime) -> dict:
    """`GET /v1/projects/{id}` (A6). One canonical repo (REPO section 1) -- not user-scoped;
    the caller has already passed `gate.require_repo`. `repo_id` is the session-side identity
    (what A3/F3 hand callers); PR/issue data lives under git-source's own numeric repo_id, so
    every git_artifacts-backed field below resolves through `aggregate.resolve_git_repo_id`
    first (its docstring has the full why). `features` comes from F4
    (`quality.list_features_for_repo`); `None` only when no git-source repo resolves at all
    (no GitHub App connected for this repo), never for a connected repo that simply has none yet.
    """
    rows = s.execute(catalog.get("manager", "repo_cost_facts"),
                     {"repo_id": repo_id, "from": frm, "to": to}).all()
    git_repo_id = aggregate.resolve_git_repo_id(s, catalog, repo_id)
    pr_rows = (s.execute(catalog.get("manager", "repo_merged_prs_in_range"),
                        {"repo_id": git_repo_id, "from": frm, "to": to}).all()
              if git_repo_id else [])

    modes_present = sorted({r.datasource for r in rows if r.datasource})
    sessions_by_mode: dict[str, int] = {}
    for r in rows:
        if r.datasource:
            sessions_by_mode[r.datasource] = sessions_by_mode.get(r.datasource, 0) + 1

    cost_value, cost_sources = _cost(rows, config)
    merged = aggregate.merged_pr_count(pr_rows)
    cost_per_pr = aggregate.blended_cost_per_pr_usd_micros(cost_value, merged)

    by_user = aggregate.group_rows_by_user(rows)
    by_user_prs = aggregate.group_rows_by_user(pr_rows)
    user_ids = sorted(by_user.keys() | by_user_prs.keys())
    users_meta = _users_meta(s, catalog, user_ids)

    contributors = []
    for uid in user_ids:
        u_cost, u_sources = _cost(by_user.get(uid, []), config)
        contributors.append({
            "user_id": uid,
            "name": users_meta.get(uid, {"display_name": uid})["display_name"],
            "spend": _money(u_cost, u_sources),
            "merged_prs": aggregate.merged_pr_count(by_user_prs.get(uid, [])),
        })
    contributors.sort(key=lambda c: -(c["spend"]["usd_micros"] or 0))

    lines_added = sum((r.stats or {}).get("additions", 0) for r in pr_rows)
    lines_removed = sum((r.stats or {}).get("deletions", 0) for r in pr_rows)

    # G4 (mvp-gap-closure-plan): every PR touching this repo in range, any state -- rebuilt
    # directly off git_artifacts (repo_prs_in_range) rather than waiting on F4's
    # feature-boundary grouping, which `features` above still gates on.
    all_pr_rows = (s.execute(catalog.get("manager", "repo_prs_in_range"),
                            {"repo_id": git_repo_id, "from": frm, "to": to}).all()
                  if git_repo_id else [])
    pr_author_ids = {r.user_id for r in all_pr_rows if r.user_id}
    pr_authors_meta = _users_meta(s, catalog, list(pr_author_ids)) if pr_author_ids else {}
    all_pr_ids = [r.artifact_id for r in all_pr_rows]
    pr_costs = ({r.pr_artifact_id: r.cost_micros for r in
                s.execute(catalog.get("manager", "pr_costs_for_ids"), {"pr_ids": all_pr_ids})}
               if all_pr_ids else {})
    prs = [
        {
            "artifact_id": r.artifact_id, "number": r.number, "title": r.title,
            "author_name": ((pr_authors_meta.get(r.user_id, {}).get("display_name")
                            if r.user_id else None) or r.author_login),
            "state": r.state,
            "cost_usd_micros": pr_costs.get(r.artifact_id),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "merged_at": r.merged_at.isoformat() if r.merged_at else None,
            "closed_at": r.closed_at.isoformat() if r.closed_at else None,
        }
        for r in all_pr_rows
    ]

    # A6-reopens: PRs in this repo reopened at least once, from the transition log. None -- not
    # 0 -- when NO PR here has any transition row (an all-pre-capture repo, whose reopen history
    # is unknown until backfill 4a runs); a real count once at least one PR has been captured.
    reopens_by_pr = ({r.artifact_id: r.reopens for r in
                     s.execute(catalog.get("manager", "pr_reopen_counts"), {"pr_ids": all_pr_ids})}
                    if all_pr_ids else {})
    reopened_prs = (sum(1 for n in reopens_by_pr.values() if n >= 1)
                   if reopens_by_pr else None)

    _kind, label = git_ident.describe(repo_id)
    data = {
        "project": {
            "repo_id": repo_id,
            "name": label,
            "spend": _money(cost_value, cost_sources),
            "merged_prs": merged,
            "cost_per_pr": _money(cost_per_pr, cost_sources),
            "rework_rate": aggregate.rework_rate(pr_rows),
        },
        "contributors": contributors,
        "highlights": {
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "reopened_prs": reopened_prs,
        },
        # F4 is GitHub-only in MVP (H3): None for any other repo, never for a GitHub repo
        # that simply has zero features yet (that's a real [], not an absence).
        "features": quality.list_features_for_repo(s, catalog, git_repo_id) if git_repo_id else None,
        "prs": prs,
    }
    return {
        "data": data,
        "mode": {"modes_present": modes_present, "sessions_by_mode": sessions_by_mode},
        "unavailable": None,
    }


# ------------------------------------------------------------------------------- D2: PR detail

def resolve_pr_visibility_repo_id(s: DbSession, catalog: QueryCatalog, pr_id: str) -> str | None:
    """The session-side repo_id `gate.require_repo` needs to authorize a PR route, resolved
    from the PR's own git-source `repo_id` (`aggregate.resolve_session_repo_id`) -- the same
    two-lookup shape `person_detail`'s route uses via `resolve_person_team`. `None` when the
    PR doesn't exist or its repo isn't bridged (`git_repos` has no matching row)."""
    basics = {r.artifact_id: r for r in
             s.execute(catalog.get("manager", "pr_basic_facts"), {"pr_ids": [pr_id]})}
    pr = basics.get(pr_id)
    if pr is None:
        return None
    return aggregate.resolve_session_repo_id(s, catalog, pr.repo_id)


def pr_detail(s: DbSession, catalog: QueryCatalog, config: ApiConfig, pr_id: str) -> dict:
    """`GET /v1/prs/{id}` (D2) -- a genuinely new route (unlike D1's `?view=` addition), since
    a PR has no existing single-user endpoint to extend. `pr_id` is the git-source artifact id
    (`github:<repo_num>:pr:<number>`) A6's `features[]`/A5's `recent_prs` already hand callers.

    `sessions` walks the same `session_git_links` join X2's `making_cost_high` already uses;
    `channels`/pre-post cost split (D2) and `review`/`quality` (X1/X2) are all composed here
    from functions Phase 1/2 already shipped -- no new worker pass.
    """
    basics = {r.artifact_id: r for r in
             s.execute(catalog.get("manager", "pr_basic_facts"), {"pr_ids": [pr_id]})}
    pr = basics.get(pr_id)
    if pr is None:
        raise NotFoundError(f"no PR {pr_id!r}")

    acceptance = quality.pr_acceptance_metrics(s, catalog, [pr_id])[pr_id]
    review_rows = list(s.execute(catalog.get("manager", "pr_review_children"), {"pr_ids": [pr_id]}))

    session_ids = [r[0] for r in s.execute(catalog.get("git", "sessions_for_artifacts"),
                                           {"artifact_ids": [pr_id]})]
    session_rows = (s.execute(catalog.get("manager", "sessions_by_ids"),
                              {"session_ids": session_ids}).all() if session_ids else [])

    modes_present = sorted({r.datasource for r in session_rows if r.datasource})
    sessions_by_mode: dict[str, int] = {}
    for r in session_rows:
        if r.datasource:
            sessions_by_mode[r.datasource] = sessions_by_mode.get(r.datasource, 0) + 1

    cost_value, cost_sources = _cost(session_rows, config)
    channels = aggregate.channel_totals(session_rows)
    channels_out = (
        {**{f"{k}_usd_micros": v for k, v in channels["totals_usd_micros"].items()},
         "complete": channels["complete"]}
        if channels else None
    )
    cost_split = (aggregate.pre_post_open_cost_split(session_rows, pr.created_at)
                 if session_rows and pr.created_at else None)
    making_high = quality.making_cost_high(s, catalog, config, [pr_id])[pr_id]
    label = quality.quality_label(making_high, acceptance, config)

    reviewer_ids = sorted({r.user_id for r in review_rows if r.user_id})
    reviewers_meta = _users_meta(s, catalog, reviewer_ids)
    author_meta = _users_meta(s, catalog, [pr.user_id]).get(pr.user_id) if pr.user_id else None

    reviews = [
        {
            "reviewer_user_id": r.user_id,
            "reviewer_name": (reviewers_meta.get(r.user_id, {}).get("display_name")
                              if r.user_id else r.author_login),
            "state": r.state,
            "submitted_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in review_rows
    ]

    sessions = [
        {
            "session_id": r.session_id,
            # The causal graph already lifts the vendor's ai_title into session_info.info.title
            # (PRSessionRef's own docstring has the full path) -- just never read here before.
            "title": (r.info or {}).get("title"),
            "user_id": r.user_id,
            "cost_usd_micros": r.cost_effective_usd_micros,
            "role": ("rework" if r.started_at is not None and pr.created_at is not None
                     and r.started_at >= pr.created_at else "build"),
        }
        for r in session_rows
    ]

    # Deterministic, real signals only (no LLM/content, same as X4's notes). The reopen flag is
    # live (git_artifact_events); all three acceptance-axis signals are captured now and each
    # gets its own flag when it crosses its config.quality cutoff (X2-config).
    q = config.quality
    churn_post_lines, rework_ratio = acceptance["churn_post_lines"], acceptance["rework_ratio"]
    flags = []
    if (acceptance["review_rounds"] or 0) >= q.review_rounds_high:
        flags.append({
            "label": "Multiple review rounds",
            "detail": f"{acceptance['review_rounds']} round(s) of changes requested before approval.",
            "sev": "amber",
        })
    if (acceptance["reopens"] or 0) >= q.reopens_high:
        flags.append({
            "label": "Reopened",
            "detail": f"Closed and reopened {acceptance['reopens']} time(s) before landing.",
            "sev": "amber",
        })
    if rework_ratio is not None and rework_ratio >= q.rework_ratio_high:
        flags.append({
            "label": "Heavy rework",
            "detail": f"Churned {churn_post_lines} lines after opening, {round(rework_ratio * 100)}% of the final size.",
            "sev": "amber",
        })
    if label == "Costly rework":
        flags.append({
            "label": "Costly rework",
            "detail": "Cost more than most recent PRs in this repo, and had a messy review "
                      "(changes requested, reopened, or heavy churn after opening).",
            "sev": "red",
        })

    stats = pr.stats or {}
    data = {
        "pr": {
            "artifact_id": pr.artifact_id,
            "number": pr.number,
            "repo_id": pr.repo_id,
            "title": pr.title,
            "state": pr.state,
            "author_user_id": pr.user_id,
            "author_name": (author_meta or {}).get("display_name") or pr.author_login,
            "branch": pr.branch,
            "base_branch": pr.base_branch,
            "labels": pr.labels or [],
            "linked_issue": pr.linked_issue,
            "created_at": pr.created_at.isoformat() if pr.created_at else None,
            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
            "closed_at": pr.closed_at.isoformat() if pr.closed_at else None,
            "lines_added": stats.get("additions"),
            "lines_removed": stats.get("deletions"),
            "files_changed": stats.get("changed_files"),
            "commits": stats.get("commits"),
        },
        "cost": {
            "spend": _money(cost_value, cost_sources),
            "pre_open": _money(cost_split["pre_open_usd_micros"], cost_sources) if cost_split else None,
            "post_open": _money(cost_split["post_open_usd_micros"], cost_sources) if cost_split else None,
            "channels": channels_out,
        },
        "review": {
            "review_rounds": acceptance["review_rounds"],
            "time_to_first_review_hours": acceptance["time_to_first_review_hours"],
            "time_to_merge_days": acceptance["time_to_merge_days"],
            "comment_count": acceptance["comment_count"],
            "reviews": reviews,
        },
        "quality": label,
        # X1-churn: post-open push churn, from commit line stats the git_commit_stats worker
        # fills (Track churn/reopen, Landing 2). `churn_post_lines` is the lines the PR churned
        # in pushes after it opened; `rework_ratio` is that over the PR's final size. Both None
        # (not 0) when the PR's post-open pushes have no captured stats yet (pre-Landing-2 /
        # pre-backfill), never guessed.
        "churn_post_lines": churn_post_lines,
        "rework_ratio": rework_ratio,
        # `churn_post_usd_micros`: a dollar figure for post-open rework, deliberately still None.
        # Commit-line churn has no honest lines->$ conversion; the dollar cost of rework is the
        # recoverable/composite question (Landing 3, gated on segment-level pricing), and
        # post-open *session* spend is already surfaced as `cost.post_open` above.
        "churn_post_usd_micros": None,
        # X1-reopen: reopen count from the transition log (`git_artifact_events`). None -- not 0 --
        # for a PR ingested before that log existed (its reopen history is genuinely unknown
        # until backfill 4a runs), which `pr_acceptance_metrics` already distinguishes.
        "reopens": acceptance["reopens"],
        "sessions": sessions,
        "flags": flags,
        # QW1 (mvp-gap-closure-plan): per-model token share across every session that touched
        # this PR -- `aggregate.model_split` already reads OTel-ledger-or-JSONL-fold per row,
        # so this works whichever datasource each linked session happens to be in.
        "models": (aggregate.model_usage_breakdown(aggregate.model_split(session_rows))
                  if session_rows else None),
    }
    return {
        "data": data,
        "mode": {"modes_present": modes_present, "sessions_by_mode": sessions_by_mode},
        "unavailable": None,
    }


# ------------------------------------------------------------------------------- D3: feature detail

def resolve_feature_visibility_repo_id(s: DbSession, catalog: QueryCatalog,
                                       feature_id: str) -> str | None:
    """Same two-lookup gate shape as D2's `resolve_pr_visibility_repo_id`: a feature's own
    `ref.project_key` is git-source's numeric repo_num, bridged back to the session-side
    scheme `gate.require_repo` checks against. `None` when the id doesn't resolve to a
    feature at all, or its repo isn't bridged."""
    f = quality.feature_by_id(s, catalog, feature_id)
    repo_num = git_features.ref_repo_num(f.ref) if f else None
    if repo_num is None:
        return None
    return aggregate.resolve_session_repo_id(s, catalog, f"github:{repo_num}")


def _repo_cost_envelope(s: DbSession, catalog: QueryCatalog, config: ApiConfig,
                        git_repo_id: str | None) -> dict | None:
    """D3-envelope-scope: the `$/PR` envelope over a repo's own trailing merged-PR costs
    (`config.forecast.envelope_trailing_days`). `None` -- so the caller falls back to the
    feature-local envelope -- when the repo isn't GitHub-bridged, has no merged PRs in-window,
    or has fewer than `config.forecast.envelope_min_prs` priced ones (too thin to be sturdier
    than feature-local). Reuses the same `pr_costs_for_ids` cost path as everything else, so no
    second cost source is introduced (one-cost-source rule)."""
    if not git_repo_id:
        return None
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=config.forecast.envelope_trailing_days)
    pr_ids = [r.artifact_id for r in
              s.execute(catalog.get("manager", "repo_merged_prs_in_range"),
                        {"repo_id": git_repo_id, "from": window_start, "to": now})]
    if not pr_ids:
        return None
    costs = {r.pr_artifact_id: r.cost_micros for r in
             s.execute(catalog.get("manager", "pr_costs_for_ids"), {"pr_ids": pr_ids})}
    values = [c for c in costs.values() if c and c > 0]
    if len(values) < config.forecast.envelope_min_prs:
        return None
    return forecast.cost_per_pr_envelope(values)


def feature_detail(s: DbSession, catalog: QueryCatalog, config: ApiConfig, feature_id: str) -> dict:
    """`GET /v1/features/{id}` (D3). `feature_id` is the routable id `quality._feature_id`
    hands out on A6's `features[]` (`github:<repo_num>:issue:<n>` or `pr:<pr_artifact_id>`),
    never the display `issue_key` -- re-derived fresh via `quality.feature_by_id` rather than
    stored, so a stale id fails closed as 404 instead of resolving to a mismatched feature.

    `cost_to_finish` (X5) envelopes over the *repo's* trailing merged-PR cost distribution
    (`config.forecast.envelope_trailing_days`), falling back to this feature's own member PRs
    when the repo is below `envelope_min_prs` (D3-envelope-scope). `benchmark` (cost percentile
    vs peer features in the same repo, D3-benchmark) is `None` (cold-start) until the repo has
    enough priced peer features; a lead-time axis is still TODO(D3-benchmark-time).
    """
    f = quality.feature_by_id(s, catalog, feature_id)
    if f is None:
        raise NotFoundError(f"no feature {feature_id!r}")

    pr_basics = {r.artifact_id: r for r in
                s.execute(catalog.get("manager", "pr_basic_facts"),
                         {"pr_ids": f.member_pr_ids}).all()} if f.member_pr_ids else {}
    pr_costs = {r.pr_artifact_id: r.cost_micros for r in
               s.execute(catalog.get("manager", "pr_costs_for_ids"),
                        {"pr_ids": f.member_pr_ids})} if f.member_pr_ids else {}
    session_rows = (s.execute(catalog.get("manager", "sessions_by_ids"),
                              {"session_ids": f.session_ids}).all() if f.session_ids else [])

    modes_present = sorted({r.datasource for r in session_rows if r.datasource})
    sessions_by_mode: dict[str, int] = {}
    for r in session_rows:
        if r.datasource:
            sessions_by_mode[r.datasource] = sessions_by_mode.get(r.datasource, 0) + 1

    channels = aggregate.channel_totals(session_rows)
    channels_out = (
        {**{f"{k}_usd_micros": v for k, v in channels["totals_usd_micros"].items()},
         "complete": channels["complete"]}
        if channels else None
    )

    member_prs = []
    for pr_id in f.member_pr_ids:
        pr = pr_basics.get(pr_id)
        if pr is None:
            continue
        member_prs.append({
            "artifact_id": pr.artifact_id, "number": pr.number, "title": pr.title,
            "state": pr.state,
            "created_at": pr.created_at.isoformat() if pr.created_at else None,
            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
            "cost": _money(pr_costs.get(pr_id, 0), ["cost_effective"]),
        })

    contributor_ids = sorted({r.user_id for r in session_rows if r.user_id}
                            | {pr.user_id for pr in pr_basics.values() if pr.user_id})
    contributors_meta = _users_meta(s, catalog, contributor_ids)
    contributors = [
        {"user_id": uid, "name": contributors_meta.get(uid, {"display_name": uid})["display_name"]}
        for uid in contributor_ids
    ]

    session_starts = [r.started_at for r in session_rows if r.started_at is not None]
    pr_opens = [pr.created_at for pr in pr_basics.values() if pr.created_at is not None]
    pr_merges = [pr.merged_at for pr in pr_basics.values() if pr.merged_at is not None]
    lifecycle = {
        "first_session_started_at": min(session_starts).isoformat() if session_starts else None,
        "first_pr_opened_at": min(pr_opens).isoformat() if pr_opens else None,
        "last_merged_at": max(pr_merges).isoformat() if pr_merges else None,
    }

    repo_num = git_features.ref_repo_num(f.ref)
    git_repo_id = f"github:{repo_num}" if repo_num else None

    open_prs = sum(1 for pr in pr_basics.values() if pr.state == "open")
    # D3-envelope-scope: draw the $/PR envelope from the whole repo's trailing merged PRs, not
    # just this feature's own members -- most features are single-PR, so a feature-local envelope
    # collapses to one point. Falls back to feature-local when the repo is below the volume floor.
    envelope = (_repo_cost_envelope(s, catalog, config, git_repo_id)
                or forecast.cost_per_pr_envelope(list(pr_costs.values())))
    cost_to_finish = forecast.feature_cost_to_finish(open_prs, envelope)

    # D3-benchmark: where this feature's spend sits among its repo peers ("vs similar features").
    features_in_repo = (quality.list_features_for_repo(s, catalog, git_repo_id)
                        if git_repo_id else [])
    benchmark = aggregate.feature_benchmark(features_in_repo, feature_id) if features_in_repo else None

    data = {
        "id": feature_id,
        "issue_key": git_features.ref_issue_key(f.ref),
        "title": ((f.issue or {}).get("title") if f.issue else None) or feature_id,
        "status": {"shipped": "shipped", "in_progress": "in-progress",
                  "not_started": "in-progress"}[f.status],
        "repo_id": git_repo_id,
        "spend": _money(f.cost_usd_micros, ["cost_effective"]) if f.session_ids else _money(0, []),
        "channels": channels_out,
        "member_prs": member_prs,
        "contributors": contributors,
        "lifecycle": lifecycle,
        "cost_to_finish": cost_to_finish,
        "benchmark": benchmark,
    }
    return {
        "data": data,
        "mode": {"modes_present": modes_present, "sessions_by_mode": sessions_by_mode},
        "unavailable": None,
    }


#: X5: how far past the requested window's own end the forecast cone (D3/E1) projects.
_FORECAST_PERIODS_AHEAD = 6


# ------------------------------------------------------------------------------- E1: economics

def economics(s: DbSession, catalog: QueryCatalog, config: ApiConfig, scope_type: str,
              scope_id: str, frm: datetime, to: datetime) -> dict:
    """`GET /v1/economics?scope_type=&scope_id=` (E1) -- `Dist` cost/time stats,
    `pr_cost_envelope` (+forecast cone, X5), merged/reverted PR cost, rework ratio, and
    `budget_burn`, for scope in org / any team / any project. The route gates with the same
    `_gate_scope` helper the budgets routes already use (F1); `scope_type`/`scope_id` follow
    that same two-part shape (never a single composite `scope=` string to parse).

    org/team scope walks the same user-scoped fetch every Track A endpoint uses
    (`directory.users_in_scope` -> `fetch_scope_sessions`/`fetch_scope_merged_prs`) --
    correct for "this team's spend, across whatever repos they touch." `repo` scope must
    instead walk `repo_cost_facts`/`repo_merged_prs_in_range` (A6's own precedent):
    a repo's contributors' *other* unrelated repos must never leak into a project's own
    economics.

    `budget_burn` (E1-budget-period) forecasts over the CALENDAR MONTH that *contains the
    window's end* (`to`) -- period-to-date actuals, projected to the month's last day -- not the
    requested display window. It carries no budget amount, only spend, so the org's budget cycle
    does not enter it: it stays on the calendar month under every cycle, like the forecast
    (TODO(A4): per-cycle burn-up and forecast). A
    7-day window still forecasts to month-end, not to day 7. `to` is the "as-of" anchor
    (deterministic and testable, unlike wall-clock `now`, and honest for a historical window
    "as of the date you were looking at").
    """
    if scope_type not in budgets_service.SCOPE_TYPES:
        raise BadRequestError(f"unknown scope_type {scope_type!r}; expected one of "
                              f"{budgets_service.SCOPE_TYPES}")

    # One answer to "which sessions does this scope cover", shared with `/v1/forecast`.
    _sessions_between, user_ids = aggregate.scope_cost_fetcher(s, catalog, scope_type, scope_id)

    if scope_type == "repo":
        git_repo_id = aggregate.resolve_git_repo_id(s, catalog, scope_id)
        pr_rows = (s.execute(catalog.get("manager", "repo_merged_prs_in_range"),
                            {"repo_id": git_repo_id, "from": frm, "to": to}).all()
                  if git_repo_id else [])
    else:
        pr_rows = aggregate.fetch_scope_merged_prs(s, catalog, user_ids, frm, to)

    rows = _sessions_between(frm, to)

    modes_present = sorted({r.datasource for r in rows if r.datasource})
    sessions_by_mode: dict[str, int] = {}
    for r in rows:
        if r.datasource:
            sessions_by_mode[r.datasource] = sessions_by_mode.get(r.datasource, 0) + 1

    cost_value, cost_sources = _cost(rows, config)
    merged = aggregate.merged_pr_count(pr_rows)
    cost_per_pr = aggregate.blended_cost_per_pr_usd_micros(cost_value, merged)

    pr_ids = [r.artifact_id for r in pr_rows]
    pr_costs = ({r.pr_artifact_id: r.cost_micros for r in
                s.execute(catalog.get("manager", "pr_costs_for_ids"), {"pr_ids": pr_ids})}
               if pr_ids else {})
    # dist_stats (unlike cost_per_pr_envelope) reports in whole dollars, matching the
    # mockup's own unitless `Dist` convention (`PercentileBar`'s `unit: "$"` formats these
    # directly as `$${v}`) -- divide out of usd_micros here, once, rather than emitting
    # micros and forcing the client to guess the scale.
    cost_values = [float(v) / 1_000_000 for v in pr_costs.values()]
    time_values = [(r.merged_at - r.created_at).total_seconds() / 86400 for r in pr_rows
                  if r.merged_at is not None and r.created_at is not None]

    envelope = forecast.cost_per_pr_envelope(list(pr_costs.values()))
    cone = forecast.forecast_cone(envelope, _FORECAST_PERIODS_AHEAD) if envelope else []

    reverted_rows = [r for r in pr_rows if r.is_revert]
    reverted_cost_values = [pr_costs.get(r.artifact_id, 0) for r in reverted_rows]
    reverted_pr_cost = (_money(round(sum(reverted_cost_values) / len(reverted_cost_values)),
                               ["cost_effective"])
                        if reverted_cost_values else _money(0, []))

    # E1-budget-period: the burn-up runs over the calendar month that *contains*
    # the window's end, not the requested display window -- `days_in_period` is the period's
    # own length (e.g. 31), while `days_reported` is how much of it has elapsed as of `to`, so
    # the projection leg (X5) actually extends to period-end instead of collapsing to nothing
    # (which it did when `days_in_period == window length == days reported`).
    period_start, period_end = budgets_service.period_bounds(budget_cycles.DEFAULT_LABEL, to.date())
    period_start_dt = datetime(period_start.year, period_start.month, period_start.day,
                               tzinfo=to.tzinfo)
    days_in_period = (period_end - period_start).days
    days_reported = (to.date() - period_start).days  # elapsed days before the in-progress `to` day
    period_rows = _sessions_between(period_start_dt, to)
    daily_actuals: list[int] = []
    if period_rows:
        # A real (possibly all-zero) daily series only once there is *some* session activity
        # in scope at all -- an empty scope must cold-start `budget_burn`, not report a flat
        # $0 burn-up (absent vs. zero, the same discipline every other block here follows).
        daily_totals: dict[int, int] = {}
        for r in period_rows:
            if r.started_at is None:
                continue
            day = (r.started_at.date() - period_start).days
            if 0 <= day < days_reported:
                daily_totals[day] = daily_totals.get(day, 0) + (r.cost_effective_usd_micros or 0)
        daily_actuals = [daily_totals.get(d, 0) for d in range(days_reported)]
    budget_burn = forecast.budget_burnup(daily_actuals, days_in_period)

    data = {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "cost_dist": forecast.dist_stats(cost_values),
        "time_to_merge_dist": forecast.dist_stats(time_values),
        "pr_cost_envelope": envelope,
        "forecast_cone": cone,
        "merged_prs": merged,
        "reverted_prs": len(reverted_rows),
        "merged_pr_cost": _money(cost_per_pr, cost_sources),
        "reverted_pr_cost": reverted_pr_cost,
        "rework_ratio": aggregate.rework_rate(pr_rows),
        "budget_burn": budget_burn,
    }
    return {
        "data": data,
        "mode": {"modes_present": modes_present, "sessions_by_mode": sessions_by_mode},
        "unavailable": None,
    }


# ------------------------------------------------------------- T6: budget forecast and plan

_NO_FORECAST_MODE = {"modes_present": [], "sessions_by_mode": {}}


def _forecast_budget_micros(amount) -> int | None:
    """A stored per-period budget as integer micros; `None` for no row or a value that is not a
    finite number (a numeric column can hold NaN) -- a budget we cannot read is no budget."""
    if amount is None:
        return None
    try:
        value = Decimal(str(amount))
    except Exception:
        return None
    if not value.is_finite():
        return None
    return int((value * 1_000_000).to_integral_value())


def _forecast_budget(amount, active: budget_cycles.ActiveCycle, as_of: date) -> int | None:
    """The budget the forecast compares against, which is by CALENDAR MONTH. With a monthly cycle
    that is the stored amount, unscaled (as before cycles existed). With any other kind the
    stored amount is per cycle period, so it is expressed per calendar month by days:
    `amount x days in as_of's calendar month / days in the cycle period containing as_of`.
    TODO(A4): run the forecast per org cycle instead of scaling to the calendar month."""
    micros = _forecast_budget_micros(amount)
    if micros is None or active.cycle.kind == "month":
        return micros
    period_days = budget_cycles.period_containing(active.cycle, as_of)["days_total"]
    return round(micros * calendar.monthrange(as_of.year, as_of.month)[1] / period_days)


def _log_unreadable(run_id: str | None, reason: str) -> None:
    _log.warning("forecast.run_unreadable", run_id=run_id, reason=reason)


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=k) for k in range(max((last - first).days + 1, 0))]


def _sum_days(daily: dict[date, int], first: date, last: date) -> int:
    return sum(daily.get(d, 0) for d in _days(first, last))


def _session_modes(rows: list) -> dict:
    """The aggregate mode block of the sessions a response was built from (as economics does)."""
    by_session: dict[str, str] = {}
    for r in rows:
        if r.datasource:
            by_session[r.session_id] = r.datasource
    counts: dict[str, int] = {}
    for mode in by_session.values():
        counts[mode] = counts.get(mode, 0) + 1
    return {"modes_present": sorted(counts), "sessions_by_mode": counts}


def _scope_daily_actuals(s: DbSession, catalog: QueryCatalog, run: forecast_store.StoredRun,
                         scope_type: str, scope_id: str, first: date, last: date) -> tuple[dict, list]:
    """The scope's actual spend per local day (`run.timezone`) over `first..last`, from the same
    session fetch `/v1/economics` uses, and the rows that fell in that range."""
    fetch, _users = aggregate.scope_cost_fetcher(s, catalog, scope_type, scope_id)
    frm, to = aggregate.local_window(first, last, run.tz)
    rows = aggregate.rows_in_local_range(fetch(frm, to), run.tz, first, last)
    return aggregate.daily_actuals(rows, run.tz, first, last), rows


def _forecast_series_view(run: forecast_store.StoredRun, series: dict[str, dict],
                          daily: dict[date, int], budget: int | None, config: ApiConfig) -> list[dict]:
    fc = config.forecast
    baseline = series.get("baseline")
    actual_to_date = [_sum_days(daily, d.start, d.forecast_from - timedelta(days=1))
                      for d in run.periods]
    out = []
    for preset in run.presets:
        ser = series[preset["id"]]
        if ser["daily"] is None:
            out.append({"preset_id": preset["id"], "status": ser["status"], "daily": [],
                        "periods": []})
            continue
        daily_points = [
            {"date": d, "expected_usd_micros": ser["daily"]["expected"][k],
             "low_usd_micros": ser["daily"]["low"][k], "high_usd_micros": ser["daily"]["high"][k]}
            for k, d in enumerate(run.dates)]
        periods = []
        for i, desc in enumerate(run.periods):
            p = ser["periods"][i]
            dates = run.dates[desc.i_from:desc.i_end + 1]
            base_total = (baseline["periods"][i]["total_expected"]
                          if baseline is not None and baseline["daily"] is not None else None)
            period = forecast.compose_period(
                start=desc.start, end=desc.end, forecast_from=desc.forecast_from, dates=dates,
                cumulative_expected=p["cumulative_expected"],
                cumulative_quantiles=p["cumulative_quantiles"],
                total_expected=p["total_expected"], total_quantiles=p["total_quantiles"],
                actual_to_date=actual_to_date[i], budget=budget, baseline_total_expected=base_total,
                grid=run.grid, band_low=run.band_low, band_high=run.band_high)
            period["runway"] = forecast.period_runway(
                current=desc.current, budget=budget, start=desc.start, end=desc.end, dates=dates,
                cumulative_quantiles=p["cumulative_quantiles"], grid=run.grid,
                actual_to_date=actual_to_date[i],
                within_budget_probability=period["within_budget_probability"],
                daily_actuals=daily, lasts_below=fc.runway_lasts_below,
                runs_out_above=fc.runway_runs_out_above,
                min_probability=fc.runway_min_probability)
            periods.append(period)
        out.append({"preset_id": preset["id"], "status": ser["status"], "daily": daily_points,
                    "periods": periods})
    return out


def budget_forecast(s: DbSession, catalog: QueryCatalog, config: ApiConfig, scope_type: str,
                    scope_id: str) -> dict:
    """`GET /v1/forecast?scope_type=&scope_id=` (T6, plan §3.6/§3.8) -- the precomputed forecast
    of one scope with every preset, actuals from the same session fetch `/v1/economics` uses
    (its `budget_burn` path: `cost_effective_usd_micros` summed over every row, roots and
    subagents) bucketed by local date in the run's timezone, and the scope's budget read now (per calendar month: scaled from the cycle budget for a
    non-monthly cycle, see `_forecast_budget`).

    `forecast` is `None` -- never an error -- when no run is loaded, when the scope is not in it,
    and when the stored run cannot be read (logged as `forecast.run_unreadable`). Nothing about
    budgets is stored with the run, so a budget edit changes the next response."""
    if scope_type not in budgets_service.SCOPE_TYPES:
        raise BadRequestError(f"unknown scope_type {scope_type!r}; expected one of "
                              f"{budgets_service.SCOPE_TYPES}")
    empty = {"data": {"scope_type": scope_type, "scope_id": scope_id, "forecast": None},
             "mode": dict(_NO_FORECAST_MODE), "unavailable": None}
    try:
        run = forecast_store.load_run(s, catalog)
        if run is None or not any(sc["scope_type"] == scope_type and sc["scope_id"] == scope_id
                                  for sc in run.scopes):
            return empty
        series = forecast_store.load_scope_series(s, catalog, run, scope_type, scope_id)
    except forecast_store.ForecastUnreadable as exc:
        _log_unreadable(exc.run_id, exc.reason)
        return empty

    # Actual spend: 28 days before the first period .. data_through (plan §3.6). `load_run` has
    # already bounded and cross-checked these dates; date arithmetic that still fails here is a
    # damaged run, while database errors are not caught and propagate.
    first = run.periods[0].start - timedelta(days=forecast_store.ACTUAL_LOOKBACK_DAYS)
    last = run.data_through
    try:
        daily, rows = _scope_daily_actuals(s, catalog, run, scope_type, scope_id, first, last)
    except (OverflowError, ValueError) as exc:
        _log_unreadable(run.run_id, f"actuals window: {type(exc).__name__}: {exc}")
        return empty
    active = _active_cycle(s, catalog, config)
    budget = _forecast_budget(budgets_service.budgets_for_scopes(
        s, catalog, scope_type, [scope_id], active.label).get(scope_id), active, run.data_through)

    try:
        view = {
            "origin": run.origin, "data_through": run.data_through, "timezone": run.timezone,
            "band_low": run.band_low, "band_high": run.band_high, "budget_usd_micros": budget,
            "presets": run.presets,
            "actual_daily": [{"date": d, "usd_micros": daily.get(d, 0)} for d in _days(first, last)],
            "series": _forecast_series_view(run, series, daily, budget, config),
        }
    except Exception as exc:  # a stored number that passed the shape checks but cannot be used
        _log_unreadable(run.run_id, f"{type(exc).__name__}: {exc}")
        return empty
    return {"data": {"scope_type": scope_type, "scope_id": scope_id, "forecast": view},
            "mode": _session_modes(rows), "unavailable": None}


def _plan_rows(run: forecast_store.StoredRun, plan: dict, actual: dict[tuple[str, str], list[int]],
               budgets: dict[tuple[str, str], int | None], config: ApiConfig) -> list[dict]:
    confidence_pct = round(config.forecast.planning_confidence * 100, 9)
    grid = run.grid
    rows = []
    scopes = ([sc for sc in run.scopes if sc["scope_type"] == "team"]
              + [sc for sc in run.scopes if sc["scope_type"] == "repo"])
    for sc in scopes:
        key = (sc["scope_type"], sc["scope_id"])
        budget = budgets.get(key)
        by_preset = []
        for preset in run.presets:
            entry = plan[key][preset["id"]]
            periods = []
            for i, p in enumerate(entry["periods"]):
                a = actual[key][i]
                tq = p["total_quantiles"]

                def at(pct: float) -> int:
                    return a + int(round(forecast.quantile_value(grid, tq, pct)))

                plan_for = at(confidence_pct)
                periods.append({
                    "needed": {"expected_usd_micros": a + p["total_expected"],
                               "p50_usd_micros": at(50), "p80_usd_micros": at(80),
                               "p90_usd_micros": at(90)},
                    "plan_for_usd_micros": plan_for,
                    "gap_usd_micros": None if budget is None else plan_for - budget,
                    "tokens_by_model": p["tokens_by_model"],
                })
            by_preset.append({"preset_id": preset["id"], "periods": periods})
        rows.append({"scope_type": sc["scope_type"], "scope_id": sc["scope_id"],
                     "name": sc["name"], "budget_usd_micros": budget, "by_preset": by_preset})
    return rows


def budget_plan(s: DbSession, catalog: QueryCatalog, config: ApiConfig) -> dict:
    """`GET /v1/forecast/plan` (T6, plan §3.8) -- what every team and project of the run needs
    for each period and preset, in dollars (mean, p50, p80, p90 and the gap to its budget at
    `config.forecast.planning_confidence`) and in tokens per model. The current period's
    dollars include the actual spend to date (the scope's own session fetch, as
    `/v1/forecast`); its tokens cover the forecast days only. No run (or an unreadable one) is
    `data: null` + `unavailable` (envelope rule 3)."""
    unavailable = {"data": None, "mode": dict(_NO_FORECAST_MODE),
                   "unavailable": {"block": "forecast", "requires": "forecast"}}
    try:
        run = forecast_store.load_run(s, catalog)
        if run is None:
            return unavailable
        plan = forecast_store.load_plan_series(s, catalog, run)
    except forecast_store.ForecastUnreadable as exc:
        _log_unreadable(exc.run_id, exc.reason)
        return unavailable

    active = _active_cycle(s, catalog, config)
    scopes = [sc for sc in run.scopes if sc["scope_type"] in ("team", "repo")]
    budgets: dict[tuple[str, str], int | None] = {}
    for scope_type in ("team", "repo"):
        ids = [sc["scope_id"] for sc in scopes if sc["scope_type"] == scope_type]
        found = budgets_service.budgets_for_scopes(s, catalog, scope_type, ids, active.label)
        for sid in ids:
            budgets[(scope_type, sid)] = _forecast_budget(found.get(sid), active, run.data_through)

    # Actual spend to date per scope, only when some period is already under way.
    current_starts = [d.start for d in run.periods if d.current]
    actual: dict[tuple[str, str], list[int]] = {}
    sessions: dict[str, str] = {}
    for sc in scopes:
        key = (sc["scope_type"], sc["scope_id"])
        if current_starts:
            try:
                daily, rows = _scope_daily_actuals(s, catalog, run, sc["scope_type"],
                                                   sc["scope_id"], min(current_starts),
                                                   run.data_through)
            except (OverflowError, ValueError) as exc:
                _log_unreadable(run.run_id, f"actuals window: {type(exc).__name__}: {exc}")
                return unavailable
            for r in rows:
                if r.datasource:
                    sessions[r.session_id] = r.datasource
        else:
            daily = {}
        actual[key] = [_sum_days(daily, d.start, d.forecast_from - timedelta(days=1))
                       for d in run.periods]

    try:
        data = {
            "planning_confidence": config.forecast.planning_confidence,
            "periods": [{"start": d.start, "end": d.end, "forecast_from": d.forecast_from,
                         "current": d.current} for d in run.periods],
            "presets": run.presets,
            "rows": _plan_rows(run, plan, actual, budgets, config),
        }
    except Exception as exc:
        _log_unreadable(run.run_id, f"{type(exc).__name__}: {exc}")
        return unavailable
    counts: dict[str, int] = {}
    for mode in sessions.values():
        counts[mode] = counts.get(mode, 0) + 1
    return {"data": data, "mode": {"modes_present": sorted(counts), "sessions_by_mode": counts},
            "unavailable": None}


# ------------------------------------------------------------------------------ A7: myTips

#: X3 bucket -> the recommendation phrase (tone lexicon: "recoverable/could save", never
#: "waste"; AGENTS.md's binding conventions).
_TIP_TEXT = {
    "resume": "Picking sessions back up after a long pause is adding up — the cache expires, so "
              "wrapping up before a break, or starting fresh after it, could save you tokens.",
    "cache_bust": "Mid-session cache resets are adding up — keeping CLAUDE.md, MCP servers and "
                  "the model steady during a session lets the cache keep working.",
    "interrupted": "Interrupted replies are adding up — a clearer first prompt or a quick plan "
                   "can help the first answer land.",
}


def my_tips(s: DbSession, catalog: QueryCatalog, config: ApiConfig, user_id: str,
           frm: datetime, to: datetime) -> dict:
    """`GET /v1/users/{id}/tips` (A7) -- the one new piece MyUsage adds on top of the shipped
    single-user endpoints (ground rules section 2, unauthenticated posture unchanged: this
    route is as ungated as `/v1/users/{id}/overview` already is)."""
    rows = aggregate.fetch_scope_sessions(s, catalog, [user_id], frm, to, shape="session")
    modes_present = sorted({r.datasource for r in rows if r.datasource})
    sessions_by_mode: dict[str, int] = {}
    for r in rows:
        if r.datasource:
            sessions_by_mode[r.datasource] = sessions_by_mode.get(r.datasource, 0) + 1

    recoverable = aggregate.recoverable(rows)
    _cost_value, cost_sources = _cost(rows, config)
    habits = aggregate.habits_from_recoverable(recoverable)
    tips = [
        {
            "category": h["category"],
            "text": _TIP_TEXT.get(h["category"], f"{h['label']} is adding up this period."),
            "could_save": _money(h["recoverable_usd_micros"], cost_sources),
        }
        for h in habits
    ]
    return {
        "data": {"tips": tips},
        "mode": {"modes_present": modes_present, "sessions_by_mode": sessions_by_mode},
        "unavailable": None,
    }


def _team_usage(team_id: str, meta: dict, rows: list, member_ids: list[str], budget_amount,
                person_spend: dict[str, int], seat_starts: dict[str, datetime],
                frm: datetime, to: datetime, config: ApiConfig,
                active: budget_cycles.ActiveCycle) -> dict:
    """A2 per-team utilization, cost-based (plan §2): the team's spend over its per-period budget
    measured against the window; `seats_under_threshold` counts MEMBERS (idle ones included) against
    their own prorated seat allowances. Tokens ride along for information only."""
    cost_value, cost_sources = _cost(rows, config)
    window_budget = aggregate.window_budget_usd_micros(
        aggregate.budget_usd_micros(budget_amount), frm, to, active)
    pct = aggregate.usage_pct(cost_value, window_budget)
    seat_allowances = _seat_allowances(member_ids, _seat_monthly(budget_amount, meta["members"]),
                                       seat_starts, frm, to, active)
    return {
        "team_id": team_id,
        "name": meta["name"],
        "members": meta["members"],
        "spend": _money(cost_value, cost_sources),
        "tokens_used": sum(r.tokens_total or 0 for r in rows),
        "allowance": _budget_money(window_budget),
        "utilization_pct": pct,
        "status": aggregate.usage_status(pct, config.utilization),
        "seats_under_threshold": aggregate.seats_under_threshold(
            member_ids, person_spend, seat_allowances, seat_starts, to, config.utilization),
    }


def _person_usage(user_id: str, meta: dict, rows: list, seat_allowance_usd_micros: int | None,
                  seat_start: datetime | None, to: datetime, config: ApiConfig) -> dict:
    """G3: `_team_usage`'s shape one level down -- a person's spend vs their prorated seat
    allowance. A seat still inside its grace period reports `status: "new"` rather than
    "under" (plan D7): it is not a low-use signal yet."""
    cost_value, cost_sources = _cost(rows, config)
    pct = aggregate.usage_pct(cost_value, seat_allowance_usd_micros)
    grace = (seat_allowance_usd_micros is not None
             and aggregate.in_grace(seat_start, to, config.utilization.new_seat_grace_days))
    status = aggregate.seat_usage_status(pct, seat_start, to, config.utilization)
    return {
        "user_id": user_id,
        "name": meta["display_name"],
        "spend": _money(cost_value, cost_sources),
        "tokens_used": sum(r.tokens_total or 0 for r in rows),
        "allowance": _budget_money(seat_allowance_usd_micros),
        "utilization_pct": pct,
        "status": status,
        "in_grace": grace,
    }


def org_usage(s: DbSession, catalog: QueryCatalog, config: ApiConfig,
              frm: datetime, to: datetime) -> dict:
    """`GET /v1/org/usage` (A2). Cost/utilization framing throughout (cost-based seats plan) --
    no "limit exceeded" wording (iteration-plan D; the client owns any such copy, and this API
    never emits it)."""
    org_id = config.directory.default_org_id
    active = _active_cycle(s, catalog, config)
    user_ids = directory.users_in_scope(s, catalog, "org", org_id)
    rows = aggregate.fetch_scope_sessions(s, catalog, user_ids, frm, to, shape="session")
    pr_rows = aggregate.fetch_scope_merged_prs(s, catalog, user_ids, frm, to)

    modes_present = sorted({r.datasource for r in rows if r.datasource})
    sessions_by_mode: dict[str, int] = {}
    for r in rows:
        if r.datasource:
            sessions_by_mode[r.datasource] = sessions_by_mode.get(r.datasource, 0) + 1

    root_sessions = sum(1 for r in rows if aggregate.is_root(r))
    total_minutes = sum(
        d for r in rows if aggregate.is_root(r)
        and (d := aggregate.session_duration_minutes(r)) is not None
    )

    user_team = _user_team_map(s, catalog, user_ids)
    by_team = aggregate.group_rows_by_team(rows, user_team)
    # Every team that has members OR activity -- a fully idle team is exactly what this screen
    # should show as "under", and used to be absent (it had no rows to group).
    org_team_ids = [r[0] for r in s.execute(catalog.get("directory", "team_ids_for_org"),
                                            {"org_id": org_id})]
    all_meta = _teams_meta(s, catalog, sorted(set(org_team_ids) | by_team.keys()))
    team_ids = sorted(tid for tid, m in all_meta.items() if m["members"] or tid in by_team)
    meta = {tid: all_meta[tid] for tid in team_ids}
    budgets = budgets_service.budgets_for_scopes(s, catalog, "team", team_ids, active.label)
    members_by_team = {tid: directory.members_of(s, catalog, tid, include_subteams=False)
                       for tid in team_ids}
    seat_starts = aggregate.fetch_seat_starts(
        s, catalog, sorted({uid for ms in members_by_team.values() for uid in ms}))
    person_spend = aggregate.spend_by_user(rows, config)

    teams = [
        _team_usage(tid, meta[tid], by_team.get(tid, []), members_by_team[tid], budgets.get(tid),
                    person_spend, seat_starts, frm, to, config, active)
        for tid in team_ids
    ]
    teams.sort(key=lambda t: -(t["utilization_pct"] or 0))

    # G3 (mvp-gap-closure-plan): the highest-utilization *over-budget* team, expanded to its
    # members -- `teams` is already sorted by utilization_pct, so the first "over" team in that
    # order is the one worth surfacing. `None` (not the top team regardless of status) when
    # nothing is actually over: "no team to point at" is a different, honest answer from "the
    # least-over team by default."
    spotlight_team = next((t for t in teams if t["status"] == "over"), None)
    spotlight = None
    if spotlight_team:
        tid = spotlight_team["team_id"]
        member_ids = members_by_team[tid]
        seat_allowances = _seat_allowances(member_ids, _seat_monthly(budgets.get(tid), meta[tid]["members"]),
                                           seat_starts, frm, to, active)
        by_user = aggregate.group_rows_by_user(rows)
        users_meta = _users_meta(s, catalog, member_ids)
        people = [
            _person_usage(uid, users_meta.get(uid, {"display_name": uid}), by_user.get(uid, []),
                          seat_allowances.get(uid), seat_starts.get(uid), to, config)
            for uid in member_ids
        ]
        people.sort(key=lambda p: (p["utilization_pct"] is None, -(p["utilization_pct"] or 0)))
        spotlight = {"team_id": tid, "team_name": spotlight_team["name"], "people": people}

    data = {
        "header": {
            "users": len({r.user_id for r in rows if r.user_id}),
            "agents": len({r.agent for r in rows}),
            "hours": round(total_minutes / 60, 1),
            "sessions": root_sessions,
            "avg_session_minutes": aggregate.avg_session_minutes(rows),
            "avg_prs_per_session": (aggregate.merged_pr_count(pr_rows) / root_sessions)
                                  if root_sessions else None,
        },
        "session_types": aggregate.session_type_counts(rows),
        "sessions_no_output_pct": aggregate.sessions_with_no_output_pct(rows),
        "reasoning_share": aggregate.reasoning_share(rows),
        "teams": teams,
        "spotlight_team": spotlight,
    }
    return {
        "data": data,
        "mode": {"modes_present": modes_present, "sessions_by_mode": sessions_by_mode},
        "unavailable": None,
    }
