"""Strict forecasting YAML configuration."""

from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal
import math
import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)
from .errors import InputError


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError("must be a finite number")
    return number


Multiplier = Annotated[float, BeforeValidator(finite_number), Field(ge=0)]
Spread = Annotated[float, BeforeValidator(finite_number), Field(ge=0)]
CorrectionRatio = Annotated[float, BeforeValidator(finite_number), Field(ge=0, le=1)]


def _event_date(value):
    if isinstance(value, datetime):
        raise ValueError("must be a calendar date, not a date-time")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("must be an ISO calendar date (YYYY-MM-DD)") from exc
    raise ValueError("must be a calendar date")


EventDate = Annotated[date, BeforeValidator(_event_date)]


class Scope(Strict):
    type: Literal["organization", "team", "project"]
    name: str | None = None
    org_id: str | None = None

    @field_validator("name", "org_id")
    @classmethod
    def nonempty_name(cls, value):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("scope name/Organization ID must be nonempty")
        return value

    @model_validator(mode="after")
    def identity(self):
        if self.type == "organization" and self.name is not None:
            raise ValueError("organization scope cannot specify name")
        if self.type != "organization" and not self.name:
            raise ValueError("team/project scope requires name")
        return self


class Input(Strict):
    dataset_dir: Path

    @field_validator("dataset_dir", mode="before")
    @classmethod
    def path_value(cls, value):
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError("dataset_dir must be a path string")
        return Path(value)


class History(Strict):
    lookback_days: StrictInt = Field(ge=14, le=3660)
    minimum_days: StrictInt = Field(ge=14)
    minimum_active_days: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def ranges(self):
        if (
            self.minimum_days > self.lookback_days
            or self.minimum_active_days > self.lookback_days
        ):
            raise ValueError("history minima cannot exceed lookback_days")
        return self


class Model(Strict):
    """Data-driven ETS (design doc §3). `seasonal`/`trend` are explicit switches:
    `auto` lets the tiered fallback learn the component from the data; `none` forces
    it off (e.g. level-only) regardless of history length.
    """

    method: Literal["ets"]
    seasonal: Literal["auto", "none"]
    trend: Literal["auto", "none"]


class KnownEvent(Strict):
    """A single dated known-event window (design doc §7)."""

    start_date: EventDate
    end_date: EventDate
    multiplier: Multiplier
    spread: Spread

    @model_validator(mode="after")
    def ranges(self):
        if self.end_date < self.start_date:
            raise ValueError("known_events end_date must not precede start_date")
        if self.spread > self.multiplier:
            raise ValueError("known_events spread must not exceed multiplier")
        return self


class KnownEvents(Strict):
    """Optional dated known-event overlay declarations, keyed by scope/agent name.

    Static shape/range checks live here. Cross-referencing names against the loaded
    dataset roster, and confirming windows fall within the resolved forecast horizon,
    requires the dataset and request window and is performed at run time (see
    `known_events.validate_known_events`), not here.
    """

    org: KnownEvent | None = None
    teams: dict[str, KnownEvent] = Field(default_factory=dict)
    projects: dict[str, KnownEvent] = Field(default_factory=dict)
    agents: dict[str, KnownEvent] = Field(default_factory=dict)

    @field_validator("teams", "projects", "agents")
    @classmethod
    def nonempty_keys(cls, value):
        if any(not isinstance(name, str) or not name.strip() for name in value):
            raise ValueError("known_events name must be nonempty")
        return value

    def is_empty(self) -> bool:
        return not (self.org or self.teams or self.projects or self.agents)


class Allocation(Strict):
    lookback_days: StrictInt = Field(gt=0)


class Uncertainty(Strict):
    paths: StrictInt = Field(ge=1, le=10000)
    seed: StrictInt = Field(ge=0)
    max_negative_correction_ratio: CorrectionRatio


class Runtime(Strict):
    workers: StrictInt = Field(ge=1, le=4)


class ForecastConfig(Strict):
    schema_version: Literal[1]
    input: Input
    default_org_id: str | None
    scopes: list[Scope] = Field(min_length=1, max_length=1000)
    history: History
    model: Model
    allocation: Allocation
    uncertainty: Uncertainty
    known_events: KnownEvents = Field(default_factory=KnownEvents)
    runtime: Runtime

    @model_validator(mode="after")
    def defaults(self):
        if any(s.org_id is None for s in self.scopes) and not self.default_org_id:
            raise ValueError("default_org_id required")
        # Known-events name resolution (config.py's KnownEvents carries no per-event org
        # key; see known_events.validate_known_events) is scoped to one organization for
        # Phase 1. Multi-org known_events is out of scope; require an unambiguous org.
        if not self.known_events.is_empty() and not self.default_org_id:
            raise ValueError("default_org_id required when known_events is set")
        return self

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be integer 1")
        return value

    @field_validator("default_org_id")
    @classmethod
    def organization_id(cls, value):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("default_org_id must be nonempty")
        return value


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    out = {}
    for k, v in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in out:
            raise InputError("duplicate YAML mapping key")
        out[key] = loader.construct_object(v, deep=deep)
    return out


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_config(path: Path) -> ForecastConfig:
    path = Path(path)
    try:
        if path.stat().st_size > 16 * 1024**2:
            raise InputError("forecast configuration exceeds 16 MiB")
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
        for scope in raw.get("scopes", []) if isinstance(raw, dict) else []:
            if isinstance(scope, dict) and "id" in scope:
                raise InputError("forecast scope id is no longer an operator input; use name for Team/Project scopes")
        cfg = ForecastConfig.model_validate(raw)
    except InputError:
        raise
    except (
        OSError,
        UnicodeError,
        yaml.YAMLError,
        ValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise InputError(f"invalid forecast configuration: {exc}") from exc
    cfg.input.dataset_dir = (
        (path.resolve().parent / cfg.input.dataset_dir).resolve()
        if not cfg.input.dataset_dir.is_absolute()
        else cfg.input.dataset_dir.resolve()
    )
    if cfg.runtime.workers > (__import__("os").cpu_count() or 1):
        raise InputError("workers exceeds available CPU count")
    return cfg
