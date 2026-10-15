"""`update` CLI (design §6, §8): build the master rate table from every enabled
source and write a new catalog version if any served price changed.

    python -m common.model_pricing.cli_update [--dry-run] [--json] [--litellm-commit SHA]
        [--config-root DIR] [--catalog-root DIR]

`--dry-run` runs the full build and prints the refresh report (§7) to stdout;
nothing is written. `--json` prints the refresh *detail* (`render_refresh_json`)
instead of the default Markdown *summary* (`render_refresh`) -- both dry-run and a
real run. `--config-root`/`--catalog-root` default to `config/model_pricing`
and `config/model_pricing/catalog` (§10) and exist so tests (and any caller building a
throwaway catalog) can point this at a `tmp_path` without touching the real one.

Exit codes (§8): 0 for a new version or a no-op (nothing changed); 1 if any enabled
source, or the config it depends on, fails.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from common.model_pricing.build import DEFAULT_FETCHERS, build, load_previous, write_catalog
from common.model_pricing.catalog import DEFAULT_CATALOG_ROOT
from common.model_pricing.config import DEFAULT_ROOT as DEFAULT_CONFIG_ROOT, load_model_pricing_config
from common.model_pricing.errors import CatalogError, ConfigError, SourceError
from common.model_pricing.reports import render_refresh, render_refresh_json
from common.msging.log import configure_logging, get_logger

#: Test-injection seam: `None` (the default) means "use `build.DEFAULT_FETCHERS` for
#: every source" -- ordinary production behavior. A test sets this to a dict
#: overriding some or all of them (same shape `build()` itself accepts, e.g.
#: `{"aws_price_list": my_failing_fetcher}`) before calling `main`, so an update run
#: can be driven end-to-end against fixtures, or made to fail a specific source,
#: without any network access.
_FETCHERS: dict[str, Callable[..., Any]] | None = None


def _wrapped_fetchers(log: Any) -> dict[str, Callable[..., Any]]:
    """`DEFAULT_FETCHERS` overridden by `_FETCHERS`, each entry wrapped to log
    `source_fetched` / `source_failed` (§8) around the call. Wrapping happens here,
    once per `main()` call, rather than inside `build.py`, which stays disk/log-free
    (its own docstring: "build() is pure")."""
    merged = {**DEFAULT_FETCHERS, **(_FETCHERS or {})}

    def _wrap(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - logged, then re-raised unchanged
                log.warning(
                    "source_failed", source=name, error=type(exc).__name__, detail=str(exc)
                )
                raise
            log.info("source_fetched", source=name)
            return result

        return wrapped

    return {name: _wrap(name, fn) for name, fn in merged.items()}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="model_pricing update", description="Build the master rate table (design §6)."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the refresh report; write nothing"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the refresh detail (JSON) instead of the summary (Markdown)",
    )
    parser.add_argument(
        "--litellm-commit", default=None, help="pin the LiteLLM commit SHA to fetch (default: latest)"
    )
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
    # (the refresh report) -- design §8's "structured JSON logs go to stderr" via
    # `common.msging.log`, made concrete here since `configure_logging()`'s own
    # default (`structlog.PrintLoggerFactory()`) writes to stdout.
    configure_logging(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    log = get_logger("model_pricing")

    config_root = Path(args.config_root)
    catalog_root = Path(args.catalog_root)

    try:
        cfg = load_model_pricing_config(config_root / "model_pricing.yaml")
        previous = load_previous(catalog_root)
        result = build(
            cfg,
            fetchers=_wrapped_fetchers(log),
            previous=previous,
            litellm_commit=args.litellm_commit,
        )
    except (SourceError, ConfigError, CatalogError) as exc:
        log.error("update_failed", error=str(exc))
        print(f"update failed: {exc}", file=sys.stderr)
        return 1

    log.info("registry_built", models=len(result.version.models))

    if result.is_noop:
        # A no-op result carries a freshly dated version id that is never written; the
        # current catalog is still `previous`.
        current = previous.version if previous is not None else result.version.version
        log.info("update_noop", version=current)
        print(f"no changes; catalog stays at {current}")
        return 0

    report = render_refresh_json(result) if args.json else render_refresh(result)
    if args.dry_run:
        print(report)
        return 0

    try:
        write_catalog(result, catalog_root)
    except CatalogError as exc:
        log.error("update_failed", error=str(exc))
        print(f"update failed: {exc}", file=sys.stderr)
        return 1

    log.info("version_written", version=result.version.version, rows=len(result.version.rates))
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
