"""F1: budgets config import. Run:

    python -m server.api.manager.cli_budgets --source config/budgets.yaml [--apply]

**`--dry-run` is the default** (same posture as `directory.cli_directory_import`) -- it loads
and validates every row (rejecting an unknown team/repo scope, F1's own rule) without writing;
`--apply` performs the upserts. Source shape:

    budgets:
      - {scope_type: org, scope_id: default, amount_usd: 15000}
      - {scope_type: team, scope_id: platform, amount_usd: 4000}

`period` is not in the file -- every row is written under the org's active budget-cycle label
("monthly" when no cycle was saved).
There is no `allowances:` section: a seat's allowance is its team budget / members
(docs/plan/2026-09-26-cost-based-seat-utilization.md). A file that still carries one is refused
(exit 2) rather than half-applied, so a stale config cannot silently lose its intent.
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

import structlog
import yaml

from server.storage.config import load_config as load_storage_config
from server.storage.engine import get_engine, session_scope
from server.storage.queries import QueryCatalog

from ..config import ApiConfig
from ..errors import ApiError
from . import budget_cycles
from . import budgets as budgets_service

log = structlog.get_logger(module="api.manager.cli_budgets")

#: The label used when the org has no saved budget cycle (the default recurring month); with a
#: saved cycle the rows are written under that cycle's label instead.
BUDGET_PERIOD = ApiConfig().budget_period


ALLOWANCES_REMOVED = (
    "token allowances were removed (cost-based seats, 2026-09-26); a seat's allowance is now its "
    "team budget / members -- delete the 'allowances:' section"
)


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {"budgets": data.get("budgets", []), "allowances": data.get("allowances") or []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="YAML file with a budgets: section")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="validate and report, write nothing (default)")
    mode.add_argument("--apply", action="store_true", help="write the upserts")
    parser.add_argument("--storage-config", default="config/storage.yaml")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.is_file():
        parser.error(f"--source {source} is not a file")
    data = _load(source)
    if data["allowances"]:
        print(f"budgets import: {ALLOWANCES_REMOVED}", file=sys.stderr)
        return 2

    storage_cfg = load_storage_config(args.storage_config)
    get_engine(storage_cfg)
    catalog = QueryCatalog.load(storage_cfg.queries_dir)

    applied = bool(args.apply)
    written = 0
    rejected: list[str] = []
    org_id = ApiConfig().directory.default_org_id
    with session_scope() as s:
        period = budget_cycles.active(s, catalog, org_id, BUDGET_PERIOD).label
        for row in data["budgets"]:
            try:
                budgets_service.put_budget(
                    s, catalog, row["scope_type"], row["scope_id"], period,
                    Decimal(str(row["amount_usd"])),
                )
                written += 1
            except ApiError as e:
                rejected.append(f"budget {row['scope_type']}/{row['scope_id']}: {e.reason}")
        if not applied:
            s.rollback()  # dry-run: validated, nothing kept (session_scope commits otherwise)

    print(f"\nbudgets import: {'APPLIED' if applied else 'DRY RUN -- nothing written'}")
    print(f"  budgets rows   {len(data['budgets'])}")
    print(f"  written        {written}")
    for r in rejected:
        print(f"  rejected: {r}")

    log.info("budgets_import", applied=applied, written=written, rejected=len(rejected))
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
