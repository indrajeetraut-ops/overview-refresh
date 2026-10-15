#!/usr/bin/env bash
# Backfill commit line stats via the compare API for pushes ingested before the git_commit_stats
# worker existed (Track churn/reopen, backfill 4b). Dry-run by default; pass --apply to write.
#
#   utils/backfill_commit_stats.sh                       # every repo, dry run
#   utils/backfill_commit_stats.sh --repo owner/name --apply
set -euo pipefail
exec python -m server.ingest.git.cli_backfill_commit_stats "$@"
