"""Shared driver for the two plot CLIs (`cli_plot_forecast.py`, `cli_plot_cost.py`).

Both draw into the run's own `<run>/charts/` (design `plot-forecast-enhancements-design.md`
§7): refuse an existing `charts/` unless `--force`; expand a `plot.yaml` entry whose
`scope_name` was omitted into one stem per matching scope in the run (needs the run's
own scope list, so it happens here, not in `plot_config.py`); detect stem collisions
across the whole expanded set and fail loud, before anything is written; otherwise a
per-stem failure (unknown scope/model, no representable paths) is captured into that
stem's own `result.json` record and never aborts the batch (design §13 handshake:
status return, not exception-type dispatch -- raise before the loop for a structural
problem, capture into the record for anything else).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import date, datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from common.msging.log import get_logger
from forecast.cost.presentation import (
    _breakdown_matplotlib,
    _breakdown_plotly,
    _daily_expected_by_scope_model,
    _fan_matplotlib,
    _fan_plotly,
    _scope_label,
)
from forecast.cost.errors import ExecutionError as CostExecutionError, InputError as CostInputError
from forecast.forecasting.errors import ExecutionError, InputError

from .actuals import load_actuals
from .plot_config import CostEntry, Defaults, ForecastEntry, stem
from .plot_forecast import build_figure, build_matplotlib_figure, export_html, export_png

_LOG = get_logger("forecast.utils.plot")
_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Shared: charts/ contract, shared plotly.min.js, JSON writing
# ---------------------------------------------------------------------------


def _json_write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def _versions() -> dict:
    result = {}
    for package in ("numpy", "plotly", "matplotlib"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "unavailable"
    return result


def _prepare_charts_dir(run_root: Path, *, force: bool) -> Path:
    """`<run>/charts/`: refuse an existing one unless `--force` (then replace it
    outright); never touches anything else in the run."""
    charts_dir = run_root / "charts"
    if charts_dir.exists() or charts_dir.is_symlink():
        if not force:
            raise InputError(f"charts/ already exists in {run_root}; use --force to replace it")
        if charts_dir.is_symlink():
            raise InputError("charts/ is a symlink; refusing --force")
        if not charts_dir.is_dir():
            raise InputError(f"charts/ exists and is not a directory: {charts_dir}")
        shutil.rmtree(charts_dir)
        _LOG.info("plot.charts_dir.replaced", charts_dir=str(charts_dir))
    charts_dir.mkdir(parents=True)
    return charts_dir


def _ensure_plotly_js(charts_dir: Path, plotly_js: Path | None) -> str:
    """Returns the relative path every chart's `include_plotlyjs` should use.
    `plotly_js` (already resolved absolute by `plot_config`), when given, is
    written from the installed `plotly`'s own `get_plotlyjs()` if it does not
    already exist there (design §8: "either file is written ... when
    missing"); otherwise one shared `charts/plotly.min.js` is written the same
    way -- exactly one copy is ever written, even when many charts are drawn,
    and an existing file (either location) is left untouched."""
    target = plotly_js if plotly_js is not None else charts_dir / "plotly.min.js"
    if not target.exists():
        from plotly.offline import get_plotlyjs

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(get_plotlyjs(), encoding="utf-8")
        _LOG.info("plot.plotly_js.written", target=str(target))
    if plotly_js is not None:
        return Path(os.path.relpath(plotly_js, start=charts_dir)).as_posix()
    return "plotly.min.js"


def _relative_to(path, charts_dir: Path) -> str | None:
    """A recorded path relative to `charts/` (design §14.3: links are relative so a
    workspace can move); `None` stays `None`."""
    return None if path is None else Path(os.path.relpath(Path(path).resolve(), start=charts_dir.resolve())).as_posix()


def _check_no_stem_collisions(stems: list[str]) -> None:
    seen: set[str] = set()
    for entry_stem in stems:
        if entry_stem in seen:
            raise InputError(f"plot entries collide on output filename stem {entry_stem!r}")
        seen.add(entry_stem)


def _render(figure_html, figure_png, charts_dir: Path, file_stem: str, fmt: str,
           plotlyjs_relative: str) -> dict:
    outputs: dict[str, str] = {}
    if fmt in ("html", "both"):
        target = charts_dir / f"{file_stem}.html"
        export_html(figure_html(), target, plotlyjs=plotlyjs_relative)
        outputs["html"] = target.name
    if fmt in ("png", "both"):
        target = charts_dir / f"{file_stem}.png"
        export_png(figure_png(), target)
        outputs["png"] = target.name
    return outputs


def _overall_status(succeeded: int, failed: int, any_partial: bool) -> str:
    if failed == 0 and not any_partial:
        return "complete"
    if succeeded == 0:
        return "failed"
    return "partial"


# ---------------------------------------------------------------------------
# Forecast: plot_forecast's `forecast:` section
# ---------------------------------------------------------------------------


def _forecast_scope_names(saved_run, scope_type: str) -> list[str]:
    return sorted({scope["scope_name"] for scope in saved_run.index.get("scopes", [])
                  if scope["scope_type"] == scope_type})


def _expand_forecast_entries(saved_run, org_id: str, entries: list[ForecastEntry],
                             defaults: Defaults) -> list[dict]:
    """One dict per stem-producing unit: `scope_type`, `scope_name` (the run's
    own recorded name; `None` for organization), `identity` (what `stem()`
    slugs -- `org_id` for organization, else `scope_name`), `resolved`. An
    entry whose scope_type has no scopes at all in this run to expand into
    contributes nothing (not an error: nothing was requested that exists)."""
    expanded = []
    for entry in entries:
        resolved = entry.resolve(defaults)
        if entry.scope_type == "organization":
            scope_names: list[str | None] = [None]
        elif entry.scope_name is not None:
            scope_names = [entry.scope_name]
        else:
            scope_names = _forecast_scope_names(saved_run, entry.scope_type)
        for scope_name in scope_names:
            identity = org_id if entry.scope_type == "organization" else scope_name
            expanded.append({
                "scope_type": entry.scope_type, "scope_name": scope_name,
                "identity": identity, "resolved": resolved,
            })
    return expanded


def run_forecast_plots(
    saved_run, entries: list[ForecastEntry], defaults: Defaults, *,
    plotly_js: Path | None = None, dataset_override: Path | None = None, force: bool = False,
) -> tuple[dict, str]:
    """Draw every `forecast:` entry against one saved forecast run. Returns
    `(result, status)`; raises `InputError`/`ExecutionError` for a structural
    problem (nothing written in that case)."""
    started = time.perf_counter()
    if not entries:
        raise InputError("at least one forecast plot entry is required")

    org_ids = {item["org_id"] for item in saved_run.index.get("series", [])}
    if len(org_ids) != 1:
        raise InputError("run must contain exactly one Organization to derive the plot Organization")
    org_id = next(iter(org_ids))

    expanded = _expand_forecast_entries(saved_run, org_id, entries, defaults)
    units = [{**item, "stem": stem(item["scope_type"], item["identity"], "models", "tokens")}
             for item in expanded]
    _check_no_stem_collisions([unit["stem"] for unit in units])

    _LOG.info("plot.forecast.read", artifact=str(saved_run.root), entry_count=len(entries),
             stem_count=len(units))
    actuals_source, actuals_lookup = load_actuals(saved_run, dataset_override=dataset_override)
    _LOG.info("plot.actuals.resolved", source=actuals_source.source,
             dataset=actuals_source.dataset_dir, hash_mismatch=actuals_source.hash_mismatch,
             note=actuals_source.note)

    charts_dir = _prepare_charts_dir(saved_run.root, force=force)
    plotlyjs_relative = _ensure_plotly_js(charts_dir, plotly_js)

    plot_records = []
    index_records = []
    succeeded = failed = 0
    any_partial = False
    for unit in units:
        resolved = unit["resolved"]
        scope_type, scope_name = unit["scope_type"], unit["scope_name"]
        file_stem = unit["stem"]
        _LOG.info("plot.entry.started", stem=file_stem, scope_type=scope_type,
                 scope_name=scope_name, format=resolved.format)
        record = {
            "scope_type": scope_type, "scope_name": scope_name, "view": "models",
            "measure": "tokens", "stem": file_stem, "model": None,
            "models": resolved.models, "samples": resolved.samples,
            "band": list(resolved.band.as_tuple()), "format": resolved.format,
        }
        kwargs = dict(
            saved_run=saved_run, org_id=org_id, scope_type=scope_type, scope_name=scope_name,
            models=resolved.models, samples=resolved.samples, band=resolved.band.as_tuple(),
            actuals=actuals_lookup, actuals_source=actuals_source.source,
            actuals_note=actuals_source.note,
        )
        summary_box: dict = {}

        def _html():
            figure, summary = build_figure(**kwargs)
            summary_box["summary"] = summary
            return figure

        def _png():
            figure, summary = build_matplotlib_figure(**kwargs)
            summary_box.setdefault("summary", summary)
            return figure

        try:
            outputs = _render(_html, _png, charts_dir, file_stem, resolved.format, plotlyjs_relative)
        except (InputError, ExecutionError) as exc:
            error_type = "input" if isinstance(exc, InputError) else "execution"
            _LOG.info("plot.entry.failed", stem=file_stem, error_type=error_type, message=str(exc))
            record.update(status="failed", outputs={}, plotted_models=[], unavailable_models=[],
                         actuals_overlaid=False, error={"type": error_type, "message": str(exc)})
            plot_records.append(record)
            failed += 1
            continue
        summary = summary_box["summary"]
        record.update(status=summary["status"], outputs=outputs,
                     plotted_models=summary["plotted_models"],
                     unavailable_models=summary["unavailable_models"],
                     actuals_overlaid=summary.get("actuals_overlaid", False), error=None)
        plot_records.append(record)
        succeeded += 1
        any_partial = any_partial or summary["status"] != "complete"
        index_records.append({
            "scope_type": scope_type, "scope_name": scope_name, "view": "models",
            "measure": "tokens", "model": None,
            "files": [outputs[key] for key in ("html", "png") if key in outputs],
            "status": summary["status"],
        })
        _LOG.info("plot.entry.completed", stem=file_stem, status=summary["status"],
                 outputs=list(outputs.values()))

    overall = _overall_status(succeeded, failed, any_partial)
    elapsed = time.perf_counter() - started
    result = {
        "schema_version": _SCHEMA_VERSION, "operation": "plot", "status": overall,
        "created_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": elapsed,
        "artifact": _relative_to(saved_run.root, charts_dir),
        "forecast_data": {"source": actuals_source.source,
                          "dataset": _relative_to(actuals_source.dataset_dir, charts_dir)},
        "counts": {"requested": len(units), "succeeded": succeeded, "failed": failed},
        "versions": _versions(), "plots": plot_records,
    }
    index = {
        "schema_version": _SCHEMA_VERSION, "operation": "plot",
        "created_at": result["created_at"], "status": overall, "plots": index_records,
    }
    _json_write(charts_dir / "result.json", result)
    _json_write(charts_dir / "index.json", index)
    _LOG.info("plot.forecast.completed", status=overall, requested=len(units),
             succeeded=succeeded, failed=failed, elapsed_seconds=elapsed)
    return result, overall


# ---------------------------------------------------------------------------
# Cost: plot_cost's `cost:` section
# ---------------------------------------------------------------------------


def _cost_scope_names(cost_run, scope_type: str) -> list[str]:
    return sorted({scope["scope_name"] for scope in cost_run.index.get("scopes", [])
                  if scope["scope_type"] == scope_type})


def _cost_scope_entry(cost_run, scope_type: str, scope_name: str) -> dict | None:
    return next((scope for scope in cost_run.index["scopes"]
                if scope["scope_type"] == scope_type and scope["scope_name"] == scope_name), None)


def _cost_models_in_scope(cost_run, scope_type: str, scope_name: str) -> list[str]:
    return sorted({entry["model"] for entry in cost_run.index["series"]
                  if entry["scope_type"] == scope_type and entry["scope_name"] == scope_name
                  and entry["status"] in ("qualified", "inactive")})


def _cost_series_entry(cost_run, scope_type: str, scope_name: str, model: str) -> dict | None:
    return next((entry for entry in cost_run.index["series"]
                if entry["scope_type"] == scope_type and entry["scope_name"] == scope_name
                and entry["model"] == model), None)


def _expand_cost_entries(cost_run, org_id: str, entries: list[CostEntry],
                         defaults: Defaults) -> list[dict]:
    """One dict per stem-producing unit. `per-model` expands to every model
    actually present (qualified/inactive) in the resolved scope (design §7);
    it is not configurable, so there is no `models` field on a cost entry."""
    expanded = []
    for entry in entries:
        resolved = entry.resolve(defaults)
        if entry.scope_type == "organization":
            scope_names = _cost_scope_names(cost_run, "organization")
        elif entry.scope_name is not None:
            scope_names = [entry.scope_name]
        else:
            scope_names = _cost_scope_names(cost_run, entry.scope_type)
        for scope_name in scope_names:
            identity = org_id if entry.scope_type == "organization" else scope_name
            for view in resolved.views:
                if view == "per-model":
                    for model in _cost_models_in_scope(cost_run, entry.scope_type, scope_name):
                        expanded.append({
                            "scope_type": entry.scope_type, "scope_name": scope_name,
                            "identity": identity, "view": model, "model": model,
                            "resolved": resolved,
                        })
                else:
                    expanded.append({
                        "scope_type": entry.scope_type, "scope_name": scope_name,
                        "identity": identity, "view": view, "model": None,
                        "resolved": resolved,
                    })
    return expanded


def run_cost_plots(
    cost_run, entries: list[CostEntry], defaults: Defaults, *,
    plotly_js: Path | None = None, force: bool = False,
) -> tuple[dict, str]:
    """Draw every `cost:` entry against one saved cost run. Returns `(result,
    status)`; raises `InputError`/`ExecutionError` for a structural problem
    (nothing written in that case)."""
    started = time.perf_counter()
    if not entries:
        raise InputError("at least one cost plot entry is required")

    org_id = cost_run.index["org_id"]
    expanded = _expand_cost_entries(cost_run, org_id, entries, defaults)
    units = [{**item, "stem": stem(item["scope_type"], item["identity"], item["view"], "cost")}
             for item in expanded]
    _check_no_stem_collisions([unit["stem"] for unit in units])

    _LOG.info("plot.cost.read", artifact=str(cost_run.root), entry_count=len(entries),
             stem_count=len(units))
    charts_dir = _prepare_charts_dir(cost_run.root, force=force)
    plotlyjs_relative = _ensure_plotly_js(charts_dir, plotly_js)

    dates_iso = cost_run.index["dates"]
    dates_obj = [date.fromisoformat(d) for d in dates_iso]
    breakdown_data = _daily_expected_by_scope_model(cost_run)

    plot_records = []
    index_records = []
    succeeded = failed = 0
    any_partial = False
    for unit in units:
        resolved = unit["resolved"]
        scope_type, scope_name, view, model = (unit["scope_type"], unit["scope_name"],
                                               unit["view"], unit["model"])
        file_stem = unit["stem"]
        _LOG.info("plot.entry.started", stem=file_stem, scope_type=scope_type,
                 scope_name=scope_name, view=view, format=resolved.format)
        record = {
            "scope_type": scope_type, "scope_name": scope_name, "view": view,
            "measure": "cost", "stem": file_stem, "model": model,
            "samples": resolved.samples, "band": list(resolved.band.as_tuple()),
            "format": resolved.format,
        }
        try:
            if view == "breakdown":
                scope = _cost_scope_entry(cost_run, scope_type, scope_name)
                if scope is None or scope["status"] == "incomplete":
                    raise InputError(f"{scope_type} {scope_name!r} is incomplete: no breakdown chart")
                per_model = breakdown_data.get((scope_type, scope_name), {})
                if not per_model:
                    raise InputError(f"{scope_type} {scope_name!r} has no per-model expected cost to break down")
                model_series = {m: [per_model.get(m, {}).get(d, 0.0) for d in dates_iso] for m in per_model}
                title = f"{_scope_label(scope_type, scope_name)} - model breakdown - expected daily cost"
                status = "complete"

                def _html():
                    return _breakdown_plotly(dates_iso, model_series, title)

                def _png():
                    return _breakdown_matplotlib(dates_obj, model_series, title)
            else:
                if model is not None:
                    series_entry = _cost_series_entry(cost_run, scope_type, scope_name, model)
                    if series_entry is None or series_entry["status"] not in ("qualified", "inactive"):
                        raise InputError(f"model {model!r} has no representable cost path in "
                                         f"{scope_type} {scope_name!r}")
                    values = cost_run.paths(series_entry)
                    status = series_entry["status"]
                    title = f"{_scope_label(scope_type, scope_name)} - {model} - {status} - daily cost"
                else:
                    scope = _cost_scope_entry(cost_run, scope_type, scope_name)
                    if scope is None:
                        raise InputError(f"unknown {scope_type} name {scope_name!r} in this cost run")
                    values = cost_run.scope_paths(scope)
                    if values is None:
                        raise InputError(f"{scope_type} {scope_name!r} is incomplete: no total cost")
                    status = scope["status"]
                    title = f"{_scope_label(scope_type, scope_name)} - total - {status} - daily cost"

                def _html():
                    return _fan_plotly(dates_iso, values, title,
                                       band=resolved.band.as_tuple(), samples=resolved.samples)

                def _png():
                    return _fan_matplotlib(dates_obj, values, title,
                                           band=resolved.band.as_tuple(), samples=resolved.samples)

            outputs = _render(_html, _png, charts_dir, file_stem, resolved.format, plotlyjs_relative)
        except (InputError, ExecutionError, CostInputError, CostExecutionError) as exc:
            # `cost_run.paths`/`scope_paths` raise the cost module's own error
            # types (distinct from forecasting's, by design); a per-entry
            # failure here must never abort the batch either way.
            error_type = "input" if isinstance(exc, (InputError, CostInputError)) else "execution"
            _LOG.info("plot.entry.failed", stem=file_stem, error_type=error_type, message=str(exc))
            record.update(status="failed", outputs={}, error={"type": error_type, "message": str(exc)})
            plot_records.append(record)
            failed += 1
            continue
        record.update(status=status, outputs=outputs, error=None)
        plot_records.append(record)
        succeeded += 1
        # "conditional" (a mixed qualified/inactive scope) is the only cost
        # status that marks the batch partial; a fully "inactive" scope/series
        # is a valid, complete chart (mirrors forecast's own convention).
        any_partial = any_partial or status not in ("complete", "qualified", "inactive")
        index_records.append({
            "scope_type": scope_type, "scope_name": scope_name, "view": view,
            "measure": "cost", "model": model,
            "files": [outputs[key] for key in ("html", "png") if key in outputs],
            "status": status,
        })
        _LOG.info("plot.entry.completed", stem=file_stem, status=status, outputs=list(outputs.values()))

    overall = _overall_status(succeeded, failed, any_partial)
    elapsed = time.perf_counter() - started
    result = {
        "schema_version": _SCHEMA_VERSION, "operation": "plot", "status": overall,
        "created_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": elapsed,
        "artifact": _relative_to(cost_run.root, charts_dir),
        "counts": {"requested": len(units), "succeeded": succeeded, "failed": failed},
        "versions": _versions(), "plots": plot_records,
    }
    index = {
        "schema_version": _SCHEMA_VERSION, "operation": "plot",
        "created_at": result["created_at"], "status": overall, "plots": index_records,
    }
    _json_write(charts_dir / "result.json", result)
    _json_write(charts_dir / "index.json", index)
    _LOG.info("plot.cost.completed", status=overall, requested=len(units),
             succeeded=succeeded, failed=failed, elapsed_seconds=elapsed)
    return result, overall
