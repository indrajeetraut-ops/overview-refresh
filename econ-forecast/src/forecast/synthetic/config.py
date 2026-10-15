"""Strict, offline YAML configuration and cross-dataset validation."""
from __future__ import annotations

import math
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Literal, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from faker.config import AVAILABLE_LOCALES
from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field, StrictInt,
                      ValidationError, field_validator, model_serializer,
                      model_validator)

from forecast.curve.artifacts import load_curve_artifact, resolve_curve_ref
from forecast.curve.errors import CurveError

from .errors import ConfigError, InputDataError


class UniqueLoader(yaml.SafeLoader):
    """Safe YAML without duplicate keys (including aliases/merge keys)."""


def _mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ConfigError("YAML mapping keys must be scalar") from exc
        if duplicate:
            raise ConfigError("duplicate YAML mapping key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def finite(value):
    """Reject booleans, coerced strings, infinity, and NaN before Pydantic conversion."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a finite number, not a boolean or string")
    try:
        valid = math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("must be a finite number")
    return value


def ident(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("ID must be a string or integer")
    value = str(value).strip()
    if not value:
        raise ValueError("ID cannot be empty")
    return value


def name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("name must be a nonempty string")
    return value.strip()


def calendar_date(value):
    if type(value) is date:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("date must be an ISO calendar date (YYYY-MM-DD)")


def shares(values, label, *, ids=False):
    """Normalize a validated mixture in sorted order, detecting normalized-key collisions."""
    if not isinstance(values, dict) or not values:
        raise ValueError(f"{label} must be a nonempty mapping")
    normalized = {}
    for key, value in values.items():
        key = ident(key) if ids else name(key)
        if key in normalized:
            raise ValueError(f"duplicate normalized key in {label}")
        value = finite(value)
        if value < 0:
            raise ValueError(f"{label} shares must be nonnegative")
        normalized[key] = float(value)
    total = math.fsum(normalized.values())
    if not math.isclose(total, 1.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError(f"{label} shares must sum to one")
    return {key: normalized[key] / total for key in sorted(normalized)}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


PositiveInt = Annotated[StrictInt, Field(gt=0)]
Seed = Annotated[StrictInt, Field(ge=0)]
Number = Annotated[float, BeforeValidator(finite)]
Ratio = Annotated[Number, Field(ge=0, le=1)]
Pct = Annotated[Number, Field(ge=0, lt=100)]
Name = Annotated[str, BeforeValidator(name)]
Identifier = Annotated[str, BeforeValidator(ident)]
CalendarDate = Annotated[date, BeforeValidator(calendar_date)]


class Versioned(Strict):
    schema_version: Literal[1]

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be integer 1")
        return value


class UserOptions(Strict):
    count: PositiveInt
    org_domain: str
    locale: str
    seed: Seed
    identity_style: Literal["independent", "consistent"] = "independent"

    @field_validator("org_domain")
    @classmethod
    def domain(cls, value):
        value = value.strip().lower()
        labels = value.split(".")
        if len(value) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
            raise ValueError("invalid organization domain")
        return value

    @field_validator("locale")
    @classmethod
    def known_locale(cls, value):
        if value not in AVAILABLE_LOCALES:
            raise ValueError("unsupported Faker locale")
        return value


class UsersConfig(Versioned):
    users: UserOptions


class TeamOptions(Strict):
    team_name: Name
    count: PositiveInt


class RosterOptions(Strict):
    users_file: Path
    seed: Seed
    teams: list[TeamOptions] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self):
        names = [team.team_name.casefold() for team in self.teams]
        if len(names) != len(set(names)):
            raise ValueError("Team names must be unique case-insensitively")
        return self


class RosterConfig(Versioned):
    roster: RosterOptions


class SeriesOptions(Strict):
    users_file: Path
    roster_file: Path
    start_date: CalendarDate
    days: PositiveInt
    timezone: str
    profile_seed: Seed
    usage_seed: Seed

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown timezone") from None
        return value

    @model_validator(mode="after")
    def supported_range(self):
        try:
            self.start_date + timedelta(days=self.days - 1)
        except OverflowError:
            raise ValueError("series date range exceeds the supported calendar") from None
        return self


class BehaviorDefaults(Strict):
    daily_variation_pct: Pct
    output_ratio: Annotated[Number, Field(ge=0, lt=1)]
    cache_hit_ratio: Ratio
    cache_hit_variation_pp: Annotated[Number, Field(ge=0, le=100)]
    cache_write_ratio: Ratio  # share of non-cache-read input that is a cache write
    cache_write_1h_share: Ratio  # share of cache writes at the 1-hour lifetime
    mix_variation_pct: Pct
    daily_zero_probability: Ratio
    # Weekend reduction is OFF by default: omit these to generate no weekly pattern.
    # Set weekend_reduction_probability > 0 to opt into a weekend drop.
    weekend_reduction_probability: Ratio = 0.0
    weekend_reduction_factor: Annotated[Number, Field(gt=0, le=1)] = 0.25


class Profile(Strict):
    daily_input_tokens: PositiveInt
    # Optional personal spikes: on each active weekday of a user on this profile, spike with
    # `spike_probability` and scale that user's day by a multiplier drawn uniformly in
    # [spike_multiplier_min, spike_multiplier_max]. All three together or none; an explicit
    # null is the same as omitting the field.
    spike_probability: Ratio | None = None
    spike_multiplier_min: Annotated[Number, Field(ge=1)] | None = None
    spike_multiplier_max: Annotated[Number, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def personal_spike_fields(self):
        given = [self.spike_probability is not None, self.spike_multiplier_min is not None,
                 self.spike_multiplier_max is not None]
        if any(given) and not all(given):
            raise ValueError("spike_probability, spike_multiplier_min and spike_multiplier_max "
                             "must be set together")
        if all(given) and self.spike_multiplier_max < self.spike_multiplier_min:
            raise ValueError("spike_multiplier_max must not be below spike_multiplier_min")
        return self

    @property
    def has_personal_spikes(self) -> bool:
        return self.spike_probability is not None

    @model_serializer(mode="wrap")
    def _drop_unset_spikes(self, handler):
        # Byte-identity (like UserRecord.github_login): a profile without personal spikes
        # dumps exactly as before, so config.yaml and its config_sha256 do not change.
        data = handler(self)
        for key in ("spike_probability", "spike_multiplier_min", "spike_multiplier_max"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


class Activity(Strict):
    daily_zero_probability: Ratio
    # Weekend reduction is OFF by default here too (see BehaviorDefaults).
    weekend_reduction_probability: Ratio = 0.0
    weekend_reduction_factor: Annotated[Number, Field(gt=0, le=1)] = 0.25


class TeamProfile(Strict):
    profile_mix: dict[str, float]
    project_mix: dict[str, float]
    activity: Activity | None = None

    @field_validator("profile_mix", mode="before")
    @classmethod
    def profile_weights(cls, value):
        return shares(value, "profile_mix")

    @field_validator("project_mix", mode="before")
    @classmethod
    def project_weights(cls, value):
        return shares(value, "project_mix")


class Project(Strict):
    name: Name


class Agent(Strict):
    billing_mode: Literal["api"]
    usage_share: Ratio
    models: dict[str, float]

    @field_validator("billing_mode", mode="before")
    @classmethod
    def billing(cls, value):
        # TODO(subscription): add subscription generation and equivalent-cost interpretation.
        if value != "api":
            raise ValueError("unsupported billing mode; only api is supported")
        return value

    @field_validator("models", mode="before")
    @classmethod
    def model_weights(cls, value):
        return shares(value, "model")


class SpikeDateRange(Strict):
    start_date: CalendarDate
    end_date: CalendarDate

    @model_validator(mode="after")
    def finish(self):
        if self.end_date < self.start_date:
            raise ValueError("spike rule end must not precede its start")
        return self


class SpikeModelTarget(Strict):
    agent: Name
    model: Name


class SpikeTargets(Strict):
    teams: list[Name]
    agents: list[Name]
    models: list[SpikeModelTarget]

    @model_validator(mode="after")
    def unique(self):
        if len(self.teams) != len(set(self.teams)):
            raise ValueError("spike rule Team targets must be unique")
        if len(self.agents) != len(set(self.agents)):
            raise ValueError("spike rule agent targets must be unique")
        pairs = [(target.agent, target.model) for target in self.models]
        if len(pairs) != len(set(pairs)):
            raise ValueError("spike rule model targets must be unique")
        if any(target.agent in self.agents for target in self.models):
            raise ValueError("spike rule model target is redundant with its agent target")
        self.teams.sort()
        self.agents.sort()
        self.models.sort(key=lambda target: (target.agent, target.model))
        return self


class SpikeRule(Strict):
    id: Name
    probability: Ratio
    multiplier_min: Annotated[Number, Field(ge=1)]
    multiplier_max: Annotated[Number, Field(ge=1)]
    date_range: SpikeDateRange | None = None
    targets: SpikeTargets

    @model_validator(mode="after")
    def bounds(self):
        if self.multiplier_max < self.multiplier_min:
            raise ValueError("spike rule maximum must not be below minimum")
        return self


class ZeroPeriod(Strict):
    id: Name
    target_type: Literal["user", "team", "org"]
    target: Name
    start_date: CalendarDate
    end_date: CalendarDate

    @model_validator(mode="after")
    def finish(self):
        if self.end_date < self.start_date:
            raise ValueError("event end must not precede its start")
        return self


class Events(Strict):
    spike_rules: list[SpikeRule]
    zero_periods: list[ZeroPeriod]


_CURVE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(?::[0-9a-f]{16})?")


def curve_ref(value):
    if not isinstance(value, str) or not _CURVE_REF.fullmatch(value.strip()):
        raise ValueError("curve reference must be name or name:id (a 16-hex-character id)")
    return value.strip()


CurveRef = Annotated[str, BeforeValidator(curve_ref)]


class CurveBindings(Strict):
    """Optional Task-1 curve artifact bindings (org/team/agent -> `name`/`name:id`)."""
    artifacts: Path
    org: CurveRef | None = None
    teams: dict[str, CurveRef] = Field(default_factory=dict)
    agents: dict[str, CurveRef] = Field(default_factory=dict)

    @field_validator("teams", "agents", mode="before")
    @classmethod
    def named_targets(cls, values):
        if not isinstance(values, dict):
            raise ValueError("expected a mapping of target name to curve reference")
        result = {}
        for key, value in values.items():
            key = name(key)
            if key in result:
                raise ValueError("duplicate normalized target name")
            result[key] = value
        return result


class SeriesConfig(Strict):
    schema_version: Literal[3]
    series: SeriesOptions
    defaults: BehaviorDefaults
    profiles: dict[str, Profile]
    team_profiles: dict[str, TeamProfile]
    projects: list[Project]
    agents: dict[str, Agent]
    events: Events
    curve: CurveBindings | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 3:
            raise ValueError("schema_version must be integer 3")
        return value

    @field_validator("profiles", "team_profiles", "agents", mode="before")
    @classmethod
    def mapping_names(cls, values):
        if not isinstance(values, dict) or not values:
            raise ValueError("expected nonempty named mapping")
        result = {}
        for key, value in values.items():
            key = name(key)
            if key in result:
                raise ValueError("duplicate normalized name")
            result[key] = value
        return {key: result[key] for key in sorted(result)}

    @model_validator(mode="after")
    def validate_all(self):
        weights = shares({key: agent.usage_share for key, agent in self.agents.items()}, "agent")
        for key, value in weights.items():
            self.agents[key].usage_share = value
        project_names = [project.name.casefold() for project in self.projects]
        if not project_names or len(project_names) != len(set(project_names)):
            raise ValueError("Project names must be nonempty and unique case-insensitively")
        for team in self.team_profiles.values():
            if not set(team.profile_mix) <= self.profiles.keys():
                raise ValueError("unknown profile in Team profile_mix")
            if not set(team.project_mix) <= {project.name for project in self.projects}:
                raise ValueError("unknown Project name in Team project_mix; use catalog names as mixture keys")
        events = [*self.events.spike_rules, *self.events.zero_periods]
        if len({event.id for event in events}) != len(events):
            raise ValueError("event IDs must be unique")
        end = self.series.start_date + timedelta(days=self.series.days - 1)
        for event in self.events.zero_periods:
            if event.start_date < self.series.start_date or event.end_date > end:
                raise ValueError("event is outside the series range")
        for rule in self.events.spike_rules:
            if rule.date_range and (
                rule.date_range.start_date < self.series.start_date
                or rule.date_range.end_date > end
            ):
                raise ValueError("spike rule range is outside the series range")
            unknown_agents = set(rule.targets.agents) - self.agents.keys()
            if unknown_agents:
                raise ValueError("spike rule references unknown agent")
            if any(
                target.agent not in self.agents
                or target.model not in self.agents[target.agent].models
                for target in rule.targets.models
            ):
                raise ValueError("spike rule references unknown agent/model pair")
        self.events.spike_rules.sort(key=lambda rule: rule.id)
        return self


def _load(path: Path, config_type):
    path = Path(path)
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
        if isinstance(raw, dict):
            if config_type is RosterConfig:
                roster = raw.get("roster")
                teams = roster.get("teams") if isinstance(roster, dict) else None
                if isinstance(teams, list) and any(isinstance(team, dict) and "team_id" in team for team in teams):
                    raise ConfigError("team_id is no longer an operator input; identify each Team by team_name")
            if config_type is SeriesConfig:
                projects = raw.get("projects")
                if isinstance(projects, list) and any(isinstance(project, dict) and "project_id" in project for project in projects):
                    raise ConfigError("project_id is no longer an operator input; define Projects by name and key project_mix by name")
        config = config_type.model_validate(raw)
    except ConfigError:
        raise
    except ValidationError as exc:
        # Never print Pydantic input values: they can contain whole identity records.
        locations = [(".".join(map(str, error["loc"])) or "root") + ": " + error["msg"] for error in exc.errors()]
        raise ConfigError("invalid configuration fields: " + ", ".join(locations)) from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark else ""
        raise ConfigError("invalid YAML" + where) from exc
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise ConfigError(f"cannot read configuration: {type(exc).__name__}") from exc
    options = config.roster if isinstance(config, RosterConfig) else config.series if isinstance(config, SeriesConfig) else None
    if options:
        for field in ("users_file", "roster_file"):
            if hasattr(options, field):
                value = getattr(options, field)
                setattr(options, field, value.resolve() if value.is_absolute() else (path.resolve().parent / value).resolve())
    if isinstance(config, SeriesConfig) and config.curve is not None:
        value = config.curve.artifacts
        config.curve.artifacts = value.resolve() if value.is_absolute() else (path.resolve().parent / value).resolve()
    return config


def load_users_config(path: Path) -> UsersConfig:
    return _load(path, UsersConfig)


def load_roster_config(path: Path) -> RosterConfig:
    return _load(path, RosterConfig)


def load_series_config(path: Path) -> SeriesConfig:
    return _load(path, SeriesConfig)


def validate_users(users) -> str:
    """Validate saved identity invariants shared by roster and series operations."""
    if not users:
        raise InputDataError("user pool must be nonempty")
    ids = [user.user_id for user in users]
    emails = [user.email.casefold() for user in users]
    orgs = {user.org_id for user in users}
    if len(ids) != len(set(ids)) or len(emails) != len(set(emails)):
        raise InputDataError("duplicate user IDs or emails")
    if len(orgs) != 1:
        raise InputDataError("users must belong to one Organization")
    return next(iter(orgs))


def validate_series_inputs(config, users, teams) -> None:
    org = validate_users(users)
    if not teams or any(not team.user_ids for team in teams):
        raise InputDataError("roster must contain nonempty Teams")
    if any(team.org_id != org for team in teams):
        raise InputDataError("inputs must belong to one Organization")
    members = [uid for team in teams for uid in team.user_ids]
    if len(members) != len(set(members)) or not set(members) <= {user.user_id for user in users}:
        raise InputDataError("roster has duplicate or unknown users")
    if (len({team.team_id for team in teams}) != len(teams)
        or len({team.team_name.casefold() for team in teams}) != len(teams)):
        raise InputDataError("duplicate Team ID or name")
    names = {team.team_name for team in teams}
    if names != set(config.team_profiles):
        raise InputDataError("team_profiles must exactly match roster Team names")
    targets = {"user": set(members), "team": names, "org": {org}}
    for event in config.events.zero_periods:
        if event.target not in targets[event.target_type]:
            raise InputDataError("event target is not in the participating roster")
    if any(set(rule.targets.teams) - names for rule in config.events.spike_rules):
        raise InputDataError("spike rule Team target is not in the participating roster")
    if config.curve is not None:
        _validate_curve_bindings(config, names)


def _validate_curve_bindings(config, team_names) -> None:
    """Resolve and verify every bound `name`/`name:id`; fail loud, naming the binding."""
    curve = config.curve
    if not curve.artifacts.is_dir():
        raise InputDataError(f"curve.artifacts is not a directory: {curve.artifacts}")
    unknown_teams = sorted(set(curve.teams) - team_names)
    if unknown_teams:
        raise InputDataError(f"curve.teams references unknown Team(s): {unknown_teams}")
    unknown_agents = sorted(set(curve.agents) - config.agents.keys())
    if unknown_agents:
        raise InputDataError(f"curve.agents references unknown agent(s): {unknown_agents}")
    bindings = [("org", curve.org)] if curve.org is not None else []
    bindings += [(f"team {team!r}", ref) for team, ref in curve.teams.items()]
    bindings += [(f"agent {agent!r}", ref) for agent, ref in curve.agents.items()]
    for label, ref in bindings:
        try:
            load_curve_artifact(resolve_curve_ref(curve.artifacts, ref))
        except CurveError as exc:
            raise InputDataError(f"curve binding for {label} ({ref!r}) is invalid: {exc}") from exc
