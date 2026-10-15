#!/usr/bin/env bash
# Wrapper for the ingest HTTP service (ingest.md §3).
set -euo pipefail
exec python -m server.ingest.cli_serve "$@"
