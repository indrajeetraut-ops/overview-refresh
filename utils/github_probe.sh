#!/usr/bin/env bash
# Read a repo's recent PRs via REST and GraphQL. Usage: utils/github_probe.sh --repo owner/name
set -euo pipefail
cd "$(dirname "$0")/.."
exec python -m server.ingest.git.cli_probe "$@"
