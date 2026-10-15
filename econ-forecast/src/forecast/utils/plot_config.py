"""Strict `plot.yaml` models for the forecast plotting utility (design
`plot-forecast-enhancements-design.md` §6, §9).

One `plot.yaml` has one section per kind (`forecast:`, `cost:`); each CLI
(`cli_plot_forecast.py`, `cli_plot_cost.py`) draws its own section against
whatever run it was given on the command line -- the config carries no run
paths. An entry's `scope_name` is optional for `team`/`project`: omitted, it
expands to every scope of that type in the run (design §5); that expansion
needs the run's own scope list, so it happens in `run.py` (T4), not here.
This module only validates the YAML shape, applies `defaults`, and provides
the shared `stem()` naming convention (§7).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from forecast.forecasting.errors import InputError
from forecast.forecasting.schemas import slug

_FORMATS = ("html", "png", "both")
_COST_VIEWS = ("total", "breakdown", "per-model")


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
            raise InputError("plot configuration YAML mapping keys must be scalar") from exc
        if duplicate:
            raise InputError("duplicate YAML mapping key in plot configuration")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _path_value(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("must be a nonempty path string")
    return Path(value)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Band(Strict):
    low: int = Field(gt=0, lt=100)
    high: int = Field(gt=0, lt=100)

    @model_validator(mode="after")
    def ordered(self):
        if self.low >= self.high:
            raise ValueError("band low must be less than band high")
        return self

    def as_tuple(self) -> tuple[int, int]:
        return (self.low, self.high)


def _default_band() -> Band:
    return Band(low=10, high=90)


class Defaults(Strict):
    format: Literal["html", "png", "both"] = "both"
    samples: int = Field(default=0, ge=0)
    band: Band = Field(default_factory=_default_band)


def _nonempty_name(value):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError("scope_name must be a nonempty string")
    return value


class ForecastEntry(Strict):
    """§6 `forecast:` entry: tokens, one file per scope, selected models stacked."""

    scope_type: Literal["organization", "team", "project"]
    scope_name: str | None = None
    models: list[str] | None = None
    format: Literal["html", "png", "both"] | None = None
    samples: int | None = Field(default=None, ge=0)
    band: Band | None = None

    @field_validator("scope_name")
    @classmethod
    def _scope_name_ok(cls, value):
        return _nonempty_name(value)

    @field_validator("models")
    @classmethod
    def _models_ok(cls, value):
        if value is not None:
            if not value or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError("models must contain nonempty model IDs")
            if len(set(value)) != len(value):
                raise ValueError("model selections must be unique")
        return value

    @model_validator(mode="after")
    def _scope_rules(self):
        if self.scope_type == "organization" and self.scope_name is not None:
            raise ValueError("scope_name must be omitted for an organization")
        return self

    def resolve(self, defaults: Defaults) -> "ResolvedForecastEntry":
        return ResolvedForecastEntry(
            scope_type=self.scope_type,
            scope_name=self.scope_name,
            models=list(self.models) if self.models is not None else None,
            format=self.format or defaults.format,
            samples=defaults.samples if self.samples is None else self.samples,
            band=defaults.band if self.band is None else self.band,
        )


class CostEntry(Strict):
    """§6 `cost:` entry: USD. `views` default `[total, breakdown]`; `per-model`
    expands to every model actually present in the resolved scope (§7) -- not
    configurable, so there is no `models` field here."""

    scope_type: Literal["organization", "team", "project"]
    scope_name: str | None = None
    views: list[Literal["total", "breakdown", "per-model"]] | None = None
    format: Literal["html", "png", "both"] | None = None
    samples: int | None = Field(default=None, ge=0)
    band: Band | None = None

    @field_validator("scope_name")
    @classmethod
    def _scope_name_ok(cls, value):
        return _nonempty_name(value)

    @field_validator("views")
    @classmethod
    def _views_ok(cls, value):
        if value is not None:
            if not value:
                raise ValueError("views must be nonempty when given")
            if len(set(value)) != len(value):
                raise ValueError("views must not repeat")
        return value

    @model_validator(mode="after")
    def _scope_rules(self):
        if self.scope_type == "organization" and self.scope_name is not None:
            raise ValueError("scope_name must be omitted for an organization")
        return self

    def resolve(self, defaults: Defaults) -> "ResolvedCostEntry":
        return ResolvedCostEntry(
            scope_type=self.scope_type,
            scope_name=self.scope_name,
            views=list(self.views) if self.views is not None else ["total", "breakdown"],
            format=self.format or defaults.format,
            samples=defaults.samples if self.samples is None else self.samples,
            band=defaults.band if self.band is None else self.band,
        )


@dataclass(frozen=True)
class ResolvedForecastEntry:
    """One forecast plot request, defaults already applied. Shared shape for
    single-plot CLI mode and config mode."""

    scope_type: str
    scope_name: str | None
    models: list[str] | None
    format: str
    samples: int
    band: Band


@dataclass(frozen=True)
class ResolvedCostEntry:
    """One cost plot request, defaults already applied."""

    scope_type: str
    scope_name: str | None
    views: list[str]
    format: str
    samples: int
    band: Band


def stem(scope_type: str, identity: str, view: str, measure: str) -> str:
    """`<scope>__<view>__<measure>`, each part slugged (design §7). `identity`
    is the org_id for `organization`, else the scope's own name."""
    scope = f"{slug(scope_type)}-{slug(identity)}"
    return f"{scope}__{slug(view)}__{slug(measure)}"


class PlotConfig(Strict):
    schema_version: Literal[1]
    plotly_js: Path | None = None
    defaults: Defaults = Field(default_factory=Defaults)
    forecast: list[ForecastEntry] | None = None
    cost: list[CostEntry] | None = None

    @field_validator("plotly_js", mode="before")
    @classmethod
    def _plotly_js_path(cls, value):
        return None if value is None else _path_value(value)

    @field_validator("forecast", "cost")
    @classmethod
    def _nonempty_section(cls, value):
        if value is not None and not value:
            raise ValueError("a plot.yaml section, if present, must list at least one entry")
        return value

    @model_validator(mode="after")
    def _at_least_one_section(self):
        if not self.forecast and not self.cost:
            raise ValueError("plot configuration must include at least one of forecast/cost")
        return self


def _resolve(base: Path, value: Path) -> Path:
    return value if value.is_absolute() else (base / value).resolve()


def load_plot_config(path: Path) -> PlotConfig:
    """Load and fully validate `plot.yaml`; `plotly_js` resolves relative to the
    YAML (like `users_file`/`roster_file` in synthetic configs). No run path
    lives here -- the CLI supplies the run."""
    path = Path(path)
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise InputError("plot configuration exceeds 4 MiB")
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
        if not isinstance(raw, dict):
            raise InputError("plot configuration must be a YAML mapping")
        config = PlotConfig.model_validate(raw)
    except InputError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError, TypeError, ValueError) as exc:
        raise InputError(f"invalid plot configuration: {exc}") from exc
    if config.plotly_js is not None:
        config.plotly_js = _resolve(path.resolve().parent, config.plotly_js)
    return config
