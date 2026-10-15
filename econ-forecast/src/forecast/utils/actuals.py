"""Reload the real observed series a forecast run consumed, for overlay plotting.

The forecast run does not store the observed series itself; it records where its
input came from. The dataset *path* comes from `SavedRun.dataset["path"]`
(`request.json` -> `dataset.path`, already resolved to an absolute path against the
run root by `forecasting.artifacts.load_run`, design §14.3 -- this is what keeps
actuals sourcing correct after a workspace and its dataset move together). The
history/allocation windowing numbers come from `resolved_config.yaml`. Per-file
hashes for the integrity check come from `manifest.json` -> `input_sources`. This
module reuses `forecasting.history.build_histories` -- the same function the
forecaster used -- so the plotted "actuals" line is exactly what the forecast saw,
not a re-derivation. It never raises for an absent/unreadable/incompatible dataset:
that is reported back as `source="unavailable"` with a human-readable note.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from forecast.forecasting.dataset import load_dataset
from forecast.forecasting.errors import InputError
from forecast.forecasting.history import SeriesHistory, build_histories


@dataclass(frozen=True)
class ActualsSource:
    """Where the actuals came from, and whether they can be trusted as-fitted."""

    source: str  # "auto" | "override" | "unavailable"
    dataset_dir: str | None
    origin: str | None
    hash_mismatch: bool
    note: str | None


_EMPTY = ActualsSource(source="unavailable", dataset_dir=None, origin=None,
                       hash_mismatch=False, note=None)


def _read_yaml_mapping(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise InputError(f"{path.name} must contain a YAML mapping")
    return value


def _read_json_mapping(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise InputError(f"{path.name} must contain a JSON object")
    return value


def load_actuals(
    saved_run, *, dataset_override: Path | None = None
) -> tuple[ActualsSource, dict[tuple[str, str, str], SeriesHistory]]:
    """Resolve and reload the run's observed input, aggregated to scope x model.

    Returns `(ActualsSource, lookup)`. `lookup` keys are
    `(scope_type, scope_id, model)` and its values are `SeriesHistory` (only the
    lookback-bounded window the forecaster actually consumed). `lookup` is empty
    whenever `source == "unavailable"`.
    """
    root = Path(saved_run.root)
    source_kind = "override" if dataset_override is not None else "auto"
    try:
        resolved = _read_yaml_mapping(root / "resolved_config.yaml")
        request_doc = _read_json_mapping(root / "request.json")
        history_cfg = resolved["history"]
        allocation_cfg = resolved["allocation"]
        origin = date.fromisoformat(request_doc["origin"])
        if dataset_override is not None:
            dataset_dir = Path(dataset_override)
        elif saved_run.dataset is not None:
            # Already resolved to an absolute path against the run root by `load_run`.
            dataset_dir = Path(saved_run.dataset["path"])
        else:
            # Pre-existing run that predates dataset-path recording: nothing to load.
            raise InputError("forecast run does not record its dataset path")
        lookback = history_cfg["lookback_days"]
        minimum = history_cfg["minimum_days"]
        active_days = history_cfg["minimum_active_days"]
        allocation_days = allocation_cfg["lookback_days"]
    except (OSError, UnicodeError, yaml.YAMLError, json.JSONDecodeError,
           KeyError, TypeError, ValueError, InputError) as exc:
        return ActualsSource(
            source="unavailable", dataset_dir=None, origin=None, hash_mismatch=False,
            note=f"forecast run does not record enough to reload actuals: {exc}",
        ), {}
    try:
        data = load_dataset(dataset_dir)
    except InputError as exc:
        return ActualsSource(
            source="unavailable", dataset_dir=str(dataset_dir), origin=origin.isoformat(),
            hash_mismatch=False, note=f"actuals dataset unavailable: {exc}",
        ), {}
    hash_mismatch = False
    if source_kind == "auto":
        recorded = saved_run.manifest.get("input_sources")
        if isinstance(recorded, dict) and recorded:
            hash_mismatch = recorded != data.hashes
    needed = {(entry["org_id"], entry["scope_type"], entry["scope_id"])
              for entry in saved_run.index.get("series", [])}
    selected_metas = [data.scopes[key] for key in needed if key in data.scopes]
    try:
        histories, _ = build_histories(
            data, selected_metas, origin,
            lookback=lookback, minimum=minimum, active_days=active_days,
            allocation_days=allocation_days,
        )
    except InputError as exc:
        return ActualsSource(
            source="unavailable", dataset_dir=str(dataset_dir), origin=origin.isoformat(),
            hash_mismatch=hash_mismatch, note=f"cannot rebuild observed history: {exc}",
        ), {}
    lookup = {(history.scope.scope_type, history.scope.scope_id, history.model): history
              for history in histories}
    note = "actuals may differ from the fitted input (hash mismatch)" if hash_mismatch else None
    return ActualsSource(
        source=source_kind, dataset_dir=str(dataset_dir), origin=origin.isoformat(),
        hash_mismatch=hash_mismatch, note=note,
    ), lookup
