#!/usr/bin/env bash
# Dump the running local stack's database into deploy/seed/, so the stop-gap deployment comes
# up already full of sessions instead of empty and waiting for a re-fold.
#
#   utils/stopgap_seed.sh
#
# Run this on the machine where the local stack is running, then copy the repo (including
# deploy/seed/) to the target host and run utils/stopgap_up.sh there.
#
# Plain SQL, gzipped, on purpose: the postgres image's entrypoint gunzips and pipes *.sql.gz
# in /docker-entrypoint-initdb.d straight to psql. A custom-format dump would need pg_restore,
# which that entrypoint does not call.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_CONTAINER="${SRC_CONTAINER:-talium_stack_pg}"
SRC_DB="${SRC_DB:-econ}"
OUT="${OUT:-$ROOT/deploy/seed/econ-seed.sql.gz}"

die() { echo "[seed] $*" >&2; exit 1; }

docker inspect "$SRC_CONTAINER" >/dev/null 2>&1 \
  || die "no container '$SRC_CONTAINER' -- start the local stack first (utils/stack_up.sh --jsonl)"

# Checked up front, through the container's own socket (no host port involved), so a missing
# database is a clear message rather than a pg_dump error halfway through a pipeline.
if [[ -z "$(docker exec "$SRC_CONTAINER" psql -U postgres -tAc \
      "select 1 from pg_database where datname = '${SRC_DB}'" 2>/dev/null)" ]]; then
  have="$(docker exec "$SRC_CONTAINER" psql -U postgres -tAc \
      "select string_agg(datname, ', ') from pg_database where not datistemplate" 2>/dev/null || true)"
  die "no database '${SRC_DB}' in ${SRC_CONTAINER} (it has: ${have:-?})"
fi

mkdir -p "$(dirname "$OUT")"

echo "[seed] dumping ${SRC_DB} from ${SRC_CONTAINER} ..."
# Into a temp file beside OUT, moved over it only once the whole pipeline has succeeded: a
# failed pg_dump must never truncate the previous good dump (an empty gzip is 20 bytes, so a
# size check afterwards cannot tell it from a real one).
TMP_OUT="$(mktemp "${OUT}.XXXXXX")"
trap 'rm -f "$TMP_OUT"' EXIT
# --no-owner/--no-privileges: the target cluster has different roles, and nothing in this
# schema depends on ownership. Without them the restore emits noisy errors for every object.
docker exec "$SRC_CONTAINER" \
    pg_dump -U postgres -d "$SRC_DB" --no-owner --no-privileges \
  | gzip -9 > "$TMP_OUT" \
  || die "pg_dump of ${SRC_DB} failed -- ${OUT#"$ROOT/"} left as it was"
# grep -c, not -q: -q exits at the first match, gunzip dies of SIGPIPE, and pipefail then
# reports a perfectly good dump as a failure.
TABLES="$(gunzip -c "$TMP_OUT" | grep -c "^CREATE TABLE" || true)"
(( TABLES > 0 )) || die "dump of ${SRC_DB} has no tables -- ${OUT#"$ROOT/"} left as it was"
chmod 644 "$TMP_OUT"
mv -f "$TMP_OUT" "$OUT"

echo "[seed] wrote $OUT ($(du -h "$OUT" | cut -f1))"
echo
echo "Row counts captured:"
docker exec "$SRC_CONTAINER" psql -U postgres -d "$SRC_DB" -tAc "
  SELECT '  sessions           ' || count(*) FROM sessions;
  SELECT '  session_info       ' || count(*) FROM session_info;
  SELECT '  otel_api_requests  ' || count(*) FROM otel_api_requests;
  SELECT '  users              ' || count(*) FROM users;"
echo
echo "This file is gitignored (deploy/seed/*.sql.gz). Copy it to the host out of band."
