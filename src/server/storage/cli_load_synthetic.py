"""Load an `org-expand` dataset into a dedicated `*_synth` database (plan §5 P10). Run:

    python -m server.storage.cli_load_synthetic --dataset <org-expand dir> \\
        --dsn postgresql+psycopg://<user>@localhost:5433/econ_synth [--replace] [--dry-run] \\
        [--forecast-export <forecast_export.json>]

The guard (D13) refuses any database whose name does not end with `_synth`, before ever
connecting -- there is no override. Never point this at `econ`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import structlog
from common.msging.log import configure_logging, get_logger

from .synthetic_load import LoadResult, RollupMismatchError, SyntheticLoadError, load_dataset


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Load a synthetic org-expand dataset into a dedicated *_synth database.")
    p.add_argument("--dataset", required=True, type=Path, help="an org-expand output directory")
    p.add_argument("--dsn", required=True, help="target database DSN; its name must end in _synth")
    p.add_argument("--replace", action="store_true", help="truncate every loaded table first")
    p.add_argument("--forecast-export", type=Path, default=None, metavar="FILE",
                   help="also load this forecast_export.json (replacing any forecast run) in the "
                        "same transaction; checked against the dataset before any database work")
    p.add_argument("--dry-run", action="store_true",
                   help="print planned row counts and exit; never connects to the database")
    return p


def _summary(result: LoadResult, dsn: str) -> dict:
    port_hint = "8091"
    base = {
        "status": "valid" if result.dry_run else "complete",
        "dry_run": result.dry_run,
        "row_counts": result.row_counts,
        "rollup_ok": result.rollup_ok,
        "forecast_run_id": result.forecast_run_id,
        "duration_ms": round(result.duration_ms, 1),
    }
    if not result.dry_run:
        base.update(
            admin_viewer_id=result.admin_viewer_id,
            sample_lead_viewer_id=result.sample_lead_viewer_id,
            sample_dev_viewer_id=result.sample_dev_viewer_id,
            next_steps=[
                f"TALIUM_DB_DSN={dsn} python -m server.api.cli_serve_api --port {port_hint}",
                f"VITE_DATA_SOURCE=api VITE_API_TARGET=http://localhost:{port_hint} "
                f"VITE_VIEWER_ID={result.admin_viewer_id} VITE_VIEWER_ROLE=admin npm run dev",
            ],
        )
    return base


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    configure_logging(json=True, logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    log = get_logger(__name__)

    try:
        result = load_dataset(args.dataset, args.dsn, replace=args.replace,
                              dry_run=args.dry_run, log=log,
                              forecast_export=args.forecast_export)
        print(json.dumps(_summary(result, args.dsn), sort_keys=True, separators=(",", ":")))
        return 0
    except SyntheticLoadError as exc:
        log.error("synthetic_load.run_failed", error_category="SyntheticLoadError", message=str(exc))
        return 2
    except RollupMismatchError as exc:
        log.error("synthetic_load.run_failed", error_category="RollupMismatchError", message=str(exc))
        return 1
    except Exception as exc:
        log.error("synthetic_load.run_failed", error_category=type(exc).__name__,
                  message="unexpected failure; see traceback")
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
