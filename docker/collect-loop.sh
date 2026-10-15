#!/usr/bin/env bash
# The collector is a batch pass (`cli_collect` sweeps, ships, checkpoints, exits), not a
# daemon. In a real deployment a scheduler runs it; here a loop stands in for one.
#
# The pass is cheap when nothing is new -- the checkpoint store makes an unchanged source a
# no-op -- so a short interval costs little and keeps the JSONL side of the join arriving on
# roughly the timescale the OTel side does.
set -euo pipefail

INTERVAL="${COLLECT_INTERVAL_S:-30}"
CONFIG="${COLLECTOR_CONFIG:-/app/docker-config/collector.yaml}"
# The sweep root. "<base>uploads/" when the uploader writes under a bucket base prefix
# (TALIUM_S3_PREFIX, e.g. "cc/logs/uploads/"); manifests outside it are never found.
SCOPE="${COLLECT_SCOPE:-uploads/}"

echo "[collector] sweeping ${SCOPE} every ${INTERVAL}s using ${CONFIG}"
while true; do
  # Never let one bad pass end the container: a half-staged upload should be retried on the
  # next sweep, not require an operator to restart the service.
  python -m collector.cli_collect --config "$CONFIG" --scope "$SCOPE" || echo "[collector] pass failed; retrying"
  sleep "$INTERVAL"
done
