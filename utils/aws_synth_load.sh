#!/usr/bin/env bash
# (Re)load the synthetic org into the AWS team stack's Postgres, then restart api_synth.
# Run it where utils/aws_up.sh ran, after the stack is up.
#
#   utils/aws_synth_load.sh                        first load
#   utils/aws_synth_load.sh --replace              drop and reload (after a regenerated dump)
#   utils/aws_synth_load.sh --local [--replace]    the laptop rehearsal
#
# The dump comes from the laptop -- utils/synth_dump.sh writes deploy/seed/econ_synth.sql.gz
# (gitignored); copy it to the same path here. Shipping a dump rather than running the
# generator + loader on the host keeps econ-forecast's toolchain and its gitignored artifacts
# off this box. The rows are loaded in their final shape: no worker ever folds econ_synth.
#
#   SYNTH_DUMP    the dump to load       (default: deploy/seed/econ_synth.sql.gz)
#   SYNTH_DB_NAME the target database    (default: econ_synth; must end in _synth)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "[synth] $*" >&2; exit 1; }

LOCAL=0; REPLACE=0
for arg in "$@"; do
  case "$arg" in
    --local)   LOCAL=1 ;;
    --replace) REPLACE=1 ;;
    *) echo "usage: $0 [--local] [--replace]" >&2; exit 2 ;;
  esac
done

SYNTH_DUMP="${SYNTH_DUMP:-deploy/seed/econ_synth.sql.gz}"
DB="${SYNTH_DB_NAME:-econ_synth}"

# The loader's own guard (server.storage.cli_load_synthetic): never anything but a *_synth DB.
[[ "$DB" == *_synth ]] || die "refusing '$DB' -- the synthetic database name must end in _synth"
[[ -s "$SYNTH_DUMP" ]] || die "no dump at $SYNTH_DUMP -- run utils/synth_dump.sh on the laptop and copy it here"

if (( LOCAL )); then
  COMPOSE=(docker compose -p talium-aws-local --env-file "$ROOT/.env.aws-local"
           -f docker/docker-compose.aws.yml -f docker/docker-compose.aws.local.yml)
else
  COMPOSE=(docker compose --env-file "$ROOT/.env.aws" -f docker/docker-compose.aws.yml)
fi

psql_admin() { "${COMPOSE[@]}" exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 "$@"; }

EXISTS="$(psql_admin -tAc "select 1 from pg_database where datname='${DB}'" | tr -d '\r\n')" \
  || die "cannot reach postgres -- is the stack up? (utils/aws_up.sh$( (( LOCAL )) && printf ' --local'))"

if [[ -n "$EXISTS" ]]; then
  (( REPLACE )) || die "$DB already exists -- pass --replace to drop and reload it"
  echo "[synth] dropping $DB ..."
  # WITH (FORCE): api_synth holds pooled connections to it.
  psql_admin -d postgres -c "DROP DATABASE \"${DB}\" WITH (FORCE)"
fi

echo "[synth] creating $DB and restoring $SYNTH_DUMP ($(du -h "$SYNTH_DUMP" | cut -f1)) ..."
psql_admin -d postgres -c "CREATE DATABASE \"${DB}\""
gunzip -c "$SYNTH_DUMP" | psql_admin -q -d "$DB" >/dev/null

echo "[synth] restarting api_synth (drops connections pooled against the old database) ..."
"${COMPOSE[@]}" restart api_synth >/dev/null

echo "[synth] loaded:"
psql_admin -d "$DB" -tAc "
  SELECT '  users         ' || count(*) FROM users;
  SELECT '  sessions      ' || count(*) FROM sessions;
  SELECT '  session_info  ' || count(*) FROM session_info;"
