#!/usr/bin/env bash
# End-to-end: the real uploader + real engine chained in-process against a mock Ingest
# (docs/manual-testing.md §4a). No server process, no manual staging.
#
#   utils/test_e2e.sh            # the four end-to-end tests
#   utils/test_e2e.sh -v         # extra args pass through to pytest
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_test_env.sh"

exec python -m pytest "$ROOT/tests/collector/test_engine.py" -q "$@"
