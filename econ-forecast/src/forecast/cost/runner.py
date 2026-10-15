"""End-to-end cost coordination and completed-artifact publication (design §3.2).

`run_cost` ties `request.py` (forecast run + dataset + scenario + one rate sheet),
`scenario.py` (token mutation, scenario runs only), `kernel.py` (pricing),
`summaries.py` (the stdout summary's per-period figures), and `artifacts.py`
(atomic write) together -- none of those modules import this one, so a caller that
only needs the library (`load_request`, `price_one_series`, `load_cost_run`, ...)
never pays for this orchestration layer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import shutil

from common.msging.log import get_logger

from .artifacts import write_cost_run
from .errors import InputError
from .kernel import ScopeCost, SeriesCost, SeriesInput, price_one_series, price_scope
from .request import CostRequest, ResolvedRequest, load_request
from .scenario import apply_scenario
from .schemas import HALF_LIFE_DAYS, OWN_MIX_HALF_WEIGHT_DAYS
from .summaries import period_summary_entries

try:
    from .presentation import render_report
except ImportError:
    # T11 (presentation.py) may not have landed yet; a run still completes, just
    # without report.md/charts/. `report_renderer=None` skips that step in
    # `write_cost_run` entirely (see its own docstring).
    render_report = None


@dataclass
class CostResult:
    """§3.2's JSON summary: scenario, per-scope status, expected USD per period."""

    status: str  # "valid" | "invalid" (validate-only) | "complete" | "incomplete"
    exit_code: int
    scenario: str
    output_dir: Path | None
    scopes: list[dict]
    unpriced_models: list[str]

    def to_dict(self) -> dict:
        value = asdict(self)
        value["output_dir"] = str(self.output_dir) if self.output_dir else None
        return value


def _force_clear_output(path: Path, *, protected: tuple[Path, ...] = (), log=None) -> None:
    """Remove an existing output path before a forced recreate (mirrors
    `forecasting.runner._force_clear_output`): refuses unless `path` resolves to a
    plain directory that is not `/`, the caller's home, cwd, and is neither equal to
    nor an ancestor of any path in `protected` (this run's forecast run + dataset
    directories). Deletion is confined to exactly the resolved directory."""
    if path.is_symlink():
        raise InputError(f"output path is a symlink; refusing --force: {path}")
    resolved = path.resolve()
    if resolved.exists() and not resolved.is_dir():
        raise InputError(f"output path exists and is not a directory; refusing --force: {resolved}")
    guarded = {Path("/"), Path.home().resolve(), Path.cwd().resolve()}
    if resolved in guarded:
        raise InputError(f"refusing to --force a protected system path: {resolved}")
    for raw in protected:
        try:
            guard = Path(raw).resolve()
        except OSError:
            continue
        if resolved == guard or resolved in guard.parents:
            raise InputError(
                f"refusing to --force an output path that is, or contains, a required input: {resolved}"
            )
    if log:
        log.info("cost.force_recreate", output_dir=str(resolved))
    shutil.rmtree(resolved)


def _series_inputs(resolved: ResolvedRequest) -> list[SeriesInput]:
    """The run's series, forecast token paths unless a scenario mutates them
    (§2.2: "Scenario: tokens' = forecast paths ...; price tokens' with the baseline
    logic" -- `scenario.apply_scenario` does exactly that swap, nothing here does)."""
    if resolved.scenario is not None:
        return apply_scenario(resolved)
    saved = resolved.forecast_run
    inputs = []
    for entry in saved.index["series"]:
        tokens = saved.load_paths(entry) if entry["status"] in ("qualified", "inactive") else None
        inputs.append(
            SeriesInput(
                org_id=entry["org_id"],
                scope_type=entry["scope_type"],
                scope_id=entry["scope_id"],
                scope_name=entry["scope_name"],
                model=entry["model"],
                status=entry["status"],
                tokens=tokens,
                assumptions=tuple(entry["assumptions"]),
                reasons=tuple(entry["reasons"]),
            )
        )
    return inputs


def _price_all(resolved: ResolvedRequest) -> tuple[list[SeriesCost], list[ScopeCost]]:
    saved = resolved.forecast_run
    series_costs = [
        price_one_series(
            series, resolved.dataset, resolved.rate_sheet, resolved.dates,
            saved.origin, resolved.history_lookback_days,
        )
        for series in _series_inputs(resolved)
    ]
    by_scope: dict[tuple[str, str], list[SeriesCost]] = {}
    for s in series_costs:
        by_scope.setdefault((s.scope_type, s.scope_id), []).append(s)
    scope_costs = [
        price_scope(
            resolved.org_id, scope["scope_type"], scope["scope_id"], scope["scope_name"],
            by_scope.get((scope["scope_type"], scope["scope_id"]), []),
            resolved.dates, resolved.samples,
        )
        for scope in saved.index["scopes"]
    ]
    return series_costs, scope_costs


