"""Split each `usage.jsonl` group into sessions, spawn subagents, and lay out a user's day
(plan §5 P4). This is where I1 (rollup exactness) is made true: every token category of every
usage row is conserved exactly across the sessions (roots + children) it produces.

In-memory, not streamed: `usage.jsonl` for the example org (§9) is expected in the low tens of
thousands of rows, which fits comfortably; streaming day-by-day like Layer 1 would need a
different grouping strategy (usage rows for one user-day arrive already grouped by date, but
subagent carve-out and day-layout both need every root session for a user-day at once anyway).
# TODO(synthetic-org): revisit if a much larger example config makes this a memory concern.
"""
from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, time, timedelta, timezone
from typing import Mapping
from zoneinfo import ZoneInfo

from forecast.contracts import DailyUsageRecord
from ..allocation import allocate_integer
from ..errors import InputDataError
from ..randomness import rng_for
from . import ids
from .config import Repo, ResolvedOrg

#: 25k processed (input + cache writes + cache_read) tokens per minute of wall-clock session time.
PROCESSED_TOKENS_PER_MIN = 25_000
_CATEGORIES = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
_PROCESSED_CATEGORIES = ("input", "cache_write_5m", "cache_write_1h", "cache_read")


def _zero_amounts() -> dict[str, int]:
    return {cat: 0 for cat in _CATEGORIES}
_LUNCH_S = 3600
_LUNCH_AFTER_LOCAL = time(12, 30)
_DAY_END_LOCAL = time(23, 59, 0)


@dataclass
class SessionPlan:
    session_id: str
    parent_session_id: str | None
    l1_user: str
    user_id: str                       # mainline id: "github:<login>" (D5)
    date: date_cls
    project: str                       # Layer 1 project name
    repo: Repo                         # org.yaml repo config for that project
    agent: str
    models: dict[str, dict[str, int]] = field(default_factory=dict)  # model -> five token types
    started_at: datetime | None = None
    ended_at: datetime | None = None
    planned_duration_s: float = 0.0
    children: list[str] = field(default_factory=list)
    k: int | None = None               # session-order index within its usage group (roots only)


def _skey(k: int) -> str:
    return f"s{k:02d}"


def _project_name_by_id(resolved: ResolvedOrg) -> dict[str, str]:
    org = resolved.org_id
    return {
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"forecast:project:{org}:{project.name.casefold()}")): project.name
        for project in resolved.series.projects
    }


def _build_groups(resolved: ResolvedOrg, usage: list[DailyUsageRecord]):
    """`(date, l1_user, project, agent) -> {model: {category: int}}`, summed from usage rows."""
    project_by_id = _project_name_by_id(resolved)
    groups: dict[tuple, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(
        _zero_amounts))
    for row in usage:
        project = project_by_id.get(row.project_id)
        if project is None:
            raise InputDataError(f"usage row references an unknown project id {row.project_id!r}")
        key = (row.date, row.user_id, project, row.agent_type)
        bucket = groups[key][row.model]
        for cat in _CATEGORIES:
            bucket[cat] += getattr(row.tokens, cat) or 0
    return groups


def _processed(amounts: Mapping[str, int]) -> int:
    return sum(amounts[cat] for cat in _PROCESSED_CATEGORIES)


def _total_processed(models: Mapping[str, Mapping[str, int]]) -> int:
    return sum(_processed(cats) for cats in models.values())


