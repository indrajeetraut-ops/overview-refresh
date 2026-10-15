#!/usr/bin/env bash
# Wrapper for the worker fold loop (ingest.md §5).
set -euo pipefail
exec python -m server.worker.cli_work "$@"
