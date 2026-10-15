#!/usr/bin/env bash
# Run one talium-sync pass against the LOCAL sync stack (utils/stack_up.sh --sync).
#
#   utils/sync_local.sh                     # build from ../upload, enroll, run --once
#   utils/sync_local.sh --source DIR        # discover DIR instead of ~/.claude/projects
#   utils/sync_local.sh --user U --github-login L
#                                           # declared identity, as upload_claude_sessions.sh's
#                                           # [user] [github_login]; defaults: OS username, and
#                                           # $TALIUM_GITHUB_LOGIN else `git config github.user`
#   utils/sync_local.sh -- --dry-run        # anything after -- goes to `talium-sync run`
#
#   TALIUM_SYNC_BIN=/path/to/talium-sync    # use this binary instead of building one
#   TALIUM_UPLOAD_REPO=/path/to/upload      # where to build from (default: ../upload)
#
# Never touches ~/.talium/sync.yaml (which may be enrolled against production): the config is
# var/talium-sync.local.yaml, always passed explicitly. The TALIUM_* client env overrides are
# cleared for the same reason -- they outrank the config file.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=utils/_sync_env.sh
source "$ROOT/utils/_sync_env.sh"

SOURCE_DIR=""
DECL_USER=""
DECL_GITHUB=""
RUN_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE_DIR="$(cd "$2" && pwd)"; shift 2 ;;
    --user) DECL_USER="$2"; shift 2 ;;
    --github-login) DECL_GITHUB="$2"; shift 2 ;;
    --) shift; RUN_ARGS=("$@"); break ;;
    *) echo "usage: $0 [--source DIR] [--user U] [--github-login L] [-- <talium-sync run args>]" >&2; exit 2 ;;
  esac
done

if ! sync_env_load "$ROOT"; then
  echo "[sync] no $(sync_env_file "$ROOT") -- bring the stack up first: utils/stack_up.sh --sync" >&2
  exit 1
fi

BIN="${TALIUM_SYNC_BIN:-}"
if [[ -z "$BIN" ]]; then
  REPO="${TALIUM_UPLOAD_REPO:-$ROOT/../upload}"
  BIN="$ROOT/var/bin/talium-sync"
  mkdir -p "$(dirname "$BIN")"
  echo "[sync] building talium-sync from ${REPO} ($(cd "$REPO" && git rev-parse --short HEAD 2>/dev/null || echo '?'))"
  (cd "$REPO" && go build -o "$BIN" ./cmd/talium-sync)
fi

CFG="$ROOT/var/talium-sync.local.yaml"
URL="http://localhost:${STACK_SYNC_PORT:-8444}"
unset TALIUM_BASE_URL TALIUM_ENROLLMENT_TOKEN TALIUM_ORG TALIUM_USER TALIUM_INTERVAL
# Declared identity rides the client's own env tier (TALIUM_USER; TALIUM_GITHUB_LOGIN is
# discover.ResolveGitHubUser's second source) -- set only when asked for, on purpose.
[[ -n "$DECL_USER" ]] && export TALIUM_USER="$DECL_USER"
[[ -n "$DECL_GITHUB" ]] && export TALIUM_GITHUB_LOGIN="$DECL_GITHUB"

# Re-enrolling every run is idempotent and keeps the config in step with the stack's secret.
"$BIN" enroll --token "$TALIUM_SYNC_ENROLLMENT_SECRET" --org "$TALIUM_ORG_ID" --url "$URL" \
  --config "$CFG" >/dev/null
echo "[sync] $("$BIN" version) -> ${URL} as org ${TALIUM_ORG_ID}" \
     "user ${TALIUM_USER:-<os user>} github ${TALIUM_GITHUB_LOGIN:-<git config>} (config ${CFG})"

if [[ -n "$SOURCE_DIR" ]]; then
  echo "[sync] discovering ${SOURCE_DIR}"
  export CLAUDE_PROJECTS_DIR="$SOURCE_DIR"
fi
# talium-sync logs only to its own file (macOS: ~/Library/Logs/talium-sync/), never stdout --
# echo this pass's lines so the plan/upload/commit outcome is visible here.
LOG="$HOME/Library/Logs/talium-sync/talium-sync.log"
BEFORE=$( [[ -f "$LOG" ]] && wc -l <"$LOG" || echo 0 )
STATUS=0
"$BIN" run --once --config "$CFG" "${RUN_ARGS[@]+"${RUN_ARGS[@]}"}" || STATUS=$?
[[ -f "$LOG" ]] && tail -n +"$((BEFORE + 1))" "$LOG"
exit "$STATUS"
