"""Command-line entry point: run the dashboard forecast batch and write its export.

    dashboard_forecast.sh --org-demo <dir> [--presets <yaml>] [--output <dir>] [--force]

The series dataset is `<org-demo>/series`; the default output is `<org-demo>/forecast`.
Order of work: list the run's scopes and map them to dashboard ids (an unmappable scope
stops here, before the output directory is touched), run the forecast and one cost run per
preset, then build, validate and write `<output>/forecast_export.json`.

Logs are structured JSON on stderr; stdout carries exactly one JSON summary. Exit codes:
0 success, 1 `ExecutionError`, 2 `InputError` (including command-line errors).
"""

import argparse
import json
from pathlib import Path
import sys

import structlog

from common.msging.log import configure_logging, get_logger

from .errors import ExecutionError, InputError
from .export import export_forecast
from .presets import DEFAULT_PRESETS_PATH
from .runner import run_dashboard_forecast, series_scopes
from .scopes import map_scopes


class DashboardParser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(f"invalid command line: {message}")


def parser():
    value = DashboardParser(
        prog="dashboard_forecast.sh",
        description="Run the dashboard forecast (one forecast, one cost run per preset) over an "
        "org-demo directory and write forecast_export.json.",
    )
    value.add_argument(
        "--org-demo", required=True, type=Path,
        help="org-demo directory (series/, org/ and configs/ inside)",
    )
    value.add_argument(
        "--presets", type=Path, default=DEFAULT_PRESETS_PATH,
        help="presets file (default: the shipped config/dashboard/presets.yaml)",
    )
    value.add_argument(
        "--output", type=Path,
        help="output directory for the run and the export (default: <org-demo>/forecast)",
    )
    value.add_argument(
        "--force", action="store_true",
        help="replace an earlier run (and its export) in a non-empty output directory",
    )
    return value


def main(argv=None):
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    log = get_logger(__name__)
    try:
        args = parser().parse_args(argv)
        org_demo = args.org_demo
        if not org_demo.is_dir():
            raise InputError(f"org-demo directory not found: {org_demo}")
        series_dir = org_demo / "series"
        output = args.output if args.output is not None else org_demo / "forecast"

        planned = series_scopes(series_dir)
        org_id = planned[0].name  # the organization scope is named after the series org id
        scopes = map_scopes(org_demo, planned, org_id=org_id)

        result = run_dashboard_forecast(series_dir, args.presets, output, force=args.force)
        if result.scopes != planned:
            raise ExecutionError("the run's scopes differ from the scopes mapped before it", stage="export")
        exported = export_forecast(result, scopes, series_dir=series_dir)
        summary = {
            "status": "complete",
            "exit_code": 0,
            "export_path": str(exported.path),
            "bytes": exported.bytes,
            "origin": result.origin.isoformat(),
            "data_through": result.data_through.isoformat(),
            "org_id": result.org_id,
            "scope_count": len(scopes),
            "preset_count": len(result.presets),
            "generated_at": exported.document["generated_at"],
        }
        log.info("dashboard.export_completed", **summary)
        print(json.dumps(summary, sort_keys=True))
        return 0
    except InputError as exc:
        summary = {"status": "invalid", "exit_code": 2, "error": str(exc), "stage": exc.stage}
        log.error("dashboard.input_error", **summary)
        print(json.dumps(summary, sort_keys=True))
        return 2
    except ExecutionError as exc:
        summary = {"status": "failed", "exit_code": 1, "error": str(exc), "stage": exc.stage}
        log.error("dashboard.execution_error", **summary)
        print(json.dumps(summary, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
