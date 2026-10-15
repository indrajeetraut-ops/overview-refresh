#!/usr/bin/env bash
# Import the team roster into the AWS team stack's directory WITHOUT forking people in two.
# Run it where utils/aws_up.sh ran, AFTER the collector has swept everyone's sessions.
#
#   utils/aws_directory_import.sh --roster test-roster/csv_roster \
#       --link anil=anilk-talium --link sumandra=sum-talium            # dry run
#   utils/aws_directory_import.sh ... --apply                          # write
#   utils/aws_directory_import.sh --local ...                          # the laptop rehearsal
#
# Why the --link step exists: the directory merges a roster row into an EXISTING user only
# through the GitHub login (identity_links, else the legacy users.github_user column). Session
# users are created by the collector under the uploader's user id ("anil", "sumandra"), and
# their users.github_user is filled only if talium-sync declared one -- which it does only when
# `github_user` is set in ~/.talium/sync.yaml or TALIUM_GITHUB_LOGIN is exported. Without it,
# the import mints a second person ("github:anilk-talium") holding the team membership while
# "anil" keeps all the spend: team screens then show the roster with zero cost.
#
# --link <upload user id>=<github login> sets users.github_user on an existing session user
# where it is still empty, so the import below "enriches, never duplicates" (reconcile.py).
# Once every client declares its login, --link is unnecessary.
#
# Before --apply writes anything, it refuses when a roster github_login would create a NEW user
# while unlinked session users still exist -- the exact shape of the fork above.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "[directory] $*" >&2; exit 1; }
usage() { echo "usage: $0 [--local] --roster DIR [--link USER=LOGIN ...] [--apply] [--force]" >&2; exit 2; }

LOCAL=0; APPLY=0; FORCE=0; ROSTER=""; LINKS=()
while (( $# )); do
  case "$1" in
    --local)  LOCAL=1 ;;
    --apply)  APPLY=1 ;;
    --force)  FORCE=1 ;;
    --roster) shift; ROSTER="${1:-}" ;;
    --link)   shift; LINKS+=("${1:-}") ;;
    *) usage ;;
  esac
  shift
done
[[ -n "$ROSTER" ]] || usage
[[ -f "$ROSTER/users.csv" ]] || die "no users.csv in $ROSTER"

if (( LOCAL )); then
  COMPOSE=(docker compose -p talium-aws-local --env-file "$ROOT/.env.aws-local"
           -f docker/docker-compose.aws.yml -f docker/docker-compose.aws.local.yml)
else
  COMPOSE=(docker compose --env-file "$ROOT/.env.aws" -f docker/docker-compose.aws.yml)
fi

sql() { "${COMPOSE[@]}" exec -T postgres psql -U postgres -d econ -v ON_ERROR_STOP=1 -tA "$@"; }
# Identifiers here are user ids and GitHub logins; refuse anything that is not plainly one,
# rather than quoting our way around it.
ident_ok() { [[ "$1" =~ ^[A-Za-z0-9._:@-]+$ ]]; }

# --- 1. link session users to their GitHub logins -------------------------------------------
# ${A[@]+...}: an empty array is "unbound" under set -u in bash 3.2 (macOS, the rehearsal).
for pair in ${LINKS[@]+"${LINKS[@]}"}; do
  [[ "$pair" == *=* ]] || die "--link wants USER=LOGIN, got '$pair'"
  user="${pair%%=*}"; login="${pair#*=}"
  ident_ok "$user" && ident_ok "$login" || die "unexpected characters in '$pair'"
  current="$(sql -c "select coalesce(github_user, '') from users where user_id = '$user'")" \
    || die "cannot reach postgres -- is the stack up?"
  [[ -n "$(sql -c "select 1 from users where user_id = '$user'")" ]] \
    || die "no user '$user' yet -- let the collector sweep their sessions first"
  if [[ "$current" == "$login" ]]; then
    echo "[directory] $user already carries github_user=$login"
  elif [[ -n "$current" ]]; then
    die "$user already carries github_user=$current, not $login -- refusing to overwrite"
  elif (( APPLY )); then
    sql -c "update users set github_user = '$login' where user_id = '$user' and github_user is null" >/dev/null
    echo "[directory] linked $user -> github:$login"
  else
    echo "[directory] would link $user -> github:$login"
  fi
done

# --- 2. guard: would the roster fork anyone? ------------------------------------------------
if (( APPLY && ! FORCE )); then
  # Roster logins that resolve to no existing user (neither on the link spine nor the legacy
  # column) are created fresh -- fine for someone with no sessions, a fork for someone with.
  logins="$(python3 -c "
import csv, sys
rows = csv.DictReader(open('$ROSTER/users.csv', newline=''))
print(' '.join(r['github_login'].strip() for r in rows if (r.get('github_login') or '').strip()))")"
  new=()
  for login in $logins; do
    ident_ok "$login" || die "unexpected characters in roster login '$login'"
    hit="$(sql -c "select 1 from users where github_user = '$login'
                   union select 1 from identity_links where provider = 'github' and external_id = '$login'")"
    [[ -n "$hit" ]] || new+=("$login")
  done
  unlinked="$(sql -c "select string_agg(u.user_id, ', ') from users u
                      where u.github_user is null and exists (select 1 from sessions s where s.user_id = u.user_id)
                        and not exists (select 1 from identity_links l where l.user_id = u.user_id)")"
  if (( ${#new[@]} )) && [[ -n "$unlinked" ]]; then
    cat >&2 <<EOF
[directory] refusing: roster login(s) ${new[*]} match no existing user, while session user(s)
[directory] ${unlinked} have no GitHub login. Importing now would create new people and leave
[directory] the spend on the old ones. Add --link <user>=<login> for each, or --force if these
[directory] really are different people.
EOF
    exit 1
  fi
fi

# --- 3. import ------------------------------------------------------------------------------
ROSTER_ABS="$(cd "$ROSTER" && pwd)"
IMPORT_ARGS=(--source /roster)
(( APPLY )) && IMPORT_ARGS+=(--apply)
"${COMPOSE[@]}" run --rm --no-deps -T -v "${ROSTER_ABS}:/roster:ro" api \
  python -m server.directory.cli_directory_import "${IMPORT_ARGS[@]}"

if (( ! APPLY )); then
  echo
  echo "[directory] dry run -- nothing written. Re-run with --apply."
  (( ${#LINKS[@]} )) && echo "[directory] (the counts above predate the --link step, which a dry run does not write:" \
    && echo "[directory]  a 'created' for a linked login becomes an 'updated' on --apply)"
  exit 0
fi

echo
sql -c "select '  ' || user_id || '  ' || coalesce(email, '-') || '  ' || coalesce(github_user, '-') from users order by 1"
cat <<EOF

The viewer baked into the real dashboard must be one of the user ids above (or a linked login).
If ECON_VIEWER_ID is not set yet:  ECON_VIEWER_ID=<user id> utils/aws_up.sh$( (( LOCAL )) && printf ' --local')
EOF
