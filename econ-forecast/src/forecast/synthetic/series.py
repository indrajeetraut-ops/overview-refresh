"""Generate one sorted day at a time using independent feature random streams."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from fractions import Fraction
from typing import Iterator, Mapping
import uuid

from common.msging.log import get_logger
from forecast.contracts import CoverageRecord, DailyUsageRecord, TokenCounts
from .allocation import allocate_integer, vary_weights
from .config import SeriesConfig
from .errors import InputDataError
from .events import resolve_event
from .models import (DayBatch, ForcedZeroEvent, PersonalSpikeEvent, SpikeEvent,
                     SpikeModelTargetRecord, SpikeTargetsRecord, TeamRecord, UserProfileRecord, UserRecord)
from .randomness import rng_for

MAX_I64 = 2**63 - 1


@dataclass
class CurveLevels:
    """Per-offset curve-guided levels for the daily loop.

    An absent array means "flat 1.0" (unbound), so a default `CurveLevels()` is the
    identity: `baseline`/`agent` always return 1.0 regardless of team/agent/offset,
    matching today's fixed-baseline behavior exactly with no array and no series length
    needed. `runner.run_series` populates the arrays from bound Task-1 curve artifacts.
    """
    org_levels: list[float] | None = None
    team_levels: dict[str, list[float]] = field(default_factory=dict)
    agent_levels: dict[str, list[float]] = field(default_factory=dict)

    def baseline(self, team_name: str, offset: int) -> float:
        """Team level if that team is bound, else org level, else 1.0 (override semantics)."""
        levels = self.team_levels.get(team_name, self.org_levels)
        return levels[offset] if levels is not None else 1.0

    def agent(self, agent_name: str, offset: int) -> float:
        """Agent level if bound, else 1.0 (multiply-on-default semantics)."""
        levels = self.agent_levels.get(agent_name)
        return levels[offset] if levels is not None else 1.0


@dataclass
class _BaselineRow:
    org_id: str
    team_id: str
    team_name: str
    user_id: str
    project_id: str
    agent: str
    model: str
    processed_input: int
    cache_ratio: float


def _choice(weights: Mapping[str, float], rng) -> str:
    draw = rng.random()
    cumulative = 0.0
    positive = [key for key in sorted(weights) if weights[key] > 0]
    for key in positive:
        cumulative += weights[key]
        if draw < cumulative:
            return key
    return positive[-1]


def _half_up(value: Fraction) -> int:
    """Exact rounding after sampled floats, avoiding float overflow at integer boundaries."""
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _activity(config, team_name):
    defaults = config.defaults
    override = config.team_profiles[team_name].activity
    if override is not None:
        return (override.daily_zero_probability,
                override.weekend_reduction_probability,
                override.weekend_reduction_factor)
    return (defaults.daily_zero_probability,
            defaults.weekend_reduction_probability,
            defaults.weekend_reduction_factor)


def _matches_rule(row: _BaselineRow, rule) -> bool:
    teams = rule.targets.teams
    if teams and row.team_name not in teams:
        return False
    agents = rule.targets.agents
    models = {(target.agent, target.model) for target in rule.targets.models}
    all_technologies = not agents and not models
    return all_technologies or row.agent in agents or (row.agent, row.model) in models


def _rule_is_eligible(rule, day) -> bool:
    if day.weekday() >= 5:
        return False
    if rule.date_range is None:
        return True
    return rule.date_range.start_date <= day <= rule.date_range.end_date


def _apply_spike_rules(config, day, rows: list[_BaselineRow]) -> tuple[list[int], list[SpikeEvent]]:
    increments = [0] * len(rows)
    events = []
    for rule in config.events.spike_rules:
        if not _rule_is_eligible(rule, day):
            continue
        selected = rng_for(
            config.series.usage_seed, "spike_selection", rule.id, day.isoformat()
        ).random() < rule.probability
        if not selected:
            continue
        multiplier = rng_for(
            config.series.usage_seed, "spike_multiplier", rule.id, day.isoformat()
        ).uniform(rule.multiplier_min, rule.multiplier_max)
        indexes = [index for index, row in enumerate(rows) if _matches_rule(row, rule)]
        baseline = sum(rows[index].processed_input for index in indexes)
        if baseline <= 0:
            continue
        final = _half_up(Fraction(baseline) * Fraction(str(multiplier)))
        increment = final - baseline
        if increment <= 0:
            continue
        keys = {f"{index:020d}": rows[index].processed_input for index in indexes}
        allocated = allocate_integer(increment, keys)
        for key, count in allocated.items():
            increments[int(key)] += count
        events.append(SpikeEvent(
            date=day,
            rule_id=rule.id,
            multiplier=multiplier,
            targets=SpikeTargetsRecord(
                teams=list(rule.targets.teams),
                agents=list(rule.targets.agents),
                models=[SpikeModelTargetRecord(agent=target.agent, model=target.model)
                        for target in rule.targets.models],
            ),
            baseline_processed_input=baseline,
            incremental_processed_input=increment,
        ))
    return increments, events


def generate_days(config: SeriesConfig, users: list[UserRecord], teams: list[TeamRecord],
                  assignments: list[UserProfileRecord], *, log=None,
                  curve_levels: CurveLevels | None = None) -> Iterator[DayBatch]:
    log = log or get_logger(__name__)
    curve_levels = curve_levels or CurveLevels()
    user_by_id = {user.user_id: user for user in users}
    team_by_user = {uid: team for team in teams for uid in team.user_ids}
    profile_by_user = {assignment.user_id: assignment.profile for assignment in assignments}
    if len(profile_by_user) != len(assignments) or set(profile_by_user) != set(team_by_user):
        raise InputDataError("profile assignments must cover each rostered user exactly once")
    if any(a.team_id != team_by_user[a.user_id].team_id or a.profile not in config.profiles for a in assignments):
        raise InputDataError("profile assignment has unknown profile or incorrect Team")
    defaults = config.defaults
    agents = sorted(config.agents)
    org = teams[0].org_id
    project_ids = {project.name: str(uuid.uuid5(
        uuid.NAMESPACE_URL, f"forecast:project:{org}:{project.name.casefold()}"))
        for project in config.projects}
    for offset in range(config.series.days):
        day = config.series.start_date + timedelta(days=offset)
        usage, coverage, events, baseline_rows = [], [], [], []
        active = inactive = zero = 0
        for uid in sorted(team_by_user):
            user, team = user_by_id[uid], team_by_user[uid]
            def rng(domain, *keys):
                return rng_for(config.series.usage_seed, domain, uid, day.isoformat(), *keys)
            coverage.extend(CoverageRecord(date=day, org_id=user.org_id, user_id=uid,
                                           agent_type=agent, status="complete") for agent in agents)
            effect = resolve_event(config, user, team, day)
            if effect.forced_zero:
                inactive += 1
                events.append(ForcedZeroEvent(date=day, user_id=uid, team_id=team.team_id,
                                              event_id=effect.event_id))
                continue
            weekend = day.weekday() >= 5
            daily_zero, reduction_probability, reduction_factor = _activity(
                config, team.team_name
            )
            if rng("activity").random() < daily_zero:
                inactive += 1
                continue
            multiplier = (
                reduction_factor
                if weekend and rng("weekend_reduction").random() < reduction_probability
                else 1
            )
            active += 1
            project = project_ids[_choice(config.team_profiles[team.team_name].project_mix, rng("project"))]
            variation = defaults.daily_variation_pct / 100
            volume = rng("volume").uniform(1 - variation, 1 + variation)
            profile_name = profile_by_user[uid]
            profile = config.profiles[profile_name]
            total_input = Fraction(profile.daily_input_tokens)
            curve_baseline = curve_levels.baseline(team.team_name, offset)
            for factor in (volume, multiplier, curve_baseline):
                total_input *= Fraction(str(factor))
            count = max(1, _half_up(total_input))
            if profile.has_personal_spikes and not weekend:
                # One independent draw per user-day, keyed only by (usage_seed, domain, user,
                # date). Scales the whole day before the agent/model split, so every row of the
                # user moves together; spike_rules later act on the scaled baseline.
                if rng("profile_spike").random() < profile.spike_probability:
                    spike_multiplier = rng("profile_spike_multiplier").uniform(
                        profile.spike_multiplier_min, profile.spike_multiplier_max)
                    spiked = max(1, _half_up(total_input * Fraction(str(spike_multiplier))))
                    if spiked > count:
                        events.append(PersonalSpikeEvent(
                            date=day, user_id=uid, team_id=team.team_id, profile=profile_name,
                            multiplier=spike_multiplier, baseline_processed_input=count,
                            incremental_processed_input=spiked - count))
                        count = spiked
            if count > MAX_I64:
                raise InputDataError("generated token count exceeds signed 64-bit range")
            try:
                agent_weights = vary_weights(
                    {a: config.agents[a].usage_share * curve_levels.agent(a, offset) for a in agents},
                    defaults.mix_variation_pct, lambda a: rng("agent_mix", a))
            except ValueError as exc:
                raise InputDataError(
                    f"curve-adjusted agent weights are invalid on {day.isoformat()} "
                    f"(every bound agent curve may be at level 0 that day): {exc}") from exc
            agent_counts = allocate_integer(count, agent_weights)
            for agent in agents:
                if agent_counts[agent] == 0:
                    continue
                models = config.agents[agent].models
                weights = vary_weights(models, defaults.mix_variation_pct,
                                       lambda model: rng("model_mix", agent, model))
                model_counts = allocate_integer(agent_counts[agent], weights)
                for model in sorted(models):
                    total = model_counts[model]
                    if total == 0:
                        continue
                    lower = max(0, defaults.cache_hit_ratio - defaults.cache_hit_variation_pp / 100)
                    upper = min(1, defaults.cache_hit_ratio + defaults.cache_hit_variation_pp / 100)
                    cache_ratio = rng("cache", agent, model).uniform(lower, upper)
                    baseline_rows.append(_BaselineRow(
                        org_id=user.org_id, team_id=team.team_id, team_name=team.team_name,
                        user_id=uid, project_id=project, agent=agent, model=model,
                        processed_input=total, cache_ratio=cache_ratio,
                    ))
        increments, spike_events = _apply_spike_rules(config, day, baseline_rows)
        events.extend(spike_events)
        for index, row in enumerate(baseline_rows):
            total = row.processed_input + increments[index]
            if total > MAX_I64:
                raise InputDataError("generated token count exceeds signed 64-bit range")
            cached = _half_up(total * Fraction(str(row.cache_ratio)))
            output = _half_up(total * Fraction(str(defaults.output_ratio)))
            uncached = total - cached
            writes = _half_up(uncached * Fraction(str(defaults.cache_write_ratio)))
            write_1h = _half_up(writes * Fraction(str(defaults.cache_write_1h_share)))
            usage.append(DailyUsageRecord(
                date=day, org_id=row.org_id, team_id=row.team_id, user_id=row.user_id,
                project_id=row.project_id, agent_type=row.agent, model=row.model,
                billing_mode="api",
                tokens=TokenCounts(input=uncached - writes, cache_write_5m=writes - write_1h,
                                   cache_write_1h=write_1h, cache_read=cached, output=output),
            ))
        usage.sort(key=lambda row: (row.org_id, row.team_id, row.user_id, row.project_id,
                                    row.agent_type, row.model, row.billing_mode))
        coverage.sort(key=lambda row: (row.org_id, row.user_id, row.agent_type))
        events.sort(key=lambda row: (
            row.kind,
            row.user_id if isinstance(row, (ForcedZeroEvent, PersonalSpikeEvent)) else "",
            row.event_id if isinstance(row, ForcedZeroEvent)
            else "" if isinstance(row, PersonalSpikeEvent) else row.rule_id,
        ))
        log.info("synthetic.day_generated", date=day.isoformat(), active_users=active,
                 inactive_users=inactive, zero_volume_users=zero, usage_rows=len(usage),
                 coverage_rows=len(coverage), event_rows=len(events))
        yield DayBatch(date=day, usage=usage, coverage=coverage, events=events,
                       active_users=active, inactive_users=inactive, zero_volume_users=zero)
