#!/usr/bin/env bash
# Stage a persistent workspace for the synthetic/forecast guide workflow.
# Example: utils/stage-synth-data.sh --work /path/to/workspace
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: stage-synth-data.sh --work <folder> [--no-venv]

Creates <folder>/config/{synthetic,forecasting} and <folder>/artifacts,
copies the repository's synthetic/forecasting config templates into it.
Prints only the resolved workspace path to stdout; capture it with:

  WORK=$(stage-synth-data.sh --work <folder>)

  --work <folder>   Workspace root to create (created if missing; existing
                     files under it are left alone).
  --no-venv         Skip creating/installing the repository venv. synt_gen.sh,
                     forecast.sh, and plot_forecast.sh already fall back to
                     venv/bin/python when present and plain python3 otherwise,
                     so this is only needed here to bootstrap a missing venv.
EOF
}

WORK=""
CREATE_VENV=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --work)
      [[ $# -ge 2 ]] || { echo "--work requires a value" >&2; exit 2; }
      WORK="$2"
      shift 2
      ;;
    --no-venv)
      CREATE_VENV=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$WORK" ]]; then
  echo "Error: --work <folder> is required" >&2
  usage >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ "$CREATE_VENV" -eq 1 && ! -x "$ROOT/venv/bin/python" ]]; then
  echo "Creating venv at $ROOT/venv" >&2
  python3 -m venv "$ROOT/venv"
  "$ROOT/venv/bin/python" -m pip install -r "$ROOT/econ-forecast/requirements.txt" >&2
fi

mkdir -p "$WORK/config/synthetic" "$WORK/config/forecasting" "$WORK/artifacts"
WORK="$(cd "$WORK" && pwd)"
cp "$ROOT/econ-forecast/config/synthetic/"*.yaml "$WORK/config/synthetic/"
cp "$ROOT/econ-forecast/config/forecasting/forecast.yaml" "$WORK/config/forecasting/"

# Only the resolved path goes to stdout, so callers can do: WORK=$(stage-synth-data.sh --work <folder>)
printf '%s\n' "$WORK"
