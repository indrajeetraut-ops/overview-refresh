"""Export the dashboard API's OpenAPI schema to a file (MVP plan F0 -- the contract's
serialised form, from which the front-end's TS types are generated).

    python -m server.api.cli_export_openapi [--out src/server/api/openapi.json] [--check]

`--check` exits non-zero if the committed file is stale (drift guard for CI); without it the
file is (re)written. Building the app needs no database -- `create_app` only touches the
engine lazily -- so this runs in a bare checkout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from server.storage.config import StorageConfig
from server.storage.engine import no_engine

from .app import create_app
from .config import ApiConfig

#: Default lives beside the package so `git diff` on it is an obvious contract change.
DEFAULT_OUT = Path(__file__).parent / "openapi.json"


def build_schema() -> dict:
    """The OpenAPI schema, built DB-less. `no_engine()` keeps `create_app`'s `get_engine`
    call from clobbering a real engine when this runs inside a test session."""
    with no_engine():
        # webui_dist neutralised so the committed schema never depends on whether `vite build`
        # ran locally (a StaticFiles mount adds no API routes, but keep it deterministic).
        cfg = ApiConfig(webui_dist="/nonexistent/webui/dist")
        app = create_app(cfg, StorageConfig(dsn="postgresql+psycopg://unused/none"))
        return app.openapi()


def _serialise(schema: dict) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if --out is stale instead of writing it")
    args = parser.parse_args()

    rendered = _serialise(build_schema())

    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != rendered:
            print(f"{args.out} is stale -- run: python -m server.api.cli_export_openapi",
                  file=sys.stderr)
            sys.exit(1)
        print(f"{args.out} is up to date")
        return

    args.out.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
