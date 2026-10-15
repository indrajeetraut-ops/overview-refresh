#!/usr/bin/env bash
# Ship your local Claude Code session files to the stack's uploader -- the JSONL half of the
# hybrid. The OTel half needs nothing: Claude Code exports it on its own.
#
#   utils/upload_claude_sessions.sh [org] [user] [github_login]
#
# The collector sweeps the staging bucket every 30s afterwards, so nothing else to run.
#
# Against the AWS demo instead of the local stack, point it at the deployed uploader. That
# route is behind basic auth in Caddy, because the uploader itself is unauthenticated:
#
#   TALIUM_UPLOAD_URL=https://econ.example.com/upload \
#   TALIUM_UPLOAD_AUTH=demo:secret \
#     utils/upload_claude_sessions.sh talium-demo anil
#
# Re-running this is safe and cheap by design, which is what makes an occasional manual push
# a reasonable way to keep the demo current: the collector skips any file whose size and
# leading 8 KiB are unchanged, and the fold only ever extends an append-only prefix, so a
# resend of an unchanged session appends nothing.
set -euo pipefail

ORG="${1:-local-dev}"
USER_ID="${2:-$(whoami)}"
# Self-declared GitHub login (case doesn't matter -- the server normalizes it), so the ingest
# fold handler can pre-populate `users.github_user` for $USER_ID and git-source's own PR-author
# resolution finds this identity instead of auto-creating a separate "github:<login>" one.
GITHUB_LOGIN="${3:-${TALIUM_GITHUB_LOGIN:-}}"
PORT="${STACK_UPLOADER_PORT:-8080}"
SRC="${CLAUDE_PROJECTS_DIR:-$HOME/.claude}"
UPLOAD_URL="${TALIUM_UPLOAD_URL:-http://localhost:${PORT}}"

if [[ ! -d "$SRC/projects" ]]; then
  echo "no $SRC/projects -- nothing to upload" >&2
  exit 1
fi

# The vendor account identity OTel telemetry carries in `user.account_uuid`, a different
# namespace than $USER_ID above -- sending it lets the server link an otel_only session (no
# matching JSONL session_id ever arrives for it) back to this same $USER_ID instead of showing
# the raw account UUID. Read from Claude Code's own OAuth credentials; silently omitted if
# missing (older Claude Code, no OAuth login, or python3 unavailable) -- an upload must never
# fail over an optional field.
ACCOUNT_UUID="$(python3 -c '
import json, sys
try:
    with open(sys.argv[1]) as f:
        creds = json.load(f)
    uuid = creds.get("oauthAccount", {}).get("accountUuid")
    if uuid:
        print(uuid)
except Exception:
    pass
' "$HOME/.claude.json" 2>/dev/null || true)"

QUERY="org=${ORG}&user=${USER_ID}"
if [[ -n "$ACCOUNT_UUID" ]]; then
  QUERY="${QUERY}&account_uuid=${ACCOUNT_UUID}"
fi
if [[ -n "$GITHUB_LOGIN" ]]; then
  QUERY="${QUERY}&github_user=${GITHUB_LOGIN}"
fi

# A session's JSONL only ever carries its `cwd` -- never a stable repo identity (two clones of
# the same repo on different machines, or two unrelated repos sharing a folder name, both
# collide onto the same basename). This machine has the actual repos on disk right now, so
# resolve each distinct cwd's real `origin` remote here and ship it as a sidecar the collector
# matches back onto sessions by cwd (source_reader.py's GIT_REMOTES_MANIFEST_REL). Best-effort
# throughout: a cwd that's not a git repo, has no `origin`, or a missing git/python3 just
# produces no entry for that cwd -- never fails the upload.
REMOTES_FILE="$SRC/projects/.talium-git-remotes.json"
trap 'rm -f "$REMOTES_FILE"' EXIT
python3 - "$SRC/projects" "$REMOTES_FILE" <<'PYEOF' 2>/dev/null || true
import json
import subprocess
import sys
from pathlib import Path

projects_dir, out_path = sys.argv[1], sys.argv[2]

cwds = set()
for jsonl in Path(projects_dir).rglob("*.jsonl"):
    if "/subagents/" in str(jsonl):
        continue
    try:
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except json.JSONDecodeError:
                    continue
                if cwd:
                    cwds.add(cwd)
                    break
    except OSError:
        continue

remotes = {}
for cwd in cwds:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        continue
    if result.returncode == 0 and result.stdout.strip():
        remotes[cwd] = result.stdout.strip()

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(remotes, f)
PYEOF

# Basic-auth credentials for the deployed uploader, as user:password. Unset against the
# local stack, which has no proxy in front of it.
AUTH_ARGS=()
if [[ -n "${TALIUM_UPLOAD_AUTH:-}" ]]; then
  AUTH_ARGS=(-u "$TALIUM_UPLOAD_AUTH")
fi

echo "[upload] tarring $SRC/projects -> ${UPLOAD_URL} as org=${ORG} user=${USER_ID}${ACCOUNT_UUID:+ account_uuid=$ACCOUNT_UUID}${GITHUB_LOGIN:+ github_user=$GITHUB_LOGIN}"
# COPYFILE_DISABLE stops macOS tar writing AppleDouble `._*` sidecars, which the uploader
# would otherwise have to filter out (docs/manual-testing.md §5).
# ${arr[@]+"${arr[@]}"} rather than a bare "${arr[@]}": macOS ships bash 3.2, where expanding
# an empty array under `set -u` is an unbound-variable error rather than nothing at all.
COPYFILE_DISABLE=1 tar czf - -C "$SRC/.." "$(basename "$SRC")/projects" \
  | curl -sS -X POST "${UPLOAD_URL}/v1/uploads?${QUERY}" \
      ${AUTH_ARGS[@]+"${AUTH_ARGS[@]}"} \
      -H "Content-Type: application/gzip" \
      --data-binary @-
echo
