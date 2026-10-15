#!/usr/bin/env bash
# Credential smoke test: list App installations and the repos each can see.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python -m server.ingest.git.cli_installations "$@"
