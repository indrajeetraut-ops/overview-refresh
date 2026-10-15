"""Regression guard for the api-latency plan's Phase A (D1): every route handler must be a
plain `def`. An `async def` handler that touches the DB blocks uvicorn's single event loop for
every other in-flight request -- exactly the stall §0 measured."""
import inspect
import os

from fastapi.routing import APIRoute

from server.api.app import create_app
from server.api.cli_serve_api import app_from_env

from .conftest import requires_db


def test_route_handlers_are_sync(api_config, storage_config):
    app = create_app(api_config, storage_config)
    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    assert routes, "expected at least one APIRoute"
    for route in routes:
        assert not inspect.iscoroutinefunction(route.endpoint), (
            f"{route.path!r} is an async handler; route handlers must be sync `def` so DB "
            f"calls run in Starlette's thread pool instead of blocking the event loop"
        )


@requires_db
def test_app_from_env_builds_from_config_env_var(monkeypatch):
    monkeypatch.setenv("TALIUM_API_CONFIG", "config/api.yaml")
    monkeypatch.setenv("TALIUM_DB_DSN", os.environ["TALIUM_TEST_DSN"])
    app = app_from_env()
    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    assert routes
