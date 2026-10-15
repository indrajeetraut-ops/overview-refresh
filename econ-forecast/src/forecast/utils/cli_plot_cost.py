"""Command-line interface for plotting a saved cost run's `cost:` section
(design §5, §14.5). Config-only, unlike `plot_forecast` -- there is no
single-selection mode.

After drawing charts into `<cost-run>/charts/`, re-renders `report.md`
(`cost.presentation.render_report`) so its Charts section links exactly what
`charts/index.json` now lists -- for exit 0 and 2. A `1` (could not start:
unreadable config/run, existing `charts/` without `--force`, a stem collision)
writes nothing and leaves `report.md` untouched.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import structlog

from common.msging.log import configure_logging
from forecast.cost.artifacts import load_cost_run
from forecast.cost.errors import ExecutionError as CostExecutionError, InputError as CostInputError
from forecast.cost.presentation import render_report
from forecast.forecasting.errors import ExecutionError, InputError
from forecast.utils.plot_config import load_plot_config
from forecast.utils.run import run_cost_plots

# plot.yaml parsing raises forecasting's InputError (a utils-level artifact,
# not cost-specific); the cost run/plots themselves raise the cost module's
# own, distinct error types (forecast.cost.errors) -- both are "could not
# start"/exit-1 failures from this CLI's point of view.
_INPUT_ERRORS = (InputError, CostInputError)
_EXECUTION_ERRORS = (ExecutionError, CostExecutionError)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps({"event": "plot.failed", "error_type": "input", "message": message},
                         sort_keys=True), file=sys.stderr)
        raise SystemExit(1)


def parser() -> argparse.ArgumentParser:
    result = _Parser(
        description=(
            "Draw a saved cost run's cost: charts (design plot.yaml): expected/"
            "band fan per scope and per model, model breakdown per scope. HTML "
            "(shared plotly.min.js) and PNG. Charts go to <cost-run>/charts/; "
            "report.md is re-rendered afterward to link them."
        ),
    )
    result.add_argument("--cost-run", type=Path, default=None,
                        help="required: saved cost-run directory; charts go to its own charts/, "
                             "and its report.md is re-rendered afterward")
    result.add_argument("--config", type=Path, default=None,
                        help="required: plot.yaml driving the cost: section for this run")
    result.add_argument("--force", action="store_true",
                        help="replace an existing <cost-run>/charts/ (default: refuse)")
    return result


def _log(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True, allow_nan=False), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    cli_parser = parser()
    arguments = cli_parser.parse_args(argv)
    started = time.perf_counter()
    if arguments.cost_run is None:
        cli_parser.error("missing required argument: --cost-run")
    if arguments.config is None:
        cli_parser.error("missing required argument: --config")
    try:
        _log("plot.config.started", config=str(arguments.config))
        config = load_plot_config(arguments.config)
        cost_run = load_cost_run(arguments.cost_run)
        if config.cost is None:
            _log("plot.cost.absent", config=str(arguments.config))
            summary = {"schema_version": 1, "operation": "plot", "status": "complete",
                      "artifact": str(arguments.cost_run), "message": "no cost: section; nothing to do"}
            print(json.dumps(summary, sort_keys=True, allow_nan=False))
            return 0
        _log("plot.selection", artifact=str(arguments.cost_run), entry_count=len(config.cost))
        result, status = run_cost_plots(
            cost_run, config.cost, config.defaults, plotly_js=config.plotly_js, force=arguments.force,
        )
        render_report(cost_run, cost_run.root)
        elapsed = time.perf_counter() - started
        _log("plot.completed", status=status, elapsed_seconds=elapsed,
             requested=result["counts"]["requested"], succeeded=result["counts"]["succeeded"],
             failed=result["counts"]["failed"])
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0 if status == "complete" else 2
    except _INPUT_ERRORS as exc:
        _log("plot.failed", error_type="input", message=str(exc),
             elapsed_seconds=time.perf_counter() - started)
        return 1
    except _EXECUTION_ERRORS as exc:
        _log("plot.failed", error_type="execution", message=str(exc), stage=exc.stage,
             elapsed_seconds=time.perf_counter() - started)
        return 1
    except Exception as exc:
        _log("plot.failed", error_type="execution", message=type(exc).__name__,
             elapsed_seconds=time.perf_counter() - started)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
