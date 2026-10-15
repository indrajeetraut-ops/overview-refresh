"""Command-line entry point for pricing a forecast run into USD (design §3.2)."""

import argparse
import json
from pathlib import Path
import sys

import structlog

from common.msging.log import configure_logging, get_logger

from .errors import ExecutionError, InputError
from .request import CostRequest
from .runner import run_cost


class CostParser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(f"invalid command line: {message}")


def parser():
    value = CostParser(
        prog="cost.sh",
        description="Price a completed forecast run's token paths into USD, baseline or a named scenario.",
        epilog="--output is required unless --validate-only.",
    )
    value.add_argument(
        "--forecast-run",
        required=True,
        type=Path,
        help="completed forecast run directory (forecast.forecasting's own output)",
    )
    value.add_argument(
        "--scenario",
        type=Path,
        help="scenario YAML (design §6.1); omitted prices the baseline",
    )
    value.add_argument(
        "--output",
        type=Path,
        help="new output directory for cost artifacts; required unless --validate-only",
    )
    value.add_argument(
        "--validate-only",
        action="store_true",
        help="resolve inputs and build the rate sheet, list unpriced models, without pricing or writing output",
    )
    value.add_argument(
        "--force",
        action="store_true",
        help="remove an existing --output directory before writing (default: refuse an existing output path)",
    )
    return value


def main(argv=None):
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    log = get_logger(__name__)
    try:
        args = parser().parse_args(argv)
        result = run_cost(
            args.forecast_run,
            CostRequest(scenario=args.scenario),
            args.output,
            validate_only=args.validate_only,
            force=args.force,
        )
        log.info("cost.run_completed", **result.to_dict())
        print(json.dumps(result.to_dict(), sort_keys=True))
        return result.exit_code
    except InputError as exc:
        summary = {"status": "invalid", "exit_code": 2, "error": str(exc), "stage": exc.stage}
        log.error("cost.input_error", **summary)
        print(json.dumps(summary, sort_keys=True))
        return 2
    except ExecutionError as exc:
        summary = {"status": "failed", "exit_code": 1, "error": str(exc), "stage": exc.stage}
        log.error("cost.execution_error", **summary)
        print(json.dumps(summary, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
