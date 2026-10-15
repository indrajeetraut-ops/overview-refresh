"""Command-line interface for plotting a saved forecast run's `forecast:` section.

Two mutually exclusive modes, both requiring `--artifact` (design §5):
  - Single-plot: `--artifact` + `--scope-type` (+ selection flags).
  - Batch config: `--artifact` + `--config <plot.yaml>` (the `forecast:` section).

Both modes resolve to the same `run.run_forecast_plots` driver so behavior
(actuals overlay, band, the `<run>/charts/` contract, resilient batching, exit
codes) is identical either way. Charts are written into `<artifact>/charts/`;
`--output` no longer exists (design §7).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import structlog
from pydantic import ValidationError

from common.msging.log import configure_logging
from forecast.forecasting.artifacts import load_run
from forecast.forecasting.errors import ExecutionError, InputError
from forecast.utils.plot_config import Defaults, ForecastEntry, load_plot_config
from forecast.utils.run import run_forecast_plots

_CONFIG_EXCLUDED_DESTS = ("scope_type", "scope_name", "models", "samples", "band", "format")


def _nonnegative(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a nonnegative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return parsed


def _percentile(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer between 1 and 99") from exc
    if not 0 < parsed < 100:
        raise argparse.ArgumentTypeError("must be an integer between 1 and 99")
    return parsed


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # A usage/argument problem is a "could not start" failure: nothing is
        # written, and it is reported the same way an unreadable config or
        # artifact would be (design §7: exit 1, stderr only).
        print(json.dumps({"event": "plot.failed", "error_type": "input", "message": message},
                         sort_keys=True), file=sys.stderr)
        raise SystemExit(1)


def parser() -> argparse.ArgumentParser:
    result = _Parser(
        description=(
            "Draw a saved forecast run's forecast: charts: real-data overlay, a "
            "configurable uncertainty band, HTML (shared plotly.min.js) and PNG "
            "output. Single-plot mode takes --scope-type plus selection flags; "
            "batch mode takes --config. Charts go to <artifact>/charts/."
        ),
    )
    result.add_argument("--artifact", type=Path, default=None,
                        help="required: saved forecast-run directory (the Organization is derived "
                             "from it; error if it holds more than one); charts go to its own charts/")
    result.add_argument("--scope-type", choices=("organization", "team", "project"), default=None,
                        help="required unless --config: organization, team, or project")
    result.add_argument("--scope-name", default=None,
                        help="optional for team/project (omit = one file per scope of that type); "
                             "must be omitted for organization")
    result.add_argument("--model", action="append", dest="models", default=None,
                        help="optional, repeatable: exact model ID; omit for all available models")
    result.add_argument("--samples", type=_nonnegative, default=None,
                        help="optional: sample paths per model to display (default: 0)")
    result.add_argument("--band", type=_percentile, nargs=2, metavar=("LOW", "HIGH"), default=None,
                        help="optional: band percentiles, 0 < LOW < HIGH < 100 (default: 10 90)")
    result.add_argument("--dataset", type=Path, default=None,
                        help="optional: override the actuals dataset directory "
                             "(default: auto-loaded from the run's recorded input); "
                             "allowed with --config too")
    result.add_argument("--format", choices=("html", "png", "both"), default=None,
                        help="optional: html, png, or both (default: both)")
    result.add_argument("--config", type=Path, default=None,
                        help="plot.yaml driving the forecast: section for this run; mutually "
                             "exclusive with --scope-type/--scope-name/--model/--samples/--band/--format")
    result.add_argument("--force", action="store_true",
                        help="replace an existing <artifact>/charts/ (default: refuse); allowed "
                             "with both modes")
    return result


def _log(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True, allow_nan=False), file=sys.stderr)


def _single_plot_entries(arguments, cli_parser: argparse.ArgumentParser) -> list[ForecastEntry]:
    if arguments.scope_type is None:
        cli_parser.error("missing required argument: --scope-type")
    entry_fields: dict = {"scope_type": arguments.scope_type}
    if arguments.scope_name is not None:
        entry_fields["scope_name"] = arguments.scope_name
    if arguments.models is not None:
        entry_fields["models"] = arguments.models
    if arguments.format is not None:
        entry_fields["format"] = arguments.format
    if arguments.samples is not None:
        entry_fields["samples"] = arguments.samples
    if arguments.band is not None:
        entry_fields["band"] = {"low": arguments.band[0], "high": arguments.band[1]}
    try:
        return [ForecastEntry.model_validate(entry_fields)]
    except ValidationError as exc:
        raise InputError(f"invalid plot selection: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    cli_parser = parser()
    arguments = cli_parser.parse_args(argv)
    started = time.perf_counter()
    if arguments.artifact is None:
        cli_parser.error("missing required argument: --artifact")
    selection_given = [f"--{name.replace('_', '-')}" for name in _CONFIG_EXCLUDED_DESTS
                       if getattr(arguments, name) is not None]
    if arguments.config is not None and selection_given:
        cli_parser.error("--config is mutually exclusive with " + ", ".join(selection_given))
    if arguments.config is None and arguments.scope_type is None:
        cli_parser.error("either --config or --scope-type is required")
    try:
        if arguments.config is not None:
            _log("plot.config.started", config=str(arguments.config))
            config = load_plot_config(arguments.config)
            if config.forecast is None:
                _log("plot.forecast.absent", config=str(arguments.config))
                summary = {"schema_version": 1, "operation": "plot", "status": "complete",
                          "artifact": str(arguments.artifact), "message": "no forecast: section; nothing to do"}
                print(json.dumps(summary, sort_keys=True, allow_nan=False))
                return 0
            entries = config.forecast
            defaults = config.defaults
            plotly_js = config.plotly_js
        else:
            entries = _single_plot_entries(arguments, cli_parser)
            defaults = Defaults()
            plotly_js = None
        _log("plot.selection", artifact=str(arguments.artifact), entry_count=len(entries),
             dataset_override=str(arguments.dataset) if arguments.dataset else None)
        saved_run = load_run(arguments.artifact)
        result, status = run_forecast_plots(
            saved_run, entries, defaults, plotly_js=plotly_js,
            dataset_override=arguments.dataset, force=arguments.force,
        )
        elapsed = time.perf_counter() - started
        _log("plot.completed", status=status, elapsed_seconds=elapsed,
             requested=result["counts"]["requested"], succeeded=result["counts"]["succeeded"],
             failed=result["counts"]["failed"])
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0 if status == "complete" else 2
    except InputError as exc:
        _log("plot.failed", error_type="input", message=str(exc),
             elapsed_seconds=time.perf_counter() - started)
        return 1
    except ExecutionError as exc:
        _log("plot.failed", error_type="execution", message=str(exc), stage=exc.stage,
             elapsed_seconds=time.perf_counter() - started)
        return 1
    except Exception as exc:
        _log("plot.failed", error_type="execution", message=type(exc).__name__,
             elapsed_seconds=time.perf_counter() - started)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
