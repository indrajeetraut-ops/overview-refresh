"""Uploader HTTP entry point. Run: python -m server.uploader.cli_serve [--config PATH]"""
import argparse

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the batch uploader HTTP service.")
    parser.add_argument("--config", default="config/uploader.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    app = create_app(config)
    uvicorn.run(app, host=config.http.host, port=config.http.port)


if __name__ == "__main__":
    main()
