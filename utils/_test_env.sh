#!/usr/bin/env bash
# Shared bootstrap for the test-runner scripts (docs/manual-testing.md §0).
# SOURCE this file, don't execute it. It removes the per-shell hoops -- venv, deps,
# TALIUM_DATA_DIR, PYTHONPATH -- so every utils/test_*.sh runs from a clean shell.
#
# Overridable before calling: TALIUM_DATA_DIR (staging/scratch root).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ROOT

# 1. venv -- create on first use (CLAUDE.md: python -m venv venv), then activate.
if [[ ! -x "$ROOT/venv/bin/python" ]]; then
  echo "[test-env] creating venv at $ROOT/venv ..."
  python3 -m venv "$ROOT/venv"
fi
# shellcheck disable=SC1091
source "$ROOT/venv/bin/activate"

# 2. deps -- install only when something is actually missing (keeps re-runs instant). The
#    sentinel spans every requirements.txt group so an under-provisioned venv is topped up.
if ! python -c 'import pytest, pydantic, yaml, structlog, httpx, sqlalchemy, fastapi, boto3, faker' >/dev/null 2>&1; then
  echo "[test-env] installing requirements ..."
  pip install -q -r "$ROOT/requirements.txt"
fi

# 3. scratch/staging root the uploader + collector configs read via ${TALIUM_DATA_DIR}.
export TALIUM_DATA_DIR="${TALIUM_DATA_DIR:-/tmp/talium-demo/data}"
mkdir -p "$TALIUM_DATA_DIR"

# 4. import path -- pytest.ini already sets `pythonpath = . src tests`, but the manual
#    `python -m ...` and fixture-builder snippets need it on the process env too.
export PYTHONPATH="$ROOT/econ-forecast/src:$ROOT/src:$ROOT/tests${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT"
