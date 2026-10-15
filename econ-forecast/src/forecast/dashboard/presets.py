"""Dashboard scenario presets: load, validate, and render to `scenario/1.0` files.

`presets.yaml` (schema `dashboard-presets/1.0`) lists the what-ifs the dashboard offers:
exactly one `baseline` (priced with no scenario) followed by named presets, each a list of
cost-module scenario changes. A preset's changes are validated with the cost module's own
`ScenarioChange`, so the two cannot drift; the only addition here is a strict key check,
because the cost schemas ignore unknown keys and a typo such as `token_factr` would
otherwise be silently dropped. The loader is strict about the file itself too: only the
`schema` alias (not the field name `schema_`), only `from` in a transfer (not `from_`),
no duplicate YAML keys, and no non-finite numbers. A preset never carries `effective_from`;
the runner supplies one date for the whole run when it renders (`render_scenario`).

`load_presets` and `render_scenario` raise only `InputError`; `write_scenario` also raises
`ExecutionError` when the file cannot be written.
"""

from __future__ import annotations

from datetime import date
import math
from pathlib import Path
import re
import typing

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
import yaml

from forecast.cost.schemas import ScenarioChange
from forecast.forecasting.config import UniqueLoader
from forecast.forecasting.errors import InputError as ForecastInputError

from .errors import ExecutionError, InputError

SCHEMA = "dashboard-presets/1.0"
BASELINE_ID = "baseline"
SCENARIO_SCHEMA = "scenario/1.0"

#: Shipped preset list: `econ-forecast/config/dashboard/presets.yaml`, located relative to
#: this package, not the working directory.
DEFAULT_PRESETS_PATH = Path(__file__).resolve().parents[3] / "config" / "dashboard" / "presets.yaml"

# Ids become directory and file names (`cost/<id>`, `scenarios/<id>.yaml`).
_ID = re.compile(r"[a-z0-9][a-z0-9-]*")


def _model_class(annotation) -> type[BaseModel] | None:
    """The pydantic model inside an `X | None` field annotation."""
    for arg in typing.get_args(annotation):
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
    return None


def _accepted_keys(model: type[BaseModel]) -> set[str]:
    """The key each field is written under in YAML: its alias when it has one (the cost
    models also accept the Python name, `from_`, but a preset file must not)."""
    return {field.alias or name for name, field in model.model_fields.items()}


def _check_change_keys(raw) -> None:
    """Reject keys the cost schemas would silently ignore. Allowed keys come from
    `ScenarioChange` and its nested models, not from a second list kept here."""
    if not isinstance(raw, dict):
        return  # pydantic reports the wrong type
    outer = set(ScenarioChange.model_fields)
    # key=str: YAML keys can be of mixed types (`on` reads as a boolean), which sorted() rejects.
    unknown = sorted(set(raw) - outer, key=str)
    if unknown:
        raise ValueError(f"unknown change key(s) {unknown}; expected one of {sorted(outer)}")
    for kind, body in raw.items():
        model = _model_class(ScenarioChange.model_fields[kind].annotation)
        if not isinstance(body, dict) or model is None:
            continue
        if "effective_from" in body:
            raise ValueError(
                f"{kind}.effective_from must not be set in a preset; the runner supplies it"
            )
        unknown = sorted(set(body) - _accepted_keys(model), key=str)
        if unknown:
            raise ValueError(f"unknown key(s) {unknown} in {kind}")
        for key, value in body.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{kind}.{key} must be a finite number")


