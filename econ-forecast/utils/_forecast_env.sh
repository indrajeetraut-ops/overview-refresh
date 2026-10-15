#!/usr/bin/env bash
# Shared runtime paths and venv bootstrap for the econ-forecast utils.
# Sourcing this helper does not change the caller's cwd. It ensures a usable venv exists
# (creating it and installing econ-forecast/requirements.txt when the venv is missing, or
# reinstalling when that file changed since the last install), then exports FORECAST_PYTHON
# and PYTHONPATH. All bootstrap output goes to stderr so wrappers keep stdout for their
# JSON summaries.
set -euo pipefail

FORECAST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FORECAST_VENV="$FORECAST_ROOT/venv"
FORECAST_REQUIREMENTS="$FORECAST_ROOT/econ-forecast/requirements.txt"

_forecast_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

_forecast_require_py311() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required")'
}

# Create the venv when its interpreter is missing.
if [[ ! -x "$FORECAST_VENV/bin/python" ]]; then
  echo "forecast: creating venv at $FORECAST_VENV" >&2
  _forecast_require_py311 python3
  python3 -m venv "$FORECAST_VENV" >&2
fi

FORECAST_PYTHON="$FORECAST_VENV/bin/python"
_forecast_require_py311 "$FORECAST_PYTHON"

# Install (or refresh) dependencies when the venv has never been provisioned from the
# requirements file, or that file changed since the last install. A hash marker keeps
# subsequent runs fast (no reinstall when requirements are unchanged).
if [[ -f "$FORECAST_REQUIREMENTS" ]]; then
  _forecast_req_hash="$(_forecast_sha256 "$FORECAST_REQUIREMENTS")"
  _forecast_marker="$FORECAST_VENV/.requirements.sha256"
  _forecast_have=""
  if [[ -f "$_forecast_marker" ]]; then
    _forecast_have="$(cat "$_forecast_marker")"
  fi
  if [[ "$_forecast_req_hash" != "$_forecast_have" ]]; then
    echo "forecast: installing dependencies from econ-forecast/requirements.txt" >&2
    "$FORECAST_PYTHON" -m pip install --quiet --upgrade pip >&2
    "$FORECAST_PYTHON" -m pip install -r "$FORECAST_REQUIREMENTS" >&2
    printf '%s\n' "$_forecast_req_hash" > "$_forecast_marker"
  fi
fi

export PYTHONPATH="$FORECAST_ROOT/econ-forecast/src:$FORECAST_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export FORECAST_ROOT FORECAST_PYTHON FORECAST_VENV
