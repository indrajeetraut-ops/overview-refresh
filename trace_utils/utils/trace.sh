#!/usr/bin/env bash
# Shell wrapper for the trace_utils CLI (project CLI-naming standard).
#
# Auto-bootstraps a dedicated virtualenv on first run so the tool "just works":
#   utils/trace.sh <command> [args...]   # create venv if needed, then run
#   utils/trace.sh --reset [args...]     # force-recreate the venv, then run
#
# The venv is named trace_util.venv (distinctive, so it's easy to spot and clean up).
# Override the base interpreter with PYTHON=/path/to/python3.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${HERE}/trace_util.venv"
PYTHON="${PYTHON:-python3}"

# Consume a --reset flag from anywhere in the args; keep the rest for the CLI.
reset=0
args=()
for a in "$@"; do
  if [ "$a" = "--reset" ]; then
    reset=1
  else
    args+=("$a")
  fi
done

if [ "$reset" -eq 1 ] && [ -d "$VENV" ]; then
  echo "trace_utils: removing venv $VENV" >&2
  rm -rf "$VENV"
fi

# Bootstrap the venv if it isn't there (first run, or after --reset).
if [ ! -x "$VENV/bin/python" ]; then
  echo "trace_utils: creating venv at $VENV ..." >&2
  "$PYTHON" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip >&2
  "$VENV/bin/python" -m pip install --quiet -e "$HERE" >&2
  echo "trace_utils: venv ready" >&2
fi

# --reset with no command: just (re)build the venv and stop.
if [ "${#args[@]}" -eq 0 ]; then
  [ "$reset" -eq 1 ] && exit 0
  exec "$VENV/bin/python" -m trace_utils.cli_trace --help
fi

exec "$VENV/bin/python" -m trace_utils.cli_trace "${args[@]}"
