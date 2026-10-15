"""Tests for the CLI-private causal-graph file helpers.

These exercise round-trip fidelity and the must-ignore / missing-file contracts
against a hand-built Graph, independent of the builder.
"""
from __future__ import annotations

import json

import pytest

from analysis.causal_graph.models import Graph, Node, NodeType, SCHEMA_VERSION
from analysis.causal_graph._cli_store import dump_graph, load_graph_dict


def _sample_graph() -> Graph:
    """A small, hand-built graph: two turn roots with a couple of child nodes."""
    turn = Node(
        id="turn-1",
        type=NodeType.TURN,
        ts="2026-08-10T00:00:00Z",
        attrs={"status": "complete"},
        children=["asst-1"],
    )
    asst = Node(
        id="asst-1",
        type=NodeType.ASSISTANT,
        attrs={"model": "opus"},
        meta={"note": "unicode ok: café"},
    )
    turn2 = Node(id="turn-2", type=NodeType.TURN)
    g = Graph(
        session_id="sess-abc",
        vendor="claude_code",
        source="jsonl",
        built="complete",
        parent_session_id=None,
        meta={"title": "sample", "objective": None},
        roots=["turn-1", "turn-2"],
    )
    g.add(turn)
    g.add(asst)
    g.add(turn2)
    return g


def test_dump_load_dump_is_byte_identical(tmp_path):
    """dump -> load -> dump produces a byte-identical file (stable round-trip)."""
    p1 = tmp_path / "graph1.json"
    p2 = tmp_path / "graph2.json"

    dump_graph(_sample_graph(), p1)
    reloaded = Graph.from_dict(load_graph_dict(p1))
    dump_graph(reloaded, p2)

    assert p1.read_bytes() == p2.read_bytes()


def test_written_file_shape(tmp_path):
    """The on-disk envelope carries schema_version and nodes as a JSON list."""
    p = tmp_path / "graph.json"
    dump_graph(_sample_graph(), p)

    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["schema_version"] == SCHEMA_VERSION
    assert isinstance(raw["nodes"], list)
    assert len(raw["nodes"]) == 3
    assert {n["id"] for n in raw["nodes"]} == {"turn-1", "asst-1", "turn-2"}


def test_unknown_top_level_key_survives_load(tmp_path):
    """An extra unknown top-level key must be ignored, not rejected (must-ignore)."""
    p = tmp_path / "graph.json"
    dump_graph(_sample_graph(), p)

    envelope = json.loads(p.read_text(encoding="utf-8"))
    envelope["future_field"] = {"anything": [1, 2, 3]}
    p.write_text(json.dumps(envelope), encoding="utf-8")

    g = Graph.from_dict(load_graph_dict(p))
    assert g.session_id == "sess-abc"
    assert set(g.nodes) == {"turn-1", "asst-1", "turn-2"}


def test_load_missing_path_raises(tmp_path):
    """A missing path raises FileNotFoundError rather than a silent empty graph."""
    with pytest.raises(FileNotFoundError):
        load_graph_dict(tmp_path / "does-not-exist.json")
