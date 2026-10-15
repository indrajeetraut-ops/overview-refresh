#!/usr/bin/env bash
# Diagnose the GitHub webhook + API setup from GitHub's side (App JWT only).
set -euo pipefail
cd "$(dirname "$0")/.."
exec python -m server.ingest.git.cli_diagnose "$@"
