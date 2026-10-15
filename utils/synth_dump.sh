#!/usr/bin/env bash
# On the laptop: produce deploy/seed/econ_synth.sql.gz (gitignored) for the AWS team stack.
# Copy it to the host's deploy/seed/ and run utils/aws_synth_load.sh there.
#
#   utils/synth_dump.sh                              build from the dataset (default; see below)
#   utils/synth_dump.sh --dataset <org-expand dir>   ... from another org-expand output
#   utils/synth_dump.sh --from-container <name>      dump an econ_synth that already exists in a
#                                                    running Postgres container instead
#
# Default: built straight from the org-expand dataset (econ-forecast/artifacts/org-demo/org) in a
# throwaway Postgres container on a private docker network, using the loader
# (server.storage.cli_load_synthetic) from this repo's own image. It depends on no local
# database, no published port and no running stack, so the dump always reflects the dataset
# rather than whatever state a local econ_synth happens to be in. Regenerate the dataset with the
# econ-forecast generator first if the org has changed.
#
# Forecast: when <org-demo>/forecast/forecast_export.json exists next to the dataset (the dataset
# argument is <org-demo>/org; the export is what econ-forecast/utils/dashboard_forecast.sh wrote),
# the loader also loads it (--forecast-export), so the dump carries forecast_runs and
# forecast_scope_series. Without that file the script behaves as before.
#
# To regenerate the dataset, forecast it and dump it in one go, use utils/synth_demo_refresh.sh --dump.
#
# The dump itself is utils/stopgap_seed.sh's (plain SQL, gzipped, --no-owner/--no-privileges,
# written to a temp file and moved into place only on success).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "[synth-dump] $*" >&2; exit 1; }

DATASET="econ-forecast/artifacts/org-demo/org"
FROM_CONTAINER=""
while (( $# )); do
  case "$1" in
    --dataset)        shift; DATASET="${1:?--dataset needs a directory}" ;;
    --from-container) shift; FROM_CONTAINER="${1:?--from-container needs a container name}" ;;
    *) echo "usage: $0 [--dataset DIR | --from-container NAME]" >&2; exit 2 ;;
  esac
  shift
done

OUT="${OUT:-$ROOT/deploy/seed/econ_synth.sql.gz}"

if [[ -n "$FROM_CONTAINER" ]]; then
  SRC_CONTAINER="$FROM_CONTAINER" SRC_DB=econ_synth OUT="$OUT" exec "$ROOT/utils/stopgap_seed.sh"
fi

[[ -f "$DATASET/config.yaml" ]] || die "no org-expand dataset at $DATASET (config.yaml missing)"
DATASET_ABS="$(cd "$DATASET" && pwd)"
FORECAST_EXPORT="$(dirname "$DATASET_ABS")/forecast/forecast_export.json"
command -v docker >/dev/null || die "docker is not installed"

# Unique per run, removed on any exit.
RUN="talium-synth-dump-$$"
NET="$RUN-net"
PG="$RUN-pg"
IMAGE="talium-synth-tools:local"
cleanup() {
  docker rm -f "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[synth-dump] building the loader image (cached after the first run) ..."
docker build -q -f docker/Dockerfile -t "$IMAGE" . >/dev/null

echo "[synth-dump] starting a throwaway postgres ..."
docker network create "$NET" >/dev/null
# trust auth: reachable only on this private network, and gone when the script exits.
docker run -d --name "$PG" --network "$NET" \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=econ_synth \
  postgres:16 >/dev/null
for _ in $(seq 1 60); do
  docker exec "$PG" pg_isready -h 127.0.0.1 -U postgres -d econ_synth >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$PG" pg_isready -h 127.0.0.1 -U postgres -d econ_synth >/dev/null 2>&1 \
  || die "the throwaway postgres did not come up"

# Built as arrays that are never empty (macOS bash 3.2 fails on an empty array under set -u).
MOUNTS=(-v "${DATASET_ABS}:/dataset:ro")
LOAD_ARGS=(--dataset /dataset --dsn "postgresql+psycopg://postgres@${PG}:5432/econ_synth")
if [[ -f "$FORECAST_EXPORT" ]]; then
  echo "[synth-dump] with the forecast export $FORECAST_EXPORT"
  MOUNTS+=(-v "${FORECAST_EXPORT}:/forecast_export.json:ro")
  LOAD_ARGS+=(--forecast-export /forecast_export.json)
fi

echo "[synth-dump] loading $DATASET (about a minute) ..."
LOG="$(mktemp)"
if ! docker run --rm --network "$NET" "${MOUNTS[@]}" "$IMAGE" \
    python -m server.storage.cli_load_synthetic "${LOAD_ARGS[@]}" >"$LOG" 2>&1; then
  tail -20 "$LOG" >&2; rm -f "$LOG"
  die "the synthetic load failed (output above); ${OUT#"$ROOT/"} left as it was"
fi
# The loader's closing line is its JSON summary; surface the part worth reading.
tail -1 "$LOG" | python3 -c "
import json, sys
try:
    s = json.loads(sys.stdin.read())
except ValueError:
    sys.exit(0)
print(f\"[synth-dump] loaded: rollup_ok={s.get('rollup_ok')}  admin viewer={s.get('admin_viewer_id')}\")" || true
rm -f "$LOG"

SRC_CONTAINER="$PG" SRC_DB=econ_synth OUT="$OUT" "$ROOT/utils/stopgap_seed.sh"
