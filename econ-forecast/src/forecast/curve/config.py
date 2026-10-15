"""Strict, offline YAML configuration and validation for shape curves.

Mirrors `forecast.synthetic.config`'s patterns (strict pydantic models, a duplicate-key-
rejecting YAML loader, precise `ConfigError` messages) but is independent of it: curve is
a standalone module and does not import from `synthetic`.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field, StrictInt,
                      ValidationError, field_validator, model_validator)

from .errors import ConfigError
from .model import resolve_anchors


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


def curve_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("name must be a nonempty string")
    stripped = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", stripped):
        raise ValueError("name must be alphanumeric with '-'/'_' separators; "
                         "it is used as an artifact directory and a stage-2 reference key")
    return stripped


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


PositiveInt = Annotated[int, Field(gt=0, strict=True)]
Name = Annotated[str, BeforeValidator(curve_name)]


class Axis(Strict):
    start: StrictInt
    end: StrictInt

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("axis.end must be greater than axis.start")
        return self


class PreviewOptions(Strict):
    days: PositiveInt | None = None
    baseline_tokens: Annotated[float, Field(gt=0)] | None = None


class CurveConfig(Strict):
    schema_version: Literal[1]
    name: Name
    interp: Literal["pchip"]
    axis: Axis
    anchors: list[tuple[StrictInt, str]] = Field(min_length=2)
    preview: PreviewOptions = Field(default_factory=PreviewOptions)
    description: str | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be integer 1")
        return value

    @field_validator("anchors", mode="before")
    @classmethod
    def anchor_pairs(cls, value):
        if not isinstance(value, list) or len(value) < 2:
            raise ValueError('anchors must be a list of at least two [pos, "value"] pairs')
        pairs = []
        for item in value:
            if (not isinstance(item, (list, tuple)) or len(item) != 2
                    or isinstance(item[0], bool) or not isinstance(item[0], int)
                    or not isinstance(item[1], str)):
                raise ValueError('each anchor must be a [pos, "value"] pair: '
                                 'pos an integer, value a quoted string')
            pairs.append((item[0], item[1]))
        return pairs

    @model_validator(mode="after")
    def resolvable(self):
        # Exercises the exact resolution stage-2 (and this tool's own runner/preview)
        # will use, so an invalid config is rejected at load time, not at generation time.
        resolve_anchors(self.anchors, self.axis.start, self.axis.end)
        return self


def load_curve_config(path: Path) -> CurveConfig:
    path = Path(path)
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
        config = CurveConfig.model_validate(raw)
    except ConfigError:
        raise
    except ValidationError as exc:
        parts = []
        for error in exc.errors():
            location = ".".join(map(str, error["loc"]))
            message = error["msg"].removeprefix("Value error, ")
            parts.append(f"{location}: {message}" if location else message)
        raise ConfigError("invalid configuration: " + "; ".join(parts)) from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark else ""
        raise ConfigError("invalid YAML" + where) from exc
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise ConfigError(f"cannot read configuration: {type(exc).__name__}") from exc
    return config
