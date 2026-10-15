"""Run one batch collection pass: sweep for uploaded manifests, normalize, ship, checkpoint.

Usage:
    python -m collector.cli_collect --config config/collector.yaml [--scope uploads/]
    python -m collector.cli_collect --config config/collector.yaml --dump-to shipped.jsonl
        # writes the Envelopes msging would have sent to shipped.jsonl (one per line)
        # against a fake in-process Ingest that ACKs them -- no live Ingest process needed.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import structlog

from .config import load_config
from .dump_transport import make_dump_transport
from .engine import run_once

log = structlog.get_logger(module="collector.cli_collect")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="Path to the collector YAML config")
    parser.add_argument("--scope", default="uploads/", help="Store prefix to sweep for manifests")
    parser.add_argument(
        "--dump-to",
        default=None,
        help="Write shipped Envelopes here (JSONL) against a fake in-process Ingest, no live server",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)

    transport = None
    if args.dump_to:
        transport = make_dump_transport(Path(args.dump_to))

    summary = run_once(config, scope=args.scope, transport=transport)

    log.info(
        "cli_collect_complete",
        manifests_found=summary.manifests_found,
        sources_processed=summary.sources_processed,
        sources_skipped_unchanged=summary.sources_skipped_unchanged,
        sources_incomplete=summary.sources_incomplete,
        dumped_to=args.dump_to,
    )


if __name__ == "__main__":
    main()
