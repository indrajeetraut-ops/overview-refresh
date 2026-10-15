"""Dashboard API HTTP entry point. Run: python -m server.api.cli_serve_api [--config PATH] [--port N]"""
import argparse
import os

import uvicorn

from server.storage.config import load_config as load_storage_config

from .app import create_app
from .config import load_config

#: Env var a forked uvicorn worker reads to rebuild its own app/engine/pool (api-latency plan
#: §2.2) -- uvicorn's `--workers` forks from an import string, not an app object, so each
#: worker process needs to load the config itself rather than inherit the parent's.
_CONFIG_ENV = "TALIUM_API_CONFIG"


def app_from_env():
    """uvicorn factory: each worker process builds its own app, engine, pool and query catalog
    from the config path `main()` exported (a forked worker cannot inherit the parent's engine)."""
    config = load_config(os.environ.get(_CONFIG_ENV, "config/api.yaml"))
    return create_app(config, load_storage_config(config.storage_config))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the dashboard read API.")
    parser.add_argument("--config", default="config/api.yaml")
    # Lets a second API process (e.g. against a *_synth database, plan §5 P10b) run alongside
    # the primary one on its own port, without a second config file.
    parser.add_argument("--port", type=int, default=None,
                        help="override config's http.port (e.g. to serve a synthetic dataset "
                             "on its own port)")
    parser.add_argument("--workers", type=int, default=None,
                        help="override config's http.workers")
    args = parser.parse_args()

    config = load_config(args.config)  # fail fast in the parent on a bad config
    os.environ[_CONFIG_ENV] = args.config
    port = args.port if args.port is not None else config.http.port
    uvicorn.run("server.api.cli_serve_api:app_from_env", factory=True,
                host=config.http.host, port=port,
                workers=args.workers or config.http.workers)


if __name__ == "__main__":
    main()
