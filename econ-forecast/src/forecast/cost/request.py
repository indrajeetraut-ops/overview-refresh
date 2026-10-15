"""The cost run's inputs: forecast run, dataset check, scenario load + validation
(design §3.1, §6.4).

`CostRequest` is the small public input (§3.2); `load_request` resolves it into
everything the kernel/mix/rates stages need, raising this module's own `InputError`/
`ExecutionError` rather than forecasting's -- the two modules' error types are
distinct on purpose (mirrors forecasting's own boundary), so a cost caller's exit-code
handling only ever needs to know about `forecast.cost.errors`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
from pathlib import Path

import yaml
from pydantic import ValidationError

from common.model_pricing import Catalog, RateSheet

from forecast.forecasting.artifacts import SavedRun, load_run
from forecast.forecasting.dataset import PreparedDataset, load_dataset
from forecast.forecasting.errors import ExecutionError as ForecastExecutionError
from forecast.forecasting.errors import InputError as ForecastInputError

from .errors import ExecutionError, InputError
from .rates import build_rate_sheet, model_ids_for_run
from .schemas import ScenarioConfig


@dataclass(frozen=True)
class CostRequest:
    """The cost run's only free input (§3.2): which scenario to price, if any.
    `None` prices the baseline (the forecast's own token paths, unchanged).
    `catalog` pins a `model_pricing` catalog version (Python API only, no CLI flag)
    -- `None` reads the live catalog at call time, same as `rate_sheet()`'s own
    default; tests pass a catalog built from fixtures instead."""

    scenario: Path | None = None
    catalog: Catalog | None = None

    def __post_init__(self):
        if self.scenario is not None and not isinstance(self.scenario, Path):
            raise InputError("scenario must be a Path or None")


@dataclass(frozen=True)
class ResolvedRequest:
    """Everything the pricing stages need, already validated. `scenario` is `None`
    for a baseline run; `scenario_name` is `"baseline"` in that case (§8.2
    `request.json.scenario`). `rate_sheet` covers every forecast model plus every
    scenario destination model, built exactly once -- both scenario validation (the
    `to`-matched check, §6.4) and pricing (§2.2) read this same sheet."""

    forecast_run: SavedRun
    dataset: PreparedDataset
    org_id: str
    dates: tuple[date, ...]
    samples: int
    periods: tuple[dict, ...]
    history_lookback_days: int
    scenario: ScenarioConfig | None
    scenario_name: str
    rate_sheet: RateSheet


def _load_forecast_run(path: Path) -> SavedRun:
    try:
        return load_run(path)
    except ForecastInputError as exc:
        raise InputError(str(exc)) from exc
    except ForecastExecutionError as exc:
        raise ExecutionError(str(exc)) from exc


def _load_prepared_dataset(path: Path) -> PreparedDataset:
    try:
        return load_dataset(path)
    except ForecastInputError as exc:
        raise InputError(str(exc)) from exc
    except ForecastExecutionError as exc:
        raise ExecutionError(str(exc)) from exc


def _series_exists(saved: SavedRun, org_id: str, scope_type: str, scope_id: str, model: str) -> bool:
    return any(
        e["org_id"] == org_id
        and e["scope_type"] == scope_type
        and e["scope_id"] == scope_id
        and e["model"] == model
        for e in saved.index["series"]
    )


def _agent_has_usage(
    dataset: PreparedDataset, org_id: str, scope_type: str, scope_id: str, agent_type: str
) -> bool:
    # usage keys: (org_id, scope_type, scope_id, model, date, agent_type, category)
    return any(
        key[0] == org_id and key[1] == scope_type and key[2] == scope_id and key[5] == agent_type
        for key, value in dataset.usage.items()
        if value > 0
    )


def _find_scope_entry(saved: SavedRun, org_id: str, scope_type: str, name: str | None) -> dict:
    if scope_type == "organization":
        matches = [
            s
            for s in saved.index["scopes"]
            if s["org_id"] == org_id and s["scope_type"] == "organization"
        ]
    else:
        matches = [
            s
            for s in saved.index["scopes"]
            if s["org_id"] == org_id
            and s["scope_type"] == scope_type
            and s["scope_name"] == name
        ]
    if len(matches) != 1:
        raise InputError(
            f"scenario scope not found in the forecast run: {scope_type} {name!r}"
        )
    return matches[0]


def _validate_change(
    change,
    saved: SavedRun,
    dataset: PreparedDataset,
    org_id: str,
    scope_entry: dict,
    dates: tuple[date, ...],
    sheet: RateSheet,
) -> None:
    scope_type, scope_id = scope_entry["scope_type"], scope_entry["scope_id"]
    if change.model_transfer is not None:
        transfer = change.model_transfer
        if not _series_exists(saved, org_id, scope_type, scope_id, transfer.from_):
            raise InputError(
                f"model_transfer.from is not a series in the declared scope: {transfer.from_!r}"
            )
        priced = next((m for m in sheet.models if m.usage_model_id == transfer.to), None)
        if priced is None or priced.status != "matched":
            raise InputError(
                f"model_transfer.to is not matched in the rate sheet: {transfer.to!r}"
            )
        effective_from = transfer.effective_from
    elif change.agent_transfer is not None:
        transfer = change.agent_transfer
        if not _agent_has_usage(dataset, org_id, scope_type, scope_id, transfer.from_):
            raise InputError(
                f"agent_transfer.from has no usage in the declared scope: {transfer.from_!r}"
            )
        if not _agent_has_usage(dataset, org_id, "organization", org_id, transfer.to):
            raise InputError(
                f"agent_transfer.to has no usage in the Organization: {transfer.to!r}"
            )
        effective_from = transfer.effective_from
    else:
        effective_from = change.growth.effective_from
    if effective_from is not None and not (dates[0] <= effective_from <= dates[-1]):
        raise InputError("effective_from must fall within the forecast dates")


def _parse_scenario(scenario_path: Path) -> ScenarioConfig:
    scenario_path = Path(scenario_path)
    try:
        raw = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise InputError(f"cannot read scenario config: {scenario_path.name}") from exc
    if not isinstance(raw, dict):
        raise InputError("scenario config must be a mapping")
    try:
        config = ScenarioConfig.model_validate(raw)
    except ValidationError as exc:
        raise InputError(f"invalid scenario config {scenario_path.name}: {exc}") from exc
    if config.name != scenario_path.stem:
        raise InputError("scenario name must match its file name")
    return config


def _destination_model_ids(config: ScenarioConfig, dataset: PreparedDataset, org_id: str) -> set[str]:
    """Every model the rate sheet needs beyond the forecast's own (§6.4): a
    `model_transfer`'s named `to`; an `agent_transfer`'s `to` agent's own models
    (§6.2's org-level split can land on any of them, so all are candidates)."""
    ids: set[str] = set()
    for change in config.changes:
        if change.model_transfer is not None:
            ids.add(change.model_transfer.to)
        elif change.agent_transfer is not None:
            to_agent = change.agent_transfer.to
            for model, agents in dataset.models.get(org_id, {}).items():
                if to_agent in agents:
                    ids.add(model)
    return ids


def _validate_scenario(
    config: ScenarioConfig,
    saved: SavedRun,
    dataset: PreparedDataset,
    org_id: str,
    sheet: RateSheet,
    dates: tuple[date, ...],
) -> ScenarioConfig:
    scope_entry = _find_scope_entry(saved, org_id, config.scope.type, config.scope.name)
    for change in config.changes:
        _validate_change(change, saved, dataset, org_id, scope_entry, dates, sheet)
    return config


def load_request(forecast_run_dir: Path, request: CostRequest) -> ResolvedRequest:
    """Resolve a `CostRequest` against a completed forecast run: validated forecast
    run + dataset + (optional) scenario + one rate sheet, ready for mix/rates/kernel
    (§3.1)."""
    saved = _load_forecast_run(forecast_run_dir)
    if (
        saved.dataset is None
        or saved.origin is None
        or saved.periods is None
        or saved.history_lookback_days is None
    ):
        raise InputError(
            "forecast run predates recorded dataset identity (T2); re-run the forecast"
        )
    org_ids = {scope["org_id"] for scope in saved.index["scopes"]}
    if len(org_ids) != 1:
        raise InputError("forecast run must model exactly one Organization (design §1)")
    org_id = next(iter(org_ids))

    dataset_root = Path(saved.dataset["path"])
    dataset = _load_prepared_dataset(dataset_root)
    actual_hash = hashlib.sha256(
        (dataset_root.resolve() / "manifest.json").read_bytes()
    ).hexdigest()
    if actual_hash != saved.dataset["manifest_sha256"]:
        raise InputError(
            "dataset does not match the forecast run's recorded manifest hash; re-run the forecast"
        )

    dates = tuple(date.fromisoformat(value) for value in saved.index["dates"])
    raw_scenario = _parse_scenario(request.scenario) if request.scenario is not None else None
    model_ids = set(model_ids_for_run(saved.index["series"]))
    if raw_scenario is not None:
        model_ids |= _destination_model_ids(raw_scenario, dataset, org_id)
    sheet = build_rate_sheet(org_id, sorted(model_ids), dates[0], dates[-1], catalog=request.catalog)

    scenario_config = None
    scenario_name = "baseline"
    if raw_scenario is not None:
        scenario_config = _validate_scenario(raw_scenario, saved, dataset, org_id, sheet, dates)
        scenario_name = scenario_config.name

    return ResolvedRequest(
        forecast_run=saved,
        dataset=dataset,
        org_id=org_id,
        dates=dates,
        samples=len(saved.index["sample_ids"]),
        periods=saved.periods,
        history_lookback_days=saved.history_lookback_days,
        scenario=scenario_config,
        scenario_name=scenario_name,
        rate_sheet=sheet,
    )
