#!/usr/bin/env bash
# Backfill git-source history for a repo. Usage: utils/git_backfill.sh --repo owner/name [options]
set -euo pipefail
cd "$(dirname "$0")/.."
exec python -m server.ingest.git.cli_backfill "$@"
