#!/usr/bin/env bash
# Wrapper for the model_pricing CLIs (design §8): update, match, show, versions.
set -euo pipefail

usage() {
    echo "usage: $(basename "$0") <update|match|show|versions> [options]" >&2
}

if [ "$#" -lt 1 ]; then
    usage
    exit 2
fi

command="$1"
shift

case "$command" in
    update|match|show|versions)
        exec "${PYTHON:-python}" -m common.model_pricing.cli_"$command" "$@"
        ;;
    *)
        usage
        exit 2
        ;;
esac
