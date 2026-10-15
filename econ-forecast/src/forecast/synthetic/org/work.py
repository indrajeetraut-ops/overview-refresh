"""Convert linked spend into work: PR-sized items, features (issue groupings), and a backlog
(plan §5 P5). `compute_work` is org-wide: it needs every root session (to link-flag them and
compute each user's first active date and calibrated `base`) plus their children (for inclusive
tokens `T(s)`).

**Implementation judgment calls** (the plan specifies these at the prose level; each is a single
coherent reading, documented here and in the progress log rather than left ambiguous in code):
- The `item_pick` RNG instance is created once per session (only when `in_flight` is nonempty)
  and consumed in sequence: first the new-item draw (only when `parallel_items` allows one),
  then -- only if a new item was NOT started -- the pick-among-in-flight draw. This avoids
  creating two independent `rng_for(seed, "item_pick", session_id)` instances that would replay
  the same first random value for two different decisions.
- Feature link method is decided before the sharing decision: `none` means no issue at all, so
  there is nothing to "share"; any other method decides sharing next, from the same `feature`
  RNG instance.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, time, timedelta
from typing import Mapping
from zoneinfo import ZoneInfo

from common.msging.log import get_logger
from ..allocation import allocate_integer
from ..randomness import rng_for
from . import ids
from .config import Module, Repo, ResolvedOrg, TeamEffective
from .sessions import SessionPlan, _total_processed

_WORK_DAY_START = time(8, 0)
_WORK_DAY_END = time(19, 0)


@dataclass
class WorkItem:
    item_id: str                       # pr_key = f"{repo}:{l1_user}:{seq}"
    l1_user: str
    repo: Repo
    seq: int
    size_loc: int
    change_type: str
    budget: float
    module: Module
    sessions: list[str] = field(default_factory=list)
    spent: float = 0.0
    opened_at: datetime | None = None
    finalized_at: datetime | None = None
    feature_id: str | None = None
    first_session_at: datetime | None = None   # first linked session's started_at (UTC)
    last_session_at: datetime | None = None    # most recent linked session's ended_at (UTC)
    opening_session_id: str | None = None      # the session whose spend crossed open_at_fraction (P6)

    @property
    def has_pr(self) -> bool:
        return self.opened_at is not None


@dataclass
class Feature:
    feature_id: str                    # f"{repo}:{l1_user}:feature:{seq}"
    l1_user: str
    repo: Repo
    team_name: str
    items: list[str] = field(default_factory=list)
    link_method: str = "none"          # graphql | branch | title | none
    issue_owner: str | None = None     # feature_id whose issue this one reuses, else None (own)
    start: datetime | None = None
    end: datetime | None = None


@dataclass
class BacklogIssue:
    repo: Repo
    week_index: int
    created_at: datetime
    author_l1_user: str
    change_type: str
    not_planned: bool
    closed_at: datetime | None = None


@dataclass
class WorkResult:
    items: dict[str, WorkItem]
    features: dict[str, Feature]
    backlog_issues: list[BacklogIssue]
    session_item: dict[str, str]       # session_id (root or child) -> item_id, linked only
    linked: dict[str, bool]            # session_id (root or child) -> link flag
    base: float
    first_active_date: dict[str, date_cls]


def _team_of(resolved: ResolvedOrg, l1_user: str) -> str:
    return next(t.team_name for t in resolved.teams if l1_user in t.user_ids)


def _link_flags(resolved: ResolvedOrg, roots: list[SessionPlan],
                children_by_parent: Mapping[str, list[SessionPlan]]) -> dict[str, bool]:
    seed = resolved.config.org.seed
    linked: dict[str, bool] = {}
    for root in roots:
        team = resolved.team_effective[_team_of(resolved, root.l1_user)]
        flag = rng_for(seed, "link", root.session_id).random() >= team.work.unlinked_share
        linked[root.session_id] = flag
        for child in children_by_parent.get(root.session_id, ()):
            linked[child.session_id] = flag
    return linked


def _inclusive_tokens(root: SessionPlan, children_by_parent: Mapping[str, list[SessionPlan]]) -> int:
    total = _total_processed(root.models) + sum(root.models[m]["output"] for m in root.models)
    for child in children_by_parent.get(root.session_id, ()):
        total += _total_processed(child.models) + sum(child.models[m]["output"] for m in child.models)
    return total


def _first_active_dates(roots: list[SessionPlan]) -> dict[str, date_cls]:
    out: dict[str, date_cls] = {}
    for root in roots:
        current = out.get(root.l1_user)
        if current is None or root.date < current:
            out[root.l1_user] = root.date
    return out


def _ramp(resolved: ResolvedOrg, l1_user: str, first_active: Mapping[str, date_cls], d: date_cls) -> float:
    onboarding = resolved.config.onboarding
    j = first_active.get(l1_user)
    if j is None or j <= resolved.series.series.start_date + timedelta(days=7):
        return 1.0
    elapsed_days = (d - j).days
    return 1 + (onboarding.initial_multiplier - 1) * max(0.0, 1 - elapsed_days / onboarding.ramp_days)


def _calibrate_base(resolved: ResolvedOrg, roots: list[SessionPlan], linked: Mapping[str, bool],
                    inclusive: Mapping[str, int], first_active: Mapping[str, date_cls],
                    log=None) -> float:
    series = resolved.series.series
    series_end = series.start_date + timedelta(days=series.days - 1)
    target = resolved.config.calibration.target_merged_prs_per_dev_week

    l_by_user: dict[str, int] = defaultdict(int)
    for root in roots:
        if linked.get(root.session_id):
            l_by_user[root.l1_user] += inclusive[root.session_id]

    dev_weeks = 0.0
    numerator = 0.0
    for l1_user, j in first_active.items():
        dev_weeks += (series_end - j).days / 7 + 1 / 7
        team = resolved.team_effective[_team_of(resolved, l1_user)]
        e_size = team.pr.size_median_loc * math.exp(team.pr.size_sigma ** 2 / 2)
        u_mult = _user_mult(resolved, l1_user, team.user_multiplier_sigma)
        l_u = l_by_user.get(l1_user, 0)
        denom = e_size * team.tokens_per_loc_multiplier * u_mult
        if denom > 0:
            numerator += l_u * (1 - team.review.abandon_rate) / denom

    base = numerator / (target * dev_weeks) if dev_weeks > 0 else 0.0
    (log or get_logger(__name__)).info("synthetic.org.calibrated", base=base,
                                       dev_weeks=dev_weeks, target=target, users=len(first_active))
    return base


def _user_mult(resolved: ResolvedOrg, l1_user: str, sigma: float) -> float:
    return rng_for(resolved.config.org.seed, "user_mult", l1_user).lognormvariate(0, sigma)


def _new_item(resolved: ResolvedOrg, l1_user: str, repo: Repo, seq: int, team: TeamEffective,
             base: float, u_mult: float, ramp: float, when: date_cls) -> WorkItem:
    r = rng_for(resolved.config.org.seed, "item", l1_user, repo.name, str(seq))
    size = round(r.lognormvariate(math.log(team.pr.size_median_loc), team.pr.size_sigma))
    size = max(team.pr.size_min_loc, min(size, team.pr.size_max_loc))
    change_type = ids.choice(dict(team.work.change_mix), r)
    budget = size * base * team.tokens_per_loc_multiplier * u_mult * ramp
    module = ids.choice({m.path: m.weight for m in repo.modules}, r)
    module_cfg = next(m for m in repo.modules if m.path == module)
    item_id = f"{repo.name}:{l1_user}:{seq}"
    return WorkItem(item_id=item_id, l1_user=l1_user, repo=repo, seq=seq, size_loc=size,
                    change_type=change_type, budget=budget, module=module_cfg)


def _work_streams(resolved: ResolvedOrg, roots_by_user_repo: Mapping[tuple, list[SessionPlan]],
                  linked: Mapping[str, bool], inclusive: Mapping[str, int], base: float,
                  first_active: Mapping[str, date_cls]) -> tuple[dict[str, WorkItem], dict[str, str]]:
    seed = resolved.config.org.seed
    items: dict[str, WorkItem] = {}
    session_item: dict[str, str] = {}

    for (l1_user, repo_name), sessions in roots_by_user_repo.items():
        team_name = _team_of(resolved, l1_user)
        team = resolved.team_effective[team_name]
        repo = sessions[0].repo
        u_mult = _user_mult(resolved, l1_user, team.user_multiplier_sigma)
        linked_sessions = sorted((s for s in sessions if linked.get(s.session_id)),
                                 key=lambda s: s.started_at)
        in_flight: list[WorkItem] = []
        seq = 0
        for s in linked_sessions:
            started_new = False
            if not in_flight:
                item = _new_item(resolved, l1_user, repo, seq, team, base, u_mult,
                                 _ramp(resolved, l1_user, first_active, s.date), s.date)
                items[item.item_id] = item
                in_flight.append(item)
                seq += 1
                started_new = True
            else:
                r = rng_for(seed, "item_pick", s.session_id)
                if len(in_flight) < team.work.parallel_items and r.random() < team.work.new_item_prob:
                    item = _new_item(resolved, l1_user, repo, seq, team, base, u_mult,
                                     _ramp(resolved, l1_user, first_active, s.date), s.date)
                    items[item.item_id] = item
                    in_flight.append(item)
                    seq += 1
                    started_new = True
                elif len(in_flight) > 1:
                    item = in_flight[0] if r.random() < 0.7 else r.choice(in_flight[1:])
                else:
                    item = in_flight[0]

            item.sessions.append(s.session_id)
            item.spent += inclusive[s.session_id]
            session_item[s.session_id] = item.item_id
            if item.first_session_at is None:
                item.first_session_at = s.started_at
            item.last_session_at = s.ended_at

            if item.opened_at is None and item.spent >= team.pr.open_at_fraction * item.budget:
                open_r = rng_for(seed, "item", l1_user, repo.name, str(item.seq), "open")
                item.opened_at = s.ended_at + timedelta(minutes=open_r.uniform(5, 60))
                item.opening_session_id = s.session_id

            if item.spent >= item.budget:
                item.finalized_at = s.ended_at
                if item in in_flight:
                    in_flight.remove(item)

    return items, session_item


def _features(resolved: ResolvedOrg, items: Mapping[str, WorkItem]) -> dict[str, Feature]:
    seed = resolved.config.org.seed
    features: dict[str, Feature] = {}
    by_user_repo: dict[tuple, list[WorkItem]] = defaultdict(list)
    for item in items.values():
        by_user_repo[(item.l1_user, item.repo.name)].append(item)

    for (l1_user, repo_name), user_items in by_user_repo.items():
        team_name = _team_of(resolved, l1_user)
        team = resolved.team_effective[team_name]
        ordered = sorted(user_items, key=lambda it: it.seq)
        fseq = 0
        i = 0
        while i < len(ordered):
            r = rng_for(seed, "feature", l1_user, repo_name, str(fseq))
            size_key = ids.choice(dict(team.work.feature_size), r)
            size = int(size_key)
            group = ordered[i:i + size]
            i += size
            feature_id = f"{repo_name}:{l1_user}:feature:{fseq}"
            starts = [it.first_session_at for it in group if it.first_session_at]
            ends = [it.last_session_at for it in group if it.last_session_at]
            feature = Feature(feature_id=feature_id, l1_user=l1_user, repo=group[0].repo,
                              team_name=team_name, items=[it.item_id for it in group],
                              start=min(starts) if starts else None,
                              end=max(ends) if ends else None)
            feature.link_method = ids.choice(dict(team.work.issue_link_mix), r)
            if feature.link_method != "none":
                if r.random() < team.work.shared_feature_prob:
                    candidate = _find_share_candidate(features, feature, team_name)
                    if candidate is not None:
                        feature.issue_owner = candidate.feature_id
            for it in group:
                it.feature_id = feature_id
            features[feature_id] = feature
            fseq += 1
    return features


def _find_share_candidate(features: Mapping[str, Feature], feature: Feature, team_name: str):
    if feature.start is None or feature.end is None:
        return None
    candidates = [
        f for f in features.values()
        if f.team_name == team_name and f.repo.name == feature.repo.name and f.l1_user != feature.l1_user
        and f.start is not None and f.end is not None
        and f.start <= feature.end and feature.start <= f.end
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda f: (f.start, f.feature_id))


def _authoring_teams(resolved: ResolvedOrg, project: str) -> list[str]:
    """Teams whose `project_mix` includes this repo's series project -- a backlog issue's
    author pool. Membership is a series-config concern, not a per-team org.yaml override, so
    this reads the series config directly rather than `team_effective`."""
    return sorted(name for name, profile in resolved.series.team_profiles.items()
                 if project in profile.project_mix)


def _backlog_issues(resolved: ResolvedOrg, *, log=None) -> list[BacklogIssue]:
    """Backlog cadence (`backlog_issues_per_repo_week`, `not_planned_share`) is an org-wide,
    not per-team, property of a repo: a repo can be in more than one team's `project_mix` (the
    example config's `payments-api` is worked by both `platform` and `payments`), and the plan
    does not specify which team's override would govern in that case. Using the UNMERGED
    `defaults.work` block sidesteps picking an arbitrary owning team."""
    seed = resolved.config.org.seed
    series = resolved.series.series
    tz = ZoneInfo(series.timezone)
    defaults = resolved.config.defaults.work
    series_end_local = datetime.combine(
        series.start_date + timedelta(days=series.days - 1), time(23, 59), tzinfo=tz)

    out: list[BacklogIssue] = []
    weeks = max(1, math.ceil(series.days / 7))
    for repo in resolved.config.repos:
        authors = [uid for name in _authoring_teams(resolved, repo.project)
                  for uid in resolved.teams_by_name[name].user_ids]
        if not authors:
            continue
        authors = sorted(authors)
        for week in range(weeks):
            r = rng_for(seed, "backlog", repo.name, str(week))
            count = _poisson(defaults.backlog_issues_per_repo_week, r)
            week_start = series.start_date + timedelta(days=week * 7)
            for _ in range(count):
                day_offset = r.randint(0, min(6, series.days - 1 - week * 7))
                hour = r.uniform(_WORK_DAY_START.hour, _WORK_DAY_END.hour)
                created = datetime.combine(week_start + timedelta(days=day_offset), time(0, 0),
                                          tzinfo=tz) + timedelta(hours=hour)
                author = r.choice(authors)
                not_planned = r.random() < defaults.not_planned_share
                closed_at = None
                if not_planned:
                    candidate = created + timedelta(days=r.uniform(3, 30))
                    if candidate <= series_end_local:
                        closed_at = candidate
                    else:
                        not_planned = False
                out.append(BacklogIssue(repo=repo, week_index=week, created_at=created,
                                        author_l1_user=author, change_type="chore",
                                        not_planned=not_planned, closed_at=closed_at))
    (log or get_logger(__name__)).info("synthetic.org.backlog", count=len(out))
    return out


def _poisson(mean: float, r) -> int:
    """Knuth's algorithm: count of exponential inter-arrivals within 1.0 (plan step 8's own
    phrasing), using `r` (the `backlog` stream) for every draw -- no extra RNG import."""
    if mean <= 0:
        return 0
    limit = math.exp(-mean)
    count = 0
    product = 1.0
    while True:
        product *= r.random()
        if product <= limit:
            return count
        count += 1


def compute_work(resolved: ResolvedOrg, sessions: list[SessionPlan], *, log=None) -> WorkResult:
    roots = [s for s in sessions if s.parent_session_id is None]
    children_by_parent: dict[str, list[SessionPlan]] = defaultdict(list)
    for s in sessions:
        if s.parent_session_id is not None:
            children_by_parent[s.parent_session_id].append(s)

    linked = _link_flags(resolved, roots, children_by_parent)
    inclusive = {root.session_id: _inclusive_tokens(root, children_by_parent) for root in roots}
    first_active = _first_active_dates(roots)
    base = _calibrate_base(resolved, roots, linked, inclusive, first_active, log=log)

    roots_by_user_repo: dict[tuple, list[SessionPlan]] = defaultdict(list)
    for root in roots:
        roots_by_user_repo[(root.l1_user, root.repo.name)].append(root)

    items, session_item = _work_streams(resolved, roots_by_user_repo, linked, inclusive, base,
                                        first_active)
    features = _features(resolved, items)
    backlog = _backlog_issues(resolved, log=log)

    return WorkResult(items=items, features=features, backlog_issues=backlog,
                      session_item=session_item, linked=linked, base=base,
                      first_active_date=first_active)
