#!/usr/bin/env bash
# Wrapper for the org-directory CSV import (identity-ingestion.md §3.3, plan G4).
#
#   utils/directory_import.sh --source path/to/csvdir            # dry run (default)
#   utils/directory_import.sh --source path/to/csvdir --apply    # write via directory.sync()
#
# The source dir holds users.csv (required), teams.csv and members.csv (optional).
set -euo pipefail
exec python -m server.directory.cli_directory_import "$@"
