"""Batch runner: one forecast and one cost run per dashboard preset.

Given a Layer 1 series dataset and a preset list, `run_dashboard_forecast` fits one
forecast (every scope of the dataset, origin per D1) and prices it once per preset. It
uses only the Python APIs of `forecast.forecasting` and `forecast.cost`; the export and the
command line sit on top of it (T4).

Output layout (the runner writes the first four entries and nothing else)::

    <output_dir>/forecast.yaml        the forecast config this run used
    <output_dir>/scenarios/<id>.yaml  one scenario per non-baseline preset
    <output_dir>/forecast/            the forecast run
    <output_dir>/cost/<id>/           one cost run per preset (`baseline` has no scenario)
    <output_dir>/forecast_export.json the dashboard export (written by `export_forecast`,
                                      not by this runner; `force` clears a stale one)

Errors: everything this runner reports is an `InputError` or an `ExecutionError`,
filesystem errors included; `stage` is `"forecast"` or `"cost"` when that stage raised or
did not complete. `InputError`: bad presets, dataset, template or output location, or an
input the forecast or cost stage rejects. `ExecutionError`: a stage did not complete, or a
file of this run could not be written. The presets, the dataset's manifest and config
snapshot, and the template are read and checked before the output directory is touched;
what only the forecaster or the cost module can reject (a template value, a dataset that
fails the forecaster's own checks, a preset naming a model the data does not have) is found
later, after a forced rerun has cleared the earlier run and written part of the new one.
A run stops at the first stage that does not complete; whatever was written before the
failure is left in place for diagnosis, so a rerun into the same directory needs
`force=True`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
import os
from pathlib import Path
import shutil
from types import MappingProxyType
from typing import Mapping

import yaml

from common.model_pricing import Catalog
from common.msging.log import get_logger

from forecast.cost import CostRequest, run_cost
from forecast.cost.errors import ExecutionError as CostExecutionError
from forecast.cost.errors import InputError as CostInputError
from forecast.forecasting import ForecastRequest, load_run
from forecast.forecasting.errors import ExecutionError as ForecastExecutionError
from forecast.forecasting.errors import InputError as ForecastInputError
from forecast.forecasting.runner import run_forecast

from .errors import ExecutionError, InputError
from .presets import PresetSpec, load_presets, write_scenario

#: Template for `forecast.yaml`: modeling policy lives there, not in this module.
DEFAULT_FORECAST_TEMPLATE = (
    Path(__file__).resolve().parents[3] / "config" / "forecasting" / "forecast.yaml"
)

#: The dataset must cover at least this many days ending on the origin (inclusive).
MIN_HISTORY_DAYS = 28

#: Entries this runner owns inside `output_dir` (the only ones `force` removes).
_OUTPUT_ENTRIES = ("forecast.yaml", "scenarios", "forecast", "cost", "forecast_export.json")


@dataclass(frozen=True)
class RunScope:
    """One forecast scope. The organization's `name` is its org id."""

    scope_type: str  # "organization" | "team" | "project"
    name: str


@dataclass(frozen=True)
class DashboardRunResult:
    """A completed run, for the export step (T4) to read."""

    data_through: date
    origin: date
    org_id: str
    output_dir: Path
    forecast_dir: Path
    presets: tuple[PresetSpec, ...]
    cost_dirs: Mapping[str, Path]  # preset id -> cost run directory
    scopes: tuple[RunScope, ...]  # organization, teams, projects


def resolve_origin(data_through: date) -> date:
    """D1: the forecast origin. `data_through` itself when it is the last day of its
    month, else the last day of the previous month."""
    if (data_through + timedelta(days=1)).month != data_through.month:
        return data_through
    return data_through.replace(day=1) - timedelta(days=1)


# ---------------------------------------------------------------------------
# series dataset
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Series:
    org_id: str
    start: date
    end: date
    scopes: tuple[RunScope, ...]


