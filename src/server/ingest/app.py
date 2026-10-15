"""Ingest as a msging `srv` host (ingest.md §3.1). Mounts the srv router (auth, the wire, the
SSE channel, the bounded in-memory queue + worker pool -- all owned by msging) and registers
the fold as the `on_event` handler. The DB fold itself is framework-free (fold_handler.py)."""
from __future__ import annotations

import structlog
from fastapi import FastAPI

from common.msging import srv
from common.msging.config import MsgingConfig
from server.storage.config import StorageConfig
from server.storage.engine import get_engine
from server.storage.queries import QueryCatalog
from server.worker.fold_api import SessionFold
from server.worker.graph_fold import GraphFold, load_pricing_survivable

from .config import IngestConfig
from .fold_handler import make_handler
from .git.config import load_config as load_github_config
from .git.routes import make_router as make_github_router
from .otlp.router import make_router as make_otlp_router

log = structlog.get_logger(module="ingest.app")


def _load_github(path: str):
    try:
        return load_github_config(path)
    except FileNotFoundError:
        # Survivable, same posture as pricing above: a deployment with no GitHub App
        # configured yet boots ingest without the webhook route rather than failing to start.
        log.warning("github_config_absent", path=path)
        return None


def create_app(
    config: IngestConfig,
    msging_config: MsgingConfig,
    storage_config: StorageConfig,
    fold: SessionFold | None = None,
) -> FastAPI:
    get_engine(storage_config)  # the process-wide engine session_scope() uses
    catalog = QueryCatalog.load(storage_config.queries_dir)
    fold = fold or GraphFold(pricing=load_pricing_survivable(config.pricing_org, config.pricing_version))

    # on_event before make_app: srv_start() (in make_app's lifespan) adopts the registered
    # handler; registering after boot would drop the notices the §4.1 rule protects.
    srv.on_event(make_handler(catalog, fold))
    app = srv.make_app("ingest", config=msging_config)

    # The second signal source, in the same process and behind its own guards (D-OT1). It
    # mounts here rather than inside srv because it is not a msging service: different wire
    # (OTLP), different credential kind (org token), different tables.
    if config.otlp.enabled:
        app.include_router(make_otlp_router(config.otlp))

    # git-source webhook receiver (git-source.md §3): a host route sharing this process's
    # storage layer and public listener, not its own process (implementation plan A3).
    if config.github_enabled:
        github_config = _load_github(config.github_config)
        if github_config is not None:
            app.include_router(make_github_router(github_config, catalog))

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app
