"""Load a dashboard forecast export into a dedicated `*_synth` database. Run:

    python -m server.storage.cli_load_forecast --export <forecast_export.json> \\
        --dsn postgresql+psycopg://<user>@localhost:5433/econ_synth

Replaces the previous forecast run (one run at a time) in a single transaction; a malformed
file or a failed write leaves the database unchanged. The guard refuses any database whose
name does not end with `_synth`, before ever connecting -- there is no override. Prints one
JSON summary line on stdout; logs are structured JSON on stderr. Exit 0 loaded, 2 bad file /
unsupported version / refused database, 1 anything else.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import structlog
from common.msging.log import configure_logging, get_logger

from .forecast_load import ForecastLoadError, ForecastLoadResult, load_forecast_export


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Load a dashboard-forecast export into a dedicated *_synth database.")
    p.add_argument("--export", required=True, type=Path, help="a forecast_export.json file")
    p.add_argument("--dsn", required=True, help="target database DSN; its name must end in _synth")
    return p


def _summary(result: ForecastLoadResult) -> dict:
    return {
        "status": "complete",
        "run_id": result.run_id,
        "scope_series_rows": result.scope_series_rows,
        "scope_count": result.scope_count,
        "preset_count": result.preset_count,
        "origin": result.origin.isoformat(),
        "data_through": result.data_through.isoformat(),
        "duration_ms": round(result.duration_ms, 1),
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    log = get_logger(__name__)

    try:
        result = load_forecast_export(args.export, args.dsn, log=log)
        print(json.dumps(_summary(result), sort_keys=True, separators=(",", ":")))
        return 0
    except ForecastLoadError as exc:
        log.error("forecast_load.run_failed", error_category="ForecastLoadError", message=str(exc))
        return 2
    except Exception as exc:
        log.error("forecast_load.run_failed", error_category=type(exc).__name__,
                  message="unexpected failure; see traceback")
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