def _read_series(series_dir: Path) -> _Series:
    """Manifest facts plus the scope list from the dataset's own config snapshot."""
    try:
        manifest = json.loads((series_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot read series manifest: {series_dir / 'manifest.json'}") from exc
    if not isinstance(manifest, dict):
        raise InputError("series manifest must be an object")
    if manifest.get("status") != "complete":
        raise InputError(f"series dataset is not complete (status {manifest.get('status')!r})")
    if manifest.get("operation") != "series":
        raise InputError(
            f"expected a Layer 1 series dataset (operation 'series'), got {manifest.get('operation')!r}"
        )
    org_id = manifest.get("org_id")
    if not isinstance(org_id, str) or not org_id:
        raise InputError("series manifest has no org_id")
    try:
        start = date.fromisoformat(manifest["start_date"])
        end = date.fromisoformat(manifest["end_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InputError("series manifest has no valid start_date/end_date") from exc
    if end < start:
        raise InputError("series manifest end_date precedes start_date")

    try:
        config = yaml.safe_load((series_dir / "config.yaml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise InputError(f"cannot read series config snapshot: {series_dir / 'config.yaml'}") from exc
    teams = config.get("team_profiles") if isinstance(config, dict) else None
    projects = config.get("projects") if isinstance(config, dict) else None
    if not isinstance(teams, dict) or not isinstance(projects, list):
        raise InputError("series config snapshot needs team_profiles and projects")
    names = [p.get("name") if isinstance(p, dict) else None for p in projects]
    if any(not isinstance(n, str) or not n for n in [*teams, *names]):
        raise InputError("series config snapshot has an unnamed team or project")
    scopes = (
        RunScope("organization", org_id),
        *(RunScope("team", name) for name in teams),
        *(RunScope("project", name) for name in names),
    )
    return _Series(org_id, start, end, scopes)


def series_scopes(series_dir: Path) -> tuple[RunScope, ...]:
    """The scopes a run over `series_dir` will forecast (organization, teams, projects)."""
    return _read_series(Path(series_dir).resolve()).scopes


def _check_history(series: _Series, origin: date) -> None:
    days = (origin - series.start).days + 1
    if days < MIN_HISTORY_DAYS:
        raise InputError(
            f"series covers {max(days, 0)} day(s) up to the origin {origin.isoformat()} "
            f"(data starts {series.start.isoformat()}); {MIN_HISTORY_DAYS} are needed"
        )


# ---------------------------------------------------------------------------
# output directory
# ---------------------------------------------------------------------------


def _identity(path: Path) -> tuple[int, int] | None:
    """(device, inode) of an existing path, else None."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_dev, stat.st_ino


def _identities(path: Path) -> set[tuple[int, int]]:
    """Identities of `path` and of every ancestor that exists."""
    found = (_identity(p) for p in (path, *path.parents))
    return {ident for ident in found if ident is not None}


def _check_separate(output_dir: Path, series_dir: Path) -> None:
    """Refuse an output directory that equals, contains or sits inside the series dataset.
    Compares resolved paths and also file identity, because on a case-insensitive
    filesystem `series` and `SERIES` are different strings for one directory."""
    series_id = _identity(series_dir)
    output_id = _identity(output_dir)
    clash = (
        output_dir == series_dir
        or output_dir in series_dir.parents
        or series_dir in output_dir.parents
        or (series_id is not None and series_id in _identities(output_dir))
        or (output_id is not None and output_id in _identities(series_dir))
    )
    if clash:
        raise InputError(
            f"output directory must be separate from the series dataset: {output_dir}"
        )


def _prepare_output(output_dir: Path, series_dir: Path, *, force: bool) -> None:
    """Enforce the output rules; with `force`, remove only this runner's own entries.

    Every problem with the location found here (not a directory, unreadable, cannot be
    cleared) is an `InputError`. A failure part-way through clearing leaves the entries
    already removed removed.
    """
    _check_separate(output_dir, series_dir)
    try:
        for parent in output_dir.parents:
            if parent.exists():
                if not parent.is_dir():
                    raise InputError(f"output path has a parent that is not a directory: {parent}")
                break
        if output_dir.exists() and not output_dir.is_dir():
            raise InputError(f"output path exists and is not a directory: {output_dir}")
        if not output_dir.exists() or not any(output_dir.iterdir()):
            return
    except OSError as exc:
        raise InputError(f"cannot inspect output directory {output_dir}: {exc.strerror or exc}") from exc
    if not force:
        raise InputError(f"output directory is not empty (use force to replace a run): {output_dir}")
    for name in _OUTPUT_ENTRIES:
        entry = output_dir / name
        try:
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
            elif entry.is_dir():
                shutil.rmtree(entry)
        except OSError as exc:
            raise InputError(f"cannot clear {entry} for a forced rerun: {exc.strerror or exc}") from exc


def _load_template(template: Path) -> dict:
    """The forecast template, read and checked before anything is written."""
    try:
        config = yaml.safe_load(Path(template).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise InputError(f"cannot read forecast template: {template}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("input"), dict):
        raise InputError(f"forecast template needs an input mapping: {template}")
    return config


def _write_forecast_yaml(config: dict, path: Path, series_dir: Path, series: _Series) -> None:
    """Template with `input.dataset_dir`, `default_org_id` and `scopes` replaced.
    A file that cannot be written is an `ExecutionError`."""
    config["input"]["dataset_dir"] = str(series_dir)
    config["default_org_id"] = series.org_id
    config["scopes"] = [
        {"type": "organization"}
        if s.scope_type == "organization"
        else {"type": s.scope_type, "name": s.name}
        for s in series.scopes
    ]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    except OSError as exc:
        raise ExecutionError(f"cannot write {path}: {exc.strerror or exc}") from exc


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


def _run_forecast_stage(config_path: Path, origin: date, forecast_dir: Path):
    try:
        result = run_forecast(config_path, ForecastRequest(origin, "month", 1, 2), forecast_dir)
    except ForecastInputError as exc:
        raise InputError(f"forecast: {exc}", stage="forecast") from exc
    except ForecastExecutionError as exc:
        raise ExecutionError(f"forecast: {exc}", stage="forecast") from exc
    except OSError as exc:
        raise ExecutionError(f"forecast: {exc.strerror or exc}", stage="forecast") from exc
    if result.exit_code == 0 and result.status == "complete" and result.rejected_scopes == 0:
        return result
    rejected = []
    try:
        for scope in load_run(forecast_dir).index["scopes"]:
            if scope["status"] == "incomplete":
                # A scope repeats each reason once per series; keep the first of each.
                reasons = "; ".join(dict.fromkeys(scope.get("reasons") or [])) or "no reason recorded"
                rejected.append(f"{scope['scope_type']} {scope['scope_name']!r} ({reasons})")
    except (ForecastInputError, ForecastExecutionError):
        pass
    raise ExecutionError(
        f"forecast did not complete (status {result.status!r}, exit {result.exit_code}, "
        f"{result.rejected_scopes} rejected scope(s)): " + (", ".join(rejected) or "see forecast run"),
        stage="forecast",
    )


def _run_cost_stage(
    forecast_dir: Path, preset: PresetSpec, scenario: Path | None, cost_dir: Path,
    catalog: Catalog | None,
) -> None:
    # TODO(#85): the cost run reads the live pricing catalog unless one is passed. An org
    # dataset pins its catalog under <org-demo>/pricing/, so the two agree only when the
    # dataset and this run come from the same refresh.
    request = CostRequest(scenario=scenario, catalog=catalog)
    try:
        result = run_cost(forecast_dir, request, cost_dir)
    except CostInputError as exc:
        raise InputError(f"cost run {preset.id!r}: {exc}", stage="cost") from exc
    except CostExecutionError as exc:
        raise ExecutionError(f"cost run {preset.id!r}: {exc}", stage="cost") from exc
    except OSError as exc:
        raise ExecutionError(
            f"cost run {preset.id!r}: {exc.strerror or exc}", stage="cost"
        ) from exc
    problems = []
    if result.exit_code != 0 or result.status != "complete":
        problems.append(f"status {result.status!r}, exit {result.exit_code}")
    if result.unpriced_models:
        problems.append(f"unpriced model(s) {result.unpriced_models}")
    not_complete = [
        f"{s['scope_type']} {s['scope_name']!r} ({s['status']})"
        for s in result.scopes
        if s["status"] != "complete"
    ]
    if not_complete:
        problems.append("scope(s) not complete: " + ", ".join(not_complete))
    if problems:
        raise ExecutionError(f"cost run {preset.id!r} did not complete: " + "; ".join(problems), stage="cost")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run_dashboard_forecast(
    series_dir: Path,
    presets_path: Path,
    output_dir: Path,
    *,
    forecast_template: Path | None = None,
    force: bool = False,
    catalog: Catalog | None = None,
) -> DashboardRunResult:
    """Run one forecast and one cost run per preset over a Layer 1 series dataset.

    The origin follows D1 (`resolve_origin` of the manifest's `end_date`); the forecast
    covers the two calendar months after it, and each scenario takes effect the day after
    the data ends. Everything must complete: any other outcome raises `InputError` or
    `ExecutionError` (see the module docstring for the categories and for what is left
    behind). `catalog` pins a pricing catalog version; `None` reads the live one. `force`
    replaces an earlier run in a non-empty `output_dir`; the presets, dataset and template
    are read first, so an unreadable or malformed one does not cost the earlier run (an
    input only the forecaster or the cost module rejects still does, see the module
    docstring).
    """
    log = get_logger(__name__)
    try:
        series_dir = Path(series_dir).resolve()
        output_dir = Path(output_dir).resolve()
    except (OSError, RuntimeError) as exc:  # e.g. a symlink loop
        raise InputError(f"cannot resolve series or output path: {exc}") from exc
    template = Path(forecast_template) if forecast_template is not None else DEFAULT_FORECAST_TEMPLATE

    presets = load_presets(Path(presets_path))
    series = _read_series(series_dir)
    data_through = series.end
    origin = resolve_origin(data_through)
    _check_history(series, origin)
    template_config = _load_template(template)
    _prepare_output(output_dir, series_dir, force=force)

    log.info(
        "dashboard.run.started",
        series_dir=str(series_dir),
        output_dir=str(output_dir),
        org_id=series.org_id,
        data_through=data_through.isoformat(),
        origin=origin.isoformat(),
        presets=[p.id for p in presets],
        scope_count=len(series.scopes),
    )

    config_path = output_dir / "forecast.yaml"
    _write_forecast_yaml(template_config, config_path, series_dir, series)

    forecast_dir = output_dir / "forecast"
    forecast_result = _run_forecast_stage(config_path, origin, forecast_dir)
    log.info(
        "dashboard.forecast.completed",
        forecast_dir=str(forecast_dir),
        scope_count=forecast_result.completed_scopes,
    )

    effective_from = data_through + timedelta(days=1)
    cost_dirs: dict[str, Path] = {}
    for preset in presets:
        scenario = None
        if not preset.is_baseline:
            scenario = write_scenario(
                preset, effective_from, output_dir / "scenarios" / f"{preset.id}.yaml"
            )
        cost_dir = output_dir / "cost" / preset.id
        _run_cost_stage(forecast_dir, preset, scenario, cost_dir, catalog)
        cost_dirs[preset.id] = cost_dir
        log.info("dashboard.cost.completed", preset_id=preset.id, cost_dir=str(cost_dir))

    log.info("dashboard.run.completed", output_dir=str(output_dir), preset_count=len(presets))
    return DashboardRunResult(
        data_through=data_through,
        origin=origin,
        org_id=series.org_id,
        output_dir=output_dir,
        forecast_dir=forecast_dir,
        presets=presets,
        cost_dirs=MappingProxyType(cost_dirs),
        scopes=series.scopes,
    )
