"""`match` CLI (design §7, §8, §9): resolve a list of observed model strings against
a catalog for one org and print the mapping report.

    python -m common.model_pricing.cli_match --org ORG --models a,b,c
        [--catalog VERSION] [--format md|json] [--date YYYY-MM-DD]
        [--config-root DIR] [--catalog-root DIR]

Exit codes (§8): 0 if every model resolved (`matched`); 2 if any did not
(`unmatched`/`ambiguous`, or matched a model with no row on the org's channel) -- a
caller scripting this (e.g. validating a channel config before deploying it) can tell
"something didn't price" from the exit code alone. 1 if the catalog/config itself
could not be loaded.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date as date_cls
from pathlib import Path

import structlog

from common.model_pricing.catalog import DEFAULT_CATALOG_ROOT, load_catalog
from common.model_pricing.config import DEFAULT_ROOT as DEFAULT_CONFIG_ROOT
from common.model_pricing.errors import CatalogError, ConfigError
from common.model_pricing.reports import render_mapping
from common.msging.log import configure_logging, get_logger


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="model_pricing match", description="Mapping report for one org (design §7, §9)."
    )
    parser.add_argument("--org", required=True, help="org id (channel config lookup, §4.3)")
    parser.add_argument(
        "--models", required=True, help="comma-separated observed model strings"
    )
    parser.add_argument("--catalog", default=None, help="catalog version; default: current")
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD; default: today")
    parser.add_argument(
        "--config-root", default=str(DEFAULT_CONFIG_ROOT), help="directory holding the *.yaml config files"
    )
    parser.add_argument(
        "--catalog-root", default=str(DEFAULT_CATALOG_ROOT), help="directory holding catalog/*.json"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    # Logs go to stderr, never stdout: stdout is reserved for this CLI's own result
    # (the mapping report) -- design §8's "structured JSON logs go to stderr" via
    # `common.msging.log`, made concrete here since `configure_logging()`'s own
    # default (`structlog.PrintLoggerFactory()`) writes to stdout.
    configure_logging(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    log = get_logger("model_pricing")

    on_date = date_cls.fromisoformat(args.date) if args.date else date_cls.today()
    model_ids = [m.strip() for m in args.models.split(",") if m.strip()]

    try:
        catalog = load_catalog(
            version=args.catalog, root=Path(args.catalog_root), config_root=Path(args.config_root)
        )
        prices = catalog.for_org(args.org)
    except (CatalogError, ConfigError) as exc:
        log.error("match_failed", error=str(exc))
        print(f"match failed: {exc}", file=sys.stderr)
        return 1

    results = prices.mapping_report(model_ids)
    print(render_mapping(results, fmt=args.format, prices=prices, date=on_date))

    if any(r.status != "matched" for r in results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
