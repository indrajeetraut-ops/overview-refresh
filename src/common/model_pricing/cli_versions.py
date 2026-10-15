"""`versions` CLI (design §8): list catalog versions with provenance and row counts.

    python -m common.model_pricing.cli_versions [--limit N] [--catalog-root DIR]

Reads `<version>.json` files directly (not `load_catalog`, which unions registries
across every earlier version and would make listing N versions O(N^2) work) -- a
version's own `sources`/`rates`/`excluded`/`unmapped` are exactly what its file holds
(§3.3: `rates` is a full snapshot, not a delta, so `len(rates)` is that version's total
priced-row count).

Always exits 0 (§8): an empty or missing catalog is reported, not treated as failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import structlog

from common.model_pricing.catalog import DEFAULT_CATALOG_ROOT, CatalogVersion
from common.msging.log import configure_logging, get_logger


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="model_pricing versions", description="List catalog versions (design §8)."
    )
    parser.add_argument("--limit", type=int, default=None, help="show only the most recent N versions")
    parser.add_argument(
        "--catalog-root", default=str(DEFAULT_CATALOG_ROOT), help="directory holding catalog/*.json"
    )
    return parser.parse_args(argv)


def _list_version_strings(root: Path) -> list[str]:
    index_path = root / "index.json"
    if not index_path.exists():
        return []
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    versions = data.get("versions") if isinstance(data, dict) else None
    return list(versions) if isinstance(versions, list) else []


def _provenance_summary(prov: dict[str, Any]) -> str:
    if prov.get("status") == "skipped":
        return f"skipped ({prov.get('reason', '?')})"
    if "commit_sha" in prov:
        return f"commit {prov['commit_sha']}"
    if "publication_date" in prov:
        return f"pub {prov['publication_date']}"
    if "fetched_at" in prov:
        return f"fetched {prov['fetched_at']}"
    return "ok"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    # Logs go to stderr, never stdout: stdout is reserved for this CLI's own result
    # (the version list) -- design §8's "structured JSON logs go to stderr" via
    # `common.msging.log`, made concrete here since `configure_logging()`'s own
    # default (`structlog.PrintLoggerFactory()`) writes to stdout.
    configure_logging(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    log = get_logger("model_pricing")

    root = Path(args.catalog_root)
    version_strings = _list_version_strings(root)
    if args.limit is not None:
        version_strings = version_strings[-args.limit :]

    if not version_strings:
        print("no catalog versions")
        return 0

    for version_str in version_strings:
        path = root / f"{version_str}.json"
        try:
            cv = CatalogVersion.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("versions_read_failed", version=version_str, error=str(exc))
            print(f"{version_str}: unreadable ({exc})")
            continue
        source_summary = ", ".join(
            f"{name}={_provenance_summary(prov)}" for name, prov in sorted(cv.sources.items())
        )
        print(
            f"{cv.version}  built_on={cv.built_on.isoformat()}  rows={len(cv.rates)}  "
            f"excluded={len(cv.excluded)}  unmapped={len(cv.unmapped)}  sources: {source_summary}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
