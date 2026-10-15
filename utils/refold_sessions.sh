#!/usr/bin/env bash
# Re-fold stored sessions with the current fold (no re-ingest; events come from sessions.signal_blob).
#
#   utils/refold_sessions.sh                         every session
#   utils/refold_sessions.sh --session-id <id> ...   just these
#   utils/refold_sessions.sh --dry-run               list what would be re-folded
#
# Against the local docker stack, run it inside the worker container so it uses the stack's DB:
#   docker exec talium_stack_worker python -m server.worker.cli_refold
set -euo pipefail
exec python -m server.worker.cli_refold "$@"
