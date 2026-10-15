"""Ingest HTTP entry point. Run: python -m server.ingest.cli_serve [--config PATH]"""
import argparse

import uvicorn

from common.msging.config import load_config as load_msging_config
from server.storage.config import load_config as load_storage_config

from .app import create_app
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ingest HTTP service.")
    parser.add_argument("--config", default="config/ingest.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    storage_cfg = load_storage_config(config.storage_config)
    msging_cfg = load_msging_config(config.msging_config)

    app = create_app(config, msging_cfg, storage_cfg)
    uvicorn.run(app, host=config.http.host, port=config.http.port)


if __name__ == "__main__":
    main()