class PresetSpec(BaseModel):
    """One preset. Strict: unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str
    group: str | None = None
    changes: list[ScenarioChange] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError(f"id {value!r} must match ^[a-z0-9][a-z0-9-]*$")
        return value

    @field_validator("label", "description")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("changes", mode="before")
    @classmethod
    def _strict_keys(cls, value):
        if isinstance(value, list):
            for item in value:
                _check_change_keys(item)
        return value

    @model_validator(mode="after")
    def _shape(self):
        if self.id == BASELINE_ID:
            if self.changes or self.group is not None:
                raise ValueError("the baseline preset has no changes and no group")
        else:
            if not self.changes:
                raise ValueError("a preset needs at least one change")
            if self.group is None or not self.group.strip():
                raise ValueError("a preset needs a non-empty group")
        # Again on the validated values: 'inf' and 1e999 reach pydantic as strings (YAML has
        # no float form for 1e999), pass the raw check above, and the cost schemas accept them.
        for change in self.changes:
            (kind, body), = change.model_dump(exclude_none=True).items()
            for key, value in body.items():
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError(f"{kind}.{key} must be a finite number")
        return self

    @property
    def is_baseline(self) -> bool:
        return self.id == BASELINE_ID


class PresetFile(BaseModel):
    """The whole `presets.yaml`."""

    # Alias only: the file says `schema:`; `schema_` is just the Python attribute.
    model_config = ConfigDict(extra="forbid")

    schema_: str = Field(alias="schema")
    presets: list[PresetSpec]

    @model_validator(mode="after")
    def _list_rules(self):
        if self.schema_ != SCHEMA:
            raise ValueError(f"schema must be {SCHEMA!r}, got {self.schema_!r}")
        ids = [p.id for p in self.presets]
        if len(ids) != len(set(ids)):
            raise ValueError("preset ids must be unique")
        if ids.count(BASELINE_ID) != 1:
            raise ValueError(f"exactly one preset must have id {BASELINE_ID!r}")
        if ids[0] != BASELINE_ID:
            raise ValueError(f"{BASELINE_ID!r} must be the first preset")
        return self


class _PlainDumper(yaml.SafeDumper):
    """No YAML anchors: every change carries its own `effective_from` line."""

    def ignore_aliases(self, data):
        return True


def _describe(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        where = ".".join(str(x) for x in error["loc"]) or "root"
        parts.append(f"{where}: {error['msg']}")
    return "; ".join(parts)


def load_presets(path: Path = DEFAULT_PRESETS_PATH) -> tuple[PresetSpec, ...]:
    """Load and validate a presets file, in file order. Every problem (unreadable file,
    duplicate YAML key, rule violation) is an `InputError` naming the file."""
    path = Path(path)
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
    except ForecastInputError as exc:  # UniqueLoader: duplicate mapping key
        raise InputError(f"invalid presets file {path.name}: {exc}") from exc
    except TypeError as exc:  # UniqueLoader: a mapping key that is not hashable (`? [a, b]`)
        raise InputError(f"invalid presets file {path.name}: unusable mapping key") from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise InputError(f"cannot read presets file: {path.name}") from exc
    if not isinstance(raw, dict):
        raise InputError(f"presets file must be a mapping: {path.name}")
    try:
        loaded = PresetFile.model_validate(raw)
    except ValidationError as exc:
        raise InputError(f"invalid presets file {path.name}: {_describe(exc)}") from exc
    return tuple(loaded.presets)


def render_scenario(preset: PresetSpec, effective_from: date) -> dict:
    """The preset as a `scenario/1.0` document: organization scope (D5), each change
    carrying `effective_from`. Baseline has no scenario (it is priced with none)."""
    if preset.is_baseline:
        raise InputError("the baseline preset has no scenario")
    changes = []
    for change in preset.changes:
        (kind, body), = change.model_dump(by_alias=True, exclude_none=True, mode="json").items()
        changes.append({kind: {**body, "effective_from": effective_from}})
    return {
        "schema": SCENARIO_SCHEMA,
        "name": preset.id,
        "scope": {"type": "organization"},
        "changes": changes,
    }


def write_scenario(preset: PresetSpec, effective_from: date, path: Path) -> Path:
    """Write `render_scenario(...)` as YAML; the cost module needs the file stem to
    equal the scenario name, so callers pass `<dir>/<preset id>.yaml`. Raises
    `InputError` for the baseline and `ExecutionError` if the file cannot be written."""
    path = Path(path)
    text = yaml.dump(render_scenario(preset, effective_from), Dumper=_PlainDumper, sort_keys=False)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ExecutionError(f"cannot write scenario file {path}: {exc.strerror or exc}") from exc
    return path
