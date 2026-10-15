"""Command-line entry point for forecast validation and generation."""

import argparse
from datetime import date
import json
from pathlib import Path
import sys

import structlog

from common.msging.log import configure_logging, get_logger

from .errors import ExecutionError, InputError
from .request import ForecastRequest
from .runner import run_forecast


class ForecastParser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(f"invalid command line: {message}")


def parser():
    value = ForecastParser(
        prog="forecast.sh",
        description=(
            "Validate inputs or forecast daily token consumption for requested "
            "Organization, Team, and Project scopes."
        ),
        epilog=(
            "Reusable modeling and scope policy belongs in the YAML config; "
            "the calendar horizon belongs on this command line; omitted origin uses the dataset's declared end date."
        ),
    )
    value.add_argument(
        "--config",
        required=True,
        type=Path,
        help="forecast YAML containing dataset, scope, history, uncertainty, and runtime policy",
    )
    value.add_argument(
        "--origin",
        help="optional historical cutoff (YYYY-MM-DD); defaults to declared dataset end, including covered zero days",
    )
    value.add_argument(
        "--unit",
        required=True,
        choices=("week", "month"),
        help="calendar unit for the requested horizon; weeks run Monday through Sunday",
    )
    value.add_argument(
        "--offset",
        type=int,
        default=1,
        help="1-based requested unit relative to the next calendar week or month (default: 1)",
    )
    value.add_argument(
        "--periods",
        type=int,
        default=1,
        help="number of consecutive calendar weeks or months to publish (default: 1)",
    )
    value.add_argument(
        "--output",
        type=Path,
        help="new output directory for forecast artifacts; required unless --validate-only",
    )
    value.add_argument(
        "--validate-only",
        action="store_true",
        help="validate config, inputs, coverage, history, and eligibility without generating paths or writing output",
    )
    value.add_argument(
        "--force",
        action="store_true",
        help="remove an existing --output directory before writing (default: refuse an existing output path)",
    )
    return value


def main(argv=None):
    configure_logging(
        json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr)
    )
    log = get_logger(__name__)
    try:
        args = parser().parse_args(argv)
        try:
            origin = date.fromisoformat(args.origin) if args.origin is not None else None
        except ValueError as exc:
            raise InputError("origin must be YYYY-MM-DD") from exc
        result = run_forecast(
            args.config,
            ForecastRequest(origin, args.unit, args.offset, args.periods),
            args.output,
            validate_only=args.validate_only,
            force=args.force,
        )
        log.info("forecast.run_completed", **result.to_dict())
        print(json.dumps(result.to_dict(), sort_keys=True))
        return result.exit_code
    except InputError as exc:
        summary = {
            "status": "invalid",
            "exit_code": 2,
            "error": str(exc),
            "stage": exc.stage,
        }
        log.error("forecast.input_error", **summary)
        print(json.dumps(summary, sort_keys=True))
        return 2
    except ExecutionError as exc:
        summary = {
            "status": "failed",
            "exit_code": 1,
            "error": str(exc),
            "stage": exc.stage,
        }
        log.error("forecast.execution_error", **summary)
        print(json.dumps(summary, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
