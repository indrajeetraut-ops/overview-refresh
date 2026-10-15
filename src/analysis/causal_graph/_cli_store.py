"""Private file-layout and JSON helpers used only by the graph CLI.

This module is *only* file IO. The envelope shape (design causal-graph-design.md
§4.3) is owned entirely by Graph.to_dict()/Graph.from_dict(); dump/load here never
inspect or reshape it. The DB/Postgres store (design §8.1) is the worker's concern
and is deliberately out of scope for v0.
"""
from __future__ import annotations

import json
from pathlib import Path

from analysis.causal_graph.models import Graph

__all__ = ["SUBAGENT_DIR", "dump_graph", "family_paths", "load_graph_dict", "subagent_dir"]

# A family on disk mirrors the layout Claude writes and the normalizer reproduces:
#
#     <root>/<master-session-id>.json
#     <root>/<master-session-id>/subagents/<child-session-id>.json
#
# The master beside a directory named for it. This is what lets many families share one
# output directory: without it, a flat dump makes every sibling file look like a child of
# whichever master is read first.
SUBAGENT_DIR = "subagents"


def subagent_dir(root: str | Path, master_session_id: str) -> Path:
    """Where a master's child envelopes live. The single definition of the layout."""
    return Path(root) / master_session_id / SUBAGENT_DIR


def family_paths(root: str | Path, master_session_id: str) -> tuple[Path, Path]:
    """``(master_path, subagents_dir)`` for a family rooted at ``root``.

    Both writer and reader call this, so a change to the layout cannot leave one of them
    behind -- the failure mode it exists to prevent is a dump and a load that disagree, which
    surfaces as a family that silently loses its subagents rather than as an error.

    Descendants are flattened into one ``subagents/`` directory rather than nested per parent.
    ``Family`` is flat (``children`` keyed by session_id) and each envelope carries its own
    ``parent_session_id``, so the real tree is preserved either way; nesting would add path
    depth without adding information, and grandchildren are rare.
    """
    root = Path(root)
    return root / f"{master_session_id}.json", subagent_dir(root, master_session_id)


def dump_graph(graph: Graph, path: str | Path) -> None:
    """Write the graph envelope to ``path`` as indented UTF-8 JSON.

    The envelope layout is delegated to ``Graph.to_dict()``; this only serializes
    and writes it. ``ensure_ascii=False`` keeps non-ASCII text readable rather than
    escaped, and the trailing newline keeps the file POSIX-friendly.
    """
    envelope = graph.to_dict()
    text = json.dumps(envelope, indent=2, ensure_ascii=False)
    Path(path).write_text(text + "\n", encoding="utf-8")


def load_graph_dict(path: str | Path) -> dict:
    """Read a graph dictionary from ``path`` without materializing the model.

    A missing path propagates ``FileNotFoundError`` rather than returning an empty
    graph — an absent file is a caller error, not a valid empty session. Unknown
    top-level keys are tolerated by ``Graph.from_dict`` (must-ignore, design §4.3).
    """
    text = Path(path).read_text(encoding="utf-8")
    envelope = json.loads(text)
    if not isinstance(envelope, dict):
        raise ValueError(f"graph JSON must contain an object: {path}")
    return envelope
