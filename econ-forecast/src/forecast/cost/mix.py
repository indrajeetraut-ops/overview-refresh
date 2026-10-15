"""Mix and attribution (design §5): exponentially weighted token ratios, parent
blend, and parent-scope attribution share.

Reuses `PreparedDataset.{usage,coverage,scopes,models}` directly (not
`forecasting.history`, whose `categories` are a fixed 28-day plain sum for
allocation -- a different, known-divergent estimator, design §11/"known error").
Coverage filtering mirrors `forecasting.history.build_histories`: a scope's
coverage keys are restricted to the model's own agents before checking day
coverage, so "covered" means the same thing here as it does for the forecast.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
import math

from forecast.forecasting.dataset import PreparedDataset

from .errors import InputError
from .schemas import HALF_LIFE_DAYS, OWN_MIX_HALF_WEIGHT_DAYS, TOKEN_TYPES


@dataclass(frozen=True)
class MixResult:
    """One series' mix (§5): `shares` sorted by `agent_type`, each a mapping with
    `agent_type` plus the five `TOKEN_TYPES` keys, values summing to 1.0 (or the
    dict is empty when the series has no weighted usage anywhere in its blend).
    `own_weight` is λ; 1.0 for the Organization (§5: "the Organization uses its
    own"). `active_days` is undecayed (a plain count, not weighted). `window_start`/
    `window_end` are the covered days actually used (`None` if there were none)."""

    shares: tuple[dict, ...]
    active_days: int
    own_weight: float
    window_start: date | None
    window_end: date | None


def _weight(day: date, origin: date, half_life_days: int) -> float:
    """§5: $w_d = 2^{-(\\text{origin}-d)/h}$."""
    return 2.0 ** (-(origin - day).days / half_life_days)


def _series_days_and_totals(
    dataset: PreparedDataset,
    org_id: str,
    scope_type: str,
    scope_id: str,
    model: str,
    origin: date,
    window_start: date,
):
    """Covered days in `[window_start, origin]` for this (scope, model) -- coverage
    keys restricted to the model's own agents (history.py's rule) -- plus each
    covered day's token total and its (agent, token_type) breakdown, both computed
    only over usage on those covered days (§5: "covered days only")."""
    scope = dataset.scopes.get((org_id, scope_type, scope_id))
    if scope is None:
        raise InputError(f"unknown scope for mix: {scope_type} {scope_id!r}")
    agents = dataset.models.get(org_id, {}).get(model)
    if agents is None:
        raise InputError(f"unknown model for mix: {model!r}")
    keys = frozenset(k for k in scope.keys if k[1] in agents)

    days: list[date] = []
    if keys:
        day = window_start
        while day <= origin:
            if all(
                dataset.coverage.get((day, org_id, user, agent)) == "complete"
                for user, agent in keys
            ):
                days.append(day)
            day += timedelta(days=1)

    totals: dict[date, float] = defaultdict(float)
    by_cat: dict[tuple[date, str, str], float] = defaultdict(float)
    if days:
        window = frozenset(days)
        for (o, st, sid, m, day, agent, cat), value in dataset.usage.items():
            if (o, st, sid, m) != (org_id, scope_type, scope_id, model) or day not in window:
                continue
            totals[day] += value
            by_cat[(day, agent, cat)] += value
    return days, totals, by_cat


def _weighted_shares(totals, by_cat, days, origin: date, half_life_days: int):
    """Own mix (§5): ratio of weighted sums, never a mean of daily ratios. Returns
    `((agent, token_type) -> share, active_days, denominator)`; an empty share dict
    (denominator <= 0) means "no weighted usage" -- the caller decides the fallback
    (organization mix, or `scenario_mix_borrowed`, §6.2)."""
    denominator = math.fsum(_weight(d, origin, half_life_days) * totals.get(d, 0.0) for d in days)
    active_days = sum(1 for d in days if totals.get(d, 0.0) > 0)
    if denominator <= 0:
        return {}, active_days, denominator
    days_set = set(days)
    numerator: dict[tuple[str, str], float] = defaultdict(float)
    for (day, agent, cat), value in by_cat.items():
        if day in days_set:
            numerator[(agent, cat)] += _weight(day, origin, half_life_days) * value
    return {key: total / denominator for key, total in numerator.items()}, active_days, denominator


def _shares_to_rows(blended: dict[tuple[str, str], float]) -> tuple[dict, ...]:
    by_agent: dict[str, dict[str, float]] = defaultdict(
        lambda: {tt: 0.0 for tt in TOKEN_TYPES}
    )
    for (agent, cat), value in blended.items():
        if cat in TOKEN_TYPES:
            by_agent[agent][cat] = value
    return tuple(
        {"agent_type": agent, **values} for agent, values in sorted(by_agent.items())
    )


def compute_mix(
    dataset: PreparedDataset,
    org_id: str,
    scope_type: str,
    scope_id: str,
    model: str,
    origin: date,
    max_age_days: int,
    *,
    parent: tuple[str, str] | None = None,
    half_life_days: int = HALF_LIFE_DAYS,
    own_mix_half_weight_days: int = OWN_MIX_HALF_WEIGHT_DAYS,
) -> MixResult:
    """One series' mix (§5). `parent` is `(parent_scope_type, parent_scope_id)` for
    a Team/Project (its Organization); `None` for the Organization itself, which is
    never blended (§5: "the Organization uses its own")."""
    if max_age_days <= 0:
        raise InputError("max_age_days must be positive")
    window_start = origin - timedelta(days=max_age_days - 1)
    own_days, own_totals, own_by_cat = _series_days_and_totals(
        dataset, org_id, scope_type, scope_id, model, origin, window_start
    )
    own_shares, active_days, _ = _weighted_shares(
        own_totals, own_by_cat, own_days, origin, half_life_days
    )

    if scope_type == "organization" or parent is None:
        blended = own_shares
        own_weight = 1.0
    else:
        parent_type, parent_id = parent
        parent_days, parent_totals, parent_by_cat = _series_days_and_totals(
            dataset, org_id, parent_type, parent_id, model, origin, window_start
        )
        parent_shares, _, _ = _weighted_shares(
            parent_totals, parent_by_cat, parent_days, origin, half_life_days
        )
        own_weight = active_days / (active_days + own_mix_half_weight_days)
        keys = set(own_shares) | set(parent_shares)
        blended = {
            key: own_weight * own_shares.get(key, 0.0)
            + (1 - own_weight) * parent_shares.get(key, 0.0)
            for key in keys
        }

    return MixResult(
        shares=_shares_to_rows(blended),
        active_days=active_days,
        own_weight=own_weight,
        window_start=min(own_days) if own_days else None,
        window_end=max(own_days) if own_days else None,
    )


def teams_matching_project(dataset: PreparedDataset, org_id: str, project_id: str) -> list[str]:
    """Every team whose population is a superset of this project's (design's scope
    table: "one team per project") -- discovered from coverage-key overlap, since
    the wire format never declares the link explicitly. Zero matches means the
    project genuinely has no team in this dataset; more than one means the team is
    not uniquely determined (callers decide whether that's fatal)."""
    project = dataset.scopes.get((org_id, "project", project_id))
    if project is None:
        return []
    project_users = {u for u, _ in project.keys if u is not None}
    if not project_users:
        return []
    return [
        sid
        for (o, t, sid), scope in dataset.scopes.items()
        if o == org_id
        and t == "team"
        and project_users <= {u for u, _ in scope.keys if u is not None}
    ]


def team_of_project(dataset: PreparedDataset, org_id: str, project_id: str) -> str | None:
    """The one team matching this project, or `None` if that is not uniquely
    determined (zero or multiple matches) -- the non-raising form, for a reporting
    context that can simply omit the level (§8.2 `mix.json.attribution`: "omit the
    team key when undetermined"). Scenario propagation, where silently skipping the
    team level would be wrong, uses `required_team_of_project` instead."""
    matches = teams_matching_project(dataset, org_id, project_id)
    return matches[0] if len(matches) == 1 else None


def required_team_of_project(dataset: PreparedDataset, org_id: str, project_id: str) -> str:
    """`team_of_project`, but raises `InputError` naming the project when its team
    is not uniquely determined, for callers (scenario propagation) that need the
    team level and must never silently skip it."""
    matches = teams_matching_project(dataset, org_id, project_id)
    if len(matches) != 1:
        raise InputError(
            f"project {project_id!r} does not have a uniquely determined team "
            f"({len(matches)} candidate teams); cannot propagate the scenario change to it"
        )
    return matches[0]


def ancestors_of(
    dataset: PreparedDataset, org_id: str, scope_type: str, scope_id: str, *, strict: bool = False
) -> list[tuple[str, str]]:
    """`(scope_type, scope_id)` pairs strictly above the given scope (§6.3, §8.2
    `mix.json.attribution`): a team's parent is the Organization; a project's
    parents are its team and the Organization, each with its own attribution share;
    the Organization has none. `strict=True` (scenario propagation, where the
    project's team is genuinely needed) raises `InputError` naming the project when
    its team is not uniquely determined; `strict=False` (the default, mix.json
    reporting) omits the team level instead."""
    if scope_type == "organization":
        return []
    if scope_type == "team":
        return [("organization", org_id)]
    result = [("organization", org_id)]
    team_id = (
        required_team_of_project(dataset, org_id, scope_id)
        if strict
        else team_of_project(dataset, org_id, scope_id)
    )
    if team_id is not None:
        result.insert(0, ("team", team_id))
    return result


def weighted_total(
    dataset: PreparedDataset,
    org_id: str,
    scope_type: str,
    scope_id: str,
    model: str,
    origin: date,
    max_age_days: int,
    *,
    half_life_days: int = HALF_LIFE_DAYS,
) -> float:
    """$\\Sigma_d w_d x_d$ for one (scope, model) series (§5's own-mix denominator,
    exposed standalone for §6.2 agent_transfer's `to`-agent model split, which
    compares volume *across* models rather than shares within one)."""
    if max_age_days <= 0:
        raise InputError("max_age_days must be positive")
    window_start = origin - timedelta(days=max_age_days - 1)
    days, totals, _ = _series_days_and_totals(
        dataset, org_id, scope_type, scope_id, model, origin, window_start
    )
    return math.fsum(_weight(d, origin, half_life_days) * totals.get(d, 0.0) for d in days)


def attribution_share(
    dataset: PreparedDataset,
    org_id: str,
    child_scope_type: str,
    child_scope_id: str,
    parent_scope_type: str,
    parent_scope_id: str,
    model: str,
    origin: date,
    max_age_days: int,
    *,
    half_life_days: int = HALF_LIFE_DAYS,
) -> float:
    """Attribution share (§5): a child scope's fraction of a parent scope's tokens
    for one model, `0` if the parent had no weighted usage on the child's covered
    days. Both sums use the child's covered-day set (§5's day weight is a function
    of the day alone, so the two sums must agree on which days count)."""
    if max_age_days <= 0:
        raise InputError("max_age_days must be positive")
    window_start = origin - timedelta(days=max_age_days - 1)
    child_days, child_totals, _ = _series_days_and_totals(
        dataset, org_id, child_scope_type, child_scope_id, model, origin, window_start
    )
    _, parent_totals, _ = _series_days_and_totals(
        dataset, org_id, parent_scope_type, parent_scope_id, model, origin, window_start
    )
    numerator = math.fsum(
        _weight(d, origin, half_life_days) * child_totals.get(d, 0.0) for d in child_days
    )
    denominator = math.fsum(
        _weight(d, origin, half_life_days) * parent_totals.get(d, 0.0) for d in child_days
    )
    return numerator / denominator if denominator > 0 else 0.0