def _split_group(resolved: ResolvedOrg, date_: date_cls, l1_user: str, project: str, agent: str,
                 totals: Mapping[str, Mapping[str, int]]) -> list[SessionPlan]:
    seed = resolved.config.org.seed
    date_iso = date_.isoformat()
    user = resolved.users_by_id[l1_user]
    repo = resolved.project_to_repo[project]
    team_name = next(t.team_name for t in resolved.teams if l1_user in t.user_ids)
    team_cfg = resolved.team_effective[team_name].sessions

    w_total = sum(_processed(cats) for cats in totals.values())
    output_total = sum(cats["output"] for cats in totals.values())
    if w_total == 0 and output_total == 0:
        raise InputDataError(
            f"usage group ({date_iso}, {l1_user}, {project}, {agent}) has zero total tokens; "
            "Layer 1 should never emit a usage row for an inactive/zero-volume user-day")

    n_raw = (w_total / team_cfg.mean_input_tokens) * rng_for(
        seed, "sess_count", l1_user, date_iso, project, agent).uniform(0.6, 1.4)
    n = max(1, min(round(n_raw), team_cfg.max_per_day, max(1, w_total // team_cfg.min_input_tokens)))

    weight_by_key = {_skey(k): rng_for(seed, "sess_weight", l1_user, date_iso, project, agent,
                                       _skey(k)).lognormvariate(0, 0.6) for k in range(n)}
    model_names = sorted(m for m, cats in totals.items() if any(cats.values()))
    n_models = len(model_names)
    dominant_by_key: dict[str, str] = {}
    for k in range(n):
        skey = _skey(k)
        model_weights = {m: _processed(totals[m]) + totals[m]["output"]
                         for m in model_names}
        dominant_by_key[skey] = ids.choice(model_weights, rng_for(
            seed, "dominant", l1_user, date_iso, project, agent, skey))

    dominant_share = team_cfg.dominant_model_share
    session_keys = set(weight_by_key)

    def build(keys: set[str]) -> dict[str, dict[str, dict[str, int]]]:
        per_session: dict[str, dict[str, dict[str, int]]] = {k: {} for k in keys}
        for m in model_names:
            weights = {}
            for k in keys:
                mult = (dominant_share if dominant_by_key[k] == m
                       else (1 - dominant_share) / max(1, n_models - 1))
                weights[k] = weight_by_key[k] * mult
            for cat in _CATEGORIES:
                total = totals[m][cat]
                if not total:
                    continue
                allocation = allocate_integer(total, weights)
                for k, amount in allocation.items():
                    if amount:
                        per_session[k].setdefault(m, _zero_amounts())[cat] = amount
        return per_session

    while True:
        per_session = build(session_keys)
        empties = {k for k in session_keys if not per_session[k]}
        empties.discard(_skey(0))  # session 0 is the merge sink; it always survives
        if not empties or len(session_keys) <= 1:
            break
        session_keys -= empties

    plans: list[SessionPlan] = []
    for k in sorted(session_keys):
        index = int(k[1:])
        session_id = ids.session_id(l1_user, date_iso, project, agent, k)
        plans.append(SessionPlan(
            session_id=session_id, parent_session_id=None, l1_user=l1_user,
            user_id=f"github:{user.github_login}", date=date_, project=project, repo=repo,
            agent=agent, models=per_session[k], k=index))
    return plans


def _spawn_subagents(resolved: ResolvedOrg, root: SessionPlan) -> list[tuple[SessionPlan, float]]:
    """Returns `[(child, offset_frac), ...]`: `offset_frac` is `r.uniform(0.1, 0.7)`, drawn from
    the SAME `subagent` stream right after that child's carve-out share (plan step 8's timing
    draw), so it is applied later (once the parent's timing is known from day layout) without
    re-entering or replaying the RNG."""
    team_name = next(t.team_name for t in resolved.teams if root.l1_user in t.user_ids)
    cfg = resolved.team_effective[team_name].sessions
    r = rng_for(resolved.config.org.seed, "subagent", root.session_id)

    pre_spawn = {m: dict(cats) for m, cats in root.models.items()}
    processed_before = _total_processed(pre_spawn)
    if not (r.random() < cfg.subagent_prob and processed_before >= 2 * cfg.min_input_tokens):
        return []

    j = r.randint(1, cfg.max_subagents)
    current = {m: dict(cats) for m, cats in root.models.items()}
    processed_current = processed_before
    children: list[tuple[SessionPlan, float]] = []
    for i in range(1, j + 1):
        f_i = r.uniform(cfg.subagent_share.min, cfg.subagent_share.max)
        offset_frac = r.uniform(0.1, 0.7)
        proposed = {m: {c: math.floor(cats[c] * f_i) for c in _CATEGORIES}
                   for m, cats in pre_spawn.items()}
        subtract = _total_processed(proposed)
        if processed_current - subtract < cfg.min_input_tokens:
            break
        for m, cats in proposed.items():
            for c, amount in cats.items():
                current[m][c] -= amount
        processed_current -= subtract
        child_models = {m: cats for m, cats in proposed.items() if any(cats.values())}
        if not child_models:
            continue
        child_id = ids.child_session_id(root.session_id, i)
        child = SessionPlan(
            session_id=child_id, parent_session_id=root.session_id, l1_user=root.l1_user,
            user_id=root.user_id, date=root.date, project=root.project, repo=root.repo,
            agent=root.agent, models=child_models)
        children.append((child, offset_frac))
        root.children.append(child_id)
    root.models = current
    return children


def _layout_day(resolved: ResolvedOrg, l1_user: str, date_: date_cls, roots: list[SessionPlan]) -> None:
    seed = resolved.config.org.seed
    date_iso = date_.isoformat()
    team_name = next(t.team_name for t in resolved.teams if l1_user in t.user_ids)
    cfg = resolved.team_effective[team_name].sessions
    tzinfo = ZoneInfo(resolved.series.series.timezone)

    window_rng = rng_for(seed, "window", l1_user)
    start_h = window_rng.uniform(cfg.work_window_start_hours.min, cfg.work_window_start_hours.max)
    len_h = window_rng.uniform(cfg.work_window_length_hours.min, cfg.work_window_length_hours.max)

    ordered = sorted(roots, key=lambda p: (p.project, p.agent, p.k))
    layout_rng = rng_for(seed, "layout", l1_user, date_iso)
    layout_rng.shuffle(ordered)
    n = len(ordered)

    durations = []
    for plan in ordered:
        processed = _total_processed(plan.models)
        duration = max(300.0, min(processed / PROCESSED_TOKENS_PER_MIN * 60, 6 * 3600))
        durations.append(duration)

    window_s = len_h * 3600
    budget = window_s - _LUNCH_S
    total_duration = sum(durations)
    if total_duration > budget:
        scale = budget / total_duration if total_duration else 1.0
        durations = [max(120.0, d * scale) for d in durations]

    remaining_idle = max(0.0, budget - sum(durations))
    gap_weights = [layout_rng.random() for _ in range(n + 1)]
    weight_sum = sum(gap_weights) or 1.0
    gaps = [w / weight_sum * remaining_idle for w in gap_weights]

    def place(start_dt: datetime, gaps_: list[float]) -> list[tuple[datetime, datetime]]:
        cursor = start_dt
        lunch_added = False
        times = []
        for i, plan in enumerate(ordered):
            gap_s = gaps_[i]
            if not lunch_added and cursor.time() >= _LUNCH_AFTER_LOCAL:
                gap_s += _LUNCH_S
                lunch_added = True
            cursor = cursor + timedelta(seconds=gap_s)
            started = cursor
            ended = started + timedelta(seconds=durations[i])
            times.append((started, ended))
            cursor = ended
        # If no gap boundary ever crossed 12:30 (a very front-loaded or very short day), the
        # lunch minute simply isn't inserted -- I4 doesn't require every day to contain one.
        return times

    start_dt = datetime.combine(date_, time(0, 0), tzinfo=tzinfo) + timedelta(hours=start_h)
    times = place(start_dt, gaps)

    day_end = datetime.combine(date_, _DAY_END_LOCAL, tzinfo=tzinfo)
    if times and times[-1][1] > day_end:
        overflow = times[-1][1] - day_end
        start_dt = start_dt - overflow
        times = place(start_dt, gaps)

    if times and times[-1][1] > day_end:
        # Still overflowing (only possible with an extreme config): compress every gap to 0 and
        # retry once more before giving up.
        start_dt = datetime.combine(date_, time(0, 0), tzinfo=tzinfo) + timedelta(hours=start_h)
        times = place(start_dt, [0.0] * len(gaps))
        if times and times[-1][1] > day_end:
            raise InputDataError(
                f"cannot fit {l1_user}'s {date_iso} sessions (total duration "
                f"{sum(durations):.0f}s) within the local day even with zero gaps")

    for plan, (started, ended) in zip(ordered, times):
        plan.started_at = started.astimezone(timezone.utc)
        plan.ended_at = ended.astimezone(timezone.utc)
        plan.planned_duration_s = (ended - started).total_seconds()


def _layout_children(root: SessionPlan, children: list[tuple[SessionPlan, float]]) -> None:
    parent_duration = root.planned_duration_s
    for child, offset_frac in children:
        started = root.started_at + timedelta(seconds=offset_frac * parent_duration)
        processed = _total_processed(child.models)
        duration = max(30.0, min(processed / PROCESSED_TOKENS_PER_MIN * 60,
                                 (root.ended_at - started).total_seconds()))
        child.started_at = started
        child.ended_at = started + timedelta(seconds=duration)
        child.planned_duration_s = duration


def plan_sessions(resolved: ResolvedOrg, usage: list[DailyUsageRecord]) -> list[SessionPlan]:
    groups = _build_groups(resolved, usage)
    roots_by_user_date: dict[tuple[str, date_cls], list[SessionPlan]] = defaultdict(list)
    children_by_root: dict[str, list[tuple[SessionPlan, float]]] = {}
    plans_by_id: dict[str, SessionPlan] = {}

    for (date_, l1_user, project, agent), totals in groups.items():
        roots = _split_group(resolved, date_, l1_user, project, agent, totals)
        for root in roots:
            children = _spawn_subagents(resolved, root)
            children_by_root[root.session_id] = children
            for child, _offset in children:
                plans_by_id[child.session_id] = child
            plans_by_id[root.session_id] = root
        roots_by_user_date[(l1_user, date_)].extend(roots)

    for (l1_user, date_), roots in roots_by_user_date.items():
        _layout_day(resolved, l1_user, date_, roots)

    for root_id, children in children_by_root.items():
        _layout_children(plans_by_id[root_id], children)

    return list(plans_by_id.values())
