"""Step 7 — integration over the real normalized corpus.

Builds every family in the local ``nrm_sess`` corpus end to end and pins the graph against the
grounded oracles measured directly off the JSONL (findings ledger / plan). The corpus lives
outside the repo, so the whole module is skipped when it is absent — it validates locally
without breaking CI on a machine that doesn't have it. Point it elsewhere with
``CAUSAL_GRAPH_CORPUS``.

Oracles (measured, e97d97f5 family):
    34 sessions · 817 assistant · 868 tool · 33 spawn nodes · 0 dangling across all 9 families
    per-model input (input+cache_read+cache_write): opus 69,702,345 · haiku 10,625,422 ·
        sonnet 8,249,578 · fable 4,347,798
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from analysis.causal_graph import build_family

CORPUS = Path(os.environ.get(
    "CAUSAL_GRAPH_CORPUS",
    "/Users/sumandra/projects/talium/traces/nrm_sess/-Users-sumandra-projects-talium-code-econ",
))

pytestmark = pytest.mark.skipif(
    not CORPUS.is_dir(),
    reason=f"nrm_sess corpus not present at {CORPUS} (it lives outside the repo)",
)


def _family(prefix: str):
    path = next(CORPUS.glob(f"{prefix}*.json"))
    return build_family(json.loads(path.read_text(encoding="utf-8")))


def _counts(family) -> dict[str, int]:
    counts: dict[str, int] = {}
    for graph in family.all_graphs():
        for node in graph.nodes.values():
            counts[node.type.value] = counts.get(node.type.value, 0) + 1
    return counts


def _dangling(family) -> int:
    total = 0
    for graph in family.all_graphs():
        ids = set(graph.nodes)
        for node in graph.nodes.values():
            total += sum(1 for c in node.children if c not in ids)
    return total


def test_all_families_build_with_zero_dangling():
    files = sorted(CORPUS.glob("*.json"))
    assert len(files) == 9  # the corpus has 9 top-level sessions
    for path in files:
        family = build_family(json.loads(path.read_text(encoding="utf-8")))
        assert _dangling(family) == 0, f"{path.name}: dangling child ids"


def test_big_family_node_counts():
    family = _family("e97d97f5")
    assert len(family.all_graphs()) == 34  # master + 33 subagents
    counts = _counts(family)
    assert counts["assistant"] == 817
    assert counts["tool"] == 868
    assert counts["spawn"] == 33
