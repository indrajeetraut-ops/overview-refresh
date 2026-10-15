#!/usr/bin/env bash
# Backfill PR reopen history from the GitHub timeline into git_artifact_events
# (Track churn/reopen, backfill 4a). Dry-run by default; pass --apply to write.
#
#   utils/backfill_reopens.sh --repo owner/name
#   utils/backfill_reopens.sh --repo owner/name --apply
#   utils/backfill_reopens.sh --org --apply
set -euo pipefail
exec python -m server.ingest.git.cli_backfill_reopens "$@"
