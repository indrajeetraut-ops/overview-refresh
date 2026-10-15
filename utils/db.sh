#!/usr/bin/env bash
# Wrapper for the storage DB management CLI (storage.md §6).
set -euo pipefail
exec python -m server.storage.cli_db "$@"