def _request_doc(forecast_run_dir: Path, output_dir: Path, resolved: ResolvedRequest) -> dict:
    saved = resolved.forecast_run
    forecast_manifest_sha256 = hashlib.sha256((saved.root / "manifest.json").read_bytes()).hexdigest()
    return {
        "org_id": resolved.org_id,
        "forecast_run": {
            "name": Path(forecast_run_dir).name,
            # Relative to the cost run directory (design §14.3), so a workspace
            # can move as a whole; the forecast run's own dataset link (already
            # resolved absolute by `forecasting.artifacts.load_run`) is what
            # every consumer here (this module, cost/request.py, utils/actuals.py)
            # actually needs, not a second copy of it.
            "path": os.path.relpath(Path(forecast_run_dir).resolve(), start=Path(output_dir).resolve()),
            "manifest_sha256": forecast_manifest_sha256,
        },
        "scenario": resolved.scenario_name,
        "periods": [
            {"type": p["type"], "start": p["start"].isoformat(), "end": p["end"].isoformat()}
            for p in resolved.periods
        ],
        "settings": {
            "half_life_days": HALF_LIFE_DAYS,
            "own_mix_half_weight_days": OWN_MIX_HALF_WEIGHT_DAYS,
            "max_age_days": resolved.history_lookback_days,
        },
    }


def _scope_summary(scope_costs: list[ScopeCost], period_entries: list[dict]) -> list[dict]:
    summary = []
    for sc in scope_costs:
        expected_by_period = {
            e["period_type"]: e["expected"]
            for e in period_entries
            if e["scope_type"] == sc.scope_type and e["scope_name"] == sc.scope_name and e["model"] is None
        }
        summary.append(
            {
                "scope_type": sc.scope_type,
                "scope_name": sc.scope_name,
                "status": sc.status,
                "expected_usd": expected_by_period,
            }
        )
    return summary


def run_cost(
    forecast_run_dir: Path,
    request: CostRequest,
    output_dir: Path | None = None,
    *,
    validate_only: bool = False,
    force: bool = False,
) -> CostResult:
    """Price a forecast run into USD paths (§3.2). `--validate-only`: reads inputs,
    builds the rate sheet, lists unpriced models, writes nothing (exit 2 if any)."""
    log = get_logger(__name__)
    resolved = load_request(forecast_run_dir, request)
    unpriced_models = sorted(
        m.usage_model_id for m in resolved.rate_sheet.models if m.status != "matched"
    )
    log.info(
        "cost.request.resolved",
        scenario=resolved.scenario_name,
        model_count=len(resolved.rate_sheet.models),
        unpriced_count=len(unpriced_models),
    )

    if validate_only:
        status = "invalid" if unpriced_models else "valid"
        log.info("cost.validate.completed", status=status, unpriced_models=unpriced_models)
        return CostResult(
            status=status,
            exit_code=2 if unpriced_models else 0,
            scenario=resolved.scenario_name,
            output_dir=None,
            scopes=[],
            unpriced_models=unpriced_models,
        )

    if output_dir is None:
        raise InputError("output_dir is required unless validate_only")
    out = Path(output_dir)
    if out.exists() or out.is_symlink():
        if not force:
            raise InputError("output path already exists")
        _force_clear_output(
            out,
            protected=(resolved.forecast_run.root, Path(resolved.forecast_run.dataset["path"])),
            log=log,
        )

    series_costs, scope_costs = _price_all(resolved)
    request_doc = _request_doc(forecast_run_dir, out, resolved)
    scenario_yaml_bytes = (
        Path(request.scenario).read_bytes() if request.scenario is not None else None
    )

    write_cost_run(
        out,
        request_doc=request_doc,
        sheet=resolved.rate_sheet,
        dataset=resolved.dataset,
        org_id=resolved.org_id,
        dates=resolved.dates,
        samples=resolved.samples,
        periods=resolved.periods,
        series_costs=series_costs,
        scope_costs=scope_costs,
        origin=resolved.forecast_run.origin,
        max_age_days=resolved.history_lookback_days,
        scenario_yaml_bytes=scenario_yaml_bytes,
        report_renderer=render_report,
    )

    period_entries = period_summary_entries(series_costs, scope_costs, resolved.dates, resolved.periods)
    incomplete = any(sc.status == "incomplete" for sc in scope_costs)
    status = "incomplete" if incomplete else "complete"
    log.info("cost.write.completed", status=status, output_dir=str(out))
    return CostResult(
        status=status,
        exit_code=2 if incomplete else 0,
        scenario=resolved.scenario_name,
        output_dir=out,
        scopes=_scope_summary(scope_costs, period_entries),
        unpriced_models=unpriced_models,
    )
