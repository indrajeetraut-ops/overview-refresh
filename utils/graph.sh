#!/usr/bin/env bash
# Wrapper for the causal-graph management CLI (design §8): build | load | query.
set -euo pipefail
PYTHONPATH=src exec venv/bin/python -m analysis.causal_graph.cli_graph "$@"
