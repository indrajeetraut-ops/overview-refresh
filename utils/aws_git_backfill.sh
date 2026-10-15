#!/usr/bin/env bash
# Backfill GitHub history (PRs, reviews, issues) into the AWS team stack through the GitHub App.
# Run it where utils/aws_up.sh ran, AFTER utils/aws_directory_import.sh.
#
#   utils/aws_git_backfill.sh                          every repo the installation covers (talium-ai)
#   utils/aws_git_backfill.sh --repo talium-ai/Docs   one repo
#   utils/aws_git_backfill.sh --local                 the laptop rehearsal
#
# Incremental: each repo keeps a cursor, so a re-run fetches only what changed -- cheap enough for
# a cron on the host (docs/deployment.md §0.5). The worker then links PRs to sessions by commit sha
# (git_correlate) and classifies them (pr_classify) on its own; nothing to refold.
#
# ORDER MATTERS. A PR author resolves to a person through users.github_user / identity_links; a
# login that matches nobody is auto-created as `github:<login>` (ingest/git/apply.py,
# create_user_for_github_login). Run this before the roster import has linked `anil` to
# `anilk-talium`, and every PR lands on a new `github:anilk-talium` while the spend stays on
# `anil` -- the same fork aws_directory_import.sh guards against. So this refuses while any
# session user still has no GitHub login (--force to override).
#
# GitHub App settings come from the environment, else from GITHUB_ENV_FILE (default .env.github,
# gitignored): TALIUM_GITHUB_APP_ID, TALIUM_GITHUB_CLIENT_ID, TALIUM_GITHUB_PRIVATE_KEY_PATH (a file
# on this host; mounted read-only into the run), TALIUM_GITHUB_WEBHOOK_SECRET,
# TALIUM_GITHUB_STATE_SECRET. Their values are never printed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "[git] $*" >&2; exit 1; }

LOCAL=0; FORCE=0; TARGET=(--org talium-ai)
while (( $# )); do
  case "$1" in
    --local) LOCAL=1 ;;
    --force) FORCE=1 ;;
    --org)   shift; TARGET=(--org "${1:?--org needs a value}") ;;
    --repo)  shift; TARGET=(--repo "${1:?--repo needs owner/name}") ;;
    *) echo "usage: $0 [--local] [--org LOGIN | --repo OWNER/NAME] [--force]" >&2; exit 2 ;;
  esac
  shift
done

if (( LOCAL )); then
  ENV_FILE="$ROOT/.env.aws-local"
  COMPOSE=(docker compose -p talium-aws-local --env-file "$ENV_FILE"
           -f docker/docker-compose.aws.yml -f docker/docker-compose.aws.local.yml)
else
  ENV_FILE="$ROOT/.env.aws"
  COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker/docker-compose.aws.yml)
fi
[[ -f "$ENV_FILE" ]] || die "${ENV_FILE#"$ROOT/"} not found -- run utils/aws_up.sh first"
ORG_ID="$(sed -n 's/^TALIUM_ORG_ID=//p' "$ENV_FILE" | head -1)"
: "${ORG_ID:?TALIUM_ORG_ID missing from ${ENV_FILE}}"

# --- GitHub App settings -------------------------------------------------------------------
GITHUB_ENV_FILE="${GITHUB_ENV_FILE:-$ROOT/.env.github}"
if [[ -z "${TALIUM_GITHUB_APP_ID:-}" && -f "$GITHUB_ENV_FILE" ]]; then
  set -a; # shellcheck disable=SC1090
  source "$GITHUB_ENV_FILE"; set +a
fi
for v in TALIUM_GITHUB_APP_ID TALIUM_GITHUB_CLIENT_ID TALIUM_GITHUB_PRIVATE_KEY_PATH \
         TALIUM_GITHUB_WEBHOOK_SECRET TALIUM_GITHUB_STATE_SECRET; do
  [[ -n "${!v:-}" ]] || die "$v is not set (environment or ${GITHUB_ENV_FILE#"$ROOT/"})"
done
[[ -f "$TALIUM_GITHUB_PRIVATE_KEY_PATH" ]] || die "no private key file at TALIUM_GITHUB_PRIVATE_KEY_PATH"
KEY_ABS="$(cd "$(dirname "$TALIUM_GITHUB_PRIVATE_KEY_PATH")" && pwd)/$(basename "$TALIUM_GITHUB_PRIVATE_KEY_PATH")"

# --- guard: identities linked first ---------------------------------------------------------
sql() { "${COMPOSE[@]}" exec -T postgres psql -U postgres -d econ -v ON_ERROR_STOP=1 -tA "$@"; }
if (( ! FORCE )); then
  unlinked="$(sql -c "select string_agg(u.user_id, ', ') from users u
                      where u.github_user is null and exists (select 1 from sessions s where s.user_id = u.user_id)
                        and not exists (select 1 from identity_links l where l.user_id = u.user_id)")" \
    || die "cannot reach postgres -- is the stack up?"
  if [[ -n "$unlinked" ]]; then
    cat >&2 <<EOF
[git] refusing: session user(s) ${unlinked} have no GitHub login yet, so their PRs would be
[git] credited to new github:<login> people instead. Run utils/aws_directory_import.sh with
[git] --link <user>=<login> first, or pass --force if they genuinely have no GitHub account.
EOF
    exit 1
  fi
fi

# --- backfill -------------------------------------------------------------------------------
# Env passed by NAME (-e VAR), so values never appear on a command line or in `ps`.
echo "[git] backfilling ${TARGET[*]} as org ${ORG_ID} ..."
OUT="$(mktemp)"; trap 'rm -f "$OUT"' EXIT
if ! "${COMPOSE[@]}" run --rm --no-deps -T \
    -v "${KEY_ABS}:/secrets/github-app.pem:ro" \
    -e TALIUM_GITHUB_APP_ID -e TALIUM_GITHUB_CLIENT_ID \
    -e TALIUM_GITHUB_WEBHOOK_SECRET -e TALIUM_GITHUB_STATE_SECRET \
    -e TALIUM_GITHUB_PRIVATE_KEY_PATH=/secrets/github-app.pem \
    api python -m server.ingest.git.cli_backfill "${TARGET[@]}" --org-id "$ORG_ID" >"$OUT" 2>&1; then
  tail -30 "$OUT" >&2
  die "backfill failed (output above)"
fi
grep -E "backfill_done|backfill_repo_failed" "$OUT" | sed 's/^/  /' || true

echo "[git] git_artifacts now:"
sql -c "select '  ' || kind || '  ' || count(*) from git_artifacts group by kind order by kind"
echo "[git] the worker links PRs to sessions (git_correlate) and classifies them over the next minute."
