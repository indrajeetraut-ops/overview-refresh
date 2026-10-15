"""session_graph + session_tree — causal-graph persistence (combined sub-plan §2b; storage design
FROZEN in docs/plan/2026-08-30-combined-normalizer-graph-fold.md).

Two tables, deliberately split by write/read role:

* `session_graph` — the WRITE-safe source of truth: one row per session (master OR subagent),
  holding that session's own `build_graph` output. Each subagent writes its own row on arrival,
  independently, order-free — no read-modify-write, no coordination.
* `session_tree` — the derived single-fetch READ cache: one row per family (keyed by the root
  master), holding a FLAT `{session_id: graph_dict}` map of the master + all descendants (any depth;
  hierarchy carried by refs, not nesting). Regenerable from `session_graph`, never a second truth.
"""
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, ts


class SessionGraph(Base):
    __tablename__ = "session_graph"
    session_id: Mapped[str] = mapped_column(primary_key=True)
    # null on masters; the immediate parent for a subagent. Indexed for family lookups.
    parent_session_id: Mapped[str | None] = mapped_column(index=True)
    graph_json: Mapped[jsb]                 # build_graph output for THIS session only
    schema_version: Mapped[str]             # graph contract version, for migration
    built_at: Mapped[ts]


class SessionTree(Base):
    __tablename__ = "session_tree"
    master_session_id: Mapped[str] = mapped_column(primary_key=True)
    # {master_session_id, graphs: {session_id: Graph.to_dict()}} -- flat, master + all descendants.
    tree_json: Mapped[jsb]
    member_count: Mapped[int] = mapped_column(default=0)  # lets a reader detect "not all children present yet"
    built_at: Mapped[ts]
