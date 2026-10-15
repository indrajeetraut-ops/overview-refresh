"""`show` CLI (design §8): print every channel's row for one model in a catalog,
marking the org's own channel if `--org` is given.

    python -m common.model_pricing.cli_show --model M [--org ORG] [--catalog VERSION]
        [--config-root DIR] [--catalog-root DIR]

`M` is an observed model string, normalized the same way `match`/`resolve` would
(§5.3) -- so `show --model us.anthropic.claude-sonnet-5` and
`show --model claude-sonnet-5` print the same rows.

Exit codes (§8): 0 if the model resolves to a known `model_id` with at least one
priced channel; 2 if it does not (unmatched/ambiguous, or matched but priced nowhere).
1 if the catalog/config itself could not be loaded.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog

from common.model_pricing.catalog import DEFAULT_CATALOG_ROOT, load_catalog
from common.model_pricing.config import DEFAULT_ROOT as DEFAULT_CONFIG_ROOT
from common.model_pricing.errors import CatalogError, ConfigError
from common.model_pricing.normalize import normalize as normalize_model_id
from common.model_pricing.reports import format_rate_per_million
from common.msging.log import configure_logging, get_logger

_ROW_TOKEN_TYPES = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="model_pricing show", description="Rows for one model across all channels (design §8)."
    )
    parser.add_argument("--model", required=True, help="observed model string")
    parser.add_argument("--org", default=None, help="mark this org's channel, if given (§4.3)")
    parser.add_argument("--catalog", default=None, help="catalog version; default: current")
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
    # (the rows table) -- design §8's "structured JSON logs go to stderr" via
    # `common.msging.log`, made concrete here since `configure_logging()`'s own
    # default (`structlog.PrintLoggerFactory()`) writes to stdout.
    configure_logging(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    log = get_logger("model_pricing")

    try:
        catalog = load_catalog(
            version=args.catalog, root=Path(args.catalog_root), config_root=Path(args.config_root)
        )
        # A neutral `OrgPrices` gives us the catalog's registry/aliases/channel_config
        # without asserting a real org identity when `--org` is not given (§4.3: an
        # unlisted org just falls through to vendor channels).
        prices = catalog.for_org(args.org or "")
    except (CatalogError, ConfigError) as exc:
        log.error("show_failed", error=str(exc))
        print(f"show failed: {exc}", file=sys.stderr)
        return 1

    result = normalize_model_id(args.model, prices.registry, prices.aliases)
    if result.status != "matched" or result.model_id is None:
        log.info("show_unmatched", model=args.model, status=result.status)
        print(f"unknown or ambiguous model: {args.model!r} (status={result.status})", file=sys.stderr)
        return 2

    model_id = result.model_id
    vendor = prices.registry.vendor_of(model_id)

    rows_by_channel = {
        channel: rows
        for (channel, mid), rows in catalog.rows_by_key.items()
        if mid == model_id
    }
    if not rows_by_channel:
        log.info("show_unpriced", model=args.model, model_id=model_id)
        print(f"model {model_id!r} ({vendor}) has no priced rows in any channel", file=sys.stderr)
        return 2

    org_channel = prices.channel_config.channel_for(args.org, vendor) if args.org else None

    print(f"# {model_id} ({vendor}) -- matched via {result.step} -- catalog {catalog.version}")
    print()
    header = ["channel", "source", "source_key", *_ROW_TOKEN_TYPES, "start_date", "retired_on", "org"]
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join(["---"] * len(header)) + "|")
    for channel in sorted(rows_by_channel):
        row = rows_by_channel[channel][-1]  # latest row for this channel (current price)
        marker = "yes" if channel == org_channel else ""
        rate_cells = [format_rate_per_million(row.rate(tt)) for tt in _ROW_TOKEN_TYPES]
        cells = [
            f"`{channel}`",
            row.source,
            row.source_key,
            *rate_cells,
            row.start_date.isoformat(),
            row.retired_on.isoformat() if row.retired_on else "-",
            marker,
        ]
        print("| " + " | ".join(cells) + " |")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
