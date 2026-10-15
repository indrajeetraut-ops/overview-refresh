#!/usr/bin/env bash
# The whole suite in one shot (docs/manual-testing.md §5): uploader, normalizer, engine,
# end-to-end, plus msging's own suite.
#
#   utils/test_all.sh            # everything
#   utils/test_all.sh -x         # extra args pass through to pytest (e.g. stop on first fail)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_test_env.sh"

exec python -m pytest -q "$@"
