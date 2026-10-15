"""Directory CSV import (docs/design/identity-ingestion.md §3.3, implementation plan G4). Run:

    python -m server.directory.cli_directory_import --source path/to/csvdir [--apply]

Reads `users.csv` / `teams.csv` / `members.csv` from `--source`, maps them to
`DirectoryRecord`s (`server.directory.providers.csv`), and either previews or applies the
sync. **`--dry-run` is the default** -- it runs the full sync inside a transaction that is
then rolled back, so the printed create/update/link counts are exactly what `--apply` would
do, and nothing is written.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from server.storage.config import load_config as load_storage_config
from server.storage.engine import get_engine, session_scope
from server.storage.queries import QueryCatalog

from .config import load_config
from .models import SyncResult
from .precedence import Precedence
from .providers.csv import parse_csv_dir
from .sync import sync

log = structlog.get_logger(module="directory.cli_directory_import")


def _print_report(result: SyncResult, warnings: list[str], *, applied: bool) -> None:
    head = "APPLIED" if applied else "DRY RUN -- nothing written"
    print(f"\ndirectory import: {head}")
    print(f"  created  {result.created}")
    print(f"  updated  {result.updated}")
    print(f"  linked   {result.linked}")
    print(f"  skipped  {result.skipped}")
    for w in warnings:
        print(f"  warning: {w}")
    for n in result.notes:
        print(f"  note:    {n}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import an org directory from CSV files.")
    parser.add_argument(
        "--source", required=True,
        help="directory containing users.csv (required), teams.csv, members.csv",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", default=True,
        help="preview the diff, write nothing (default)",
    )
    mode.add_argument(
        "--apply", action="store_true",
        help="write the changes via directory.sync()",
    )
    parser.add_argument("--config", default="config/directory.yaml")
    parser.add_argument("--storage-config", default="config/storage.yaml")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.is_dir():
        parser.error(f"--source {source} is not a directory")

    config = load_config(args.config)
    parsed = parse_csv_dir(source, config)
    log.info(
        "directory.csv_parsed",
        source=str(source),
        teams=len(parsed.teams),
        records=len(parsed.records),
        warnings=len(parsed.warnings),
    )

    storage_cfg = load_storage_config(args.storage_config)
    get_engine(storage_cfg)
    catalog = QueryCatalog.load(storage_cfg.queries_dir)
    precedence = Precedence(config.precedence)

    if args.apply:
        with session_scope() as s:
            result = sync(
                s, catalog, parsed.records, teams=parsed.teams,
                precedence=precedence, config=config,
            )
        _print_report(result, parsed.warnings, applied=True)
    else:
        # full sync, then roll back -- the counts are real, the writes are not.
        with Session(get_engine(storage_cfg)) as s:
            result = sync(
                s, catalog, parsed.records, teams=parsed.teams,
                precedence=precedence, config=config,
            )
            s.rollback()
        _print_report(result, parsed.warnings, applied=False)

    return 0


if __name__ == "__main__":
    sys.exit(main())
