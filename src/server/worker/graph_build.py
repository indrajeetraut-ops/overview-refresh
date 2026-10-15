"""Build, persist, and materialize causal graphs (combined sub-plan §2b).

Per-session on arrival: `build_and_persist` builds one session's graph and upserts its
`session_graph` row (write-safe source of truth, no coordination). Per-family at rollup:
`materialize_tree` assembles the family (master + descendants) from those rows and upserts the
single flat `session_tree` row (the one-fetch read cache). `db_resolver` is the storage-specific
resolver `GraphContext.load_tree` calls, one batched read per BFS depth.

Upserts use the ORM (same discipline as `persistence.persist_session_info`); the batched read uses
the `graph` named query. No graph logic here -- it wires the engine (`build_graph`, `GraphContext`)
to storage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session as DbSession

from analysis.causal_graph import GraphContext, build_graph
from analysis.causal_graph.models import SCHEMA_VERSION
from server.storage.queries import QueryCatalog
from server.storage.schema.graph import SessionGraph, SessionTree


def build_and_persist(s: DbSession, session: dict) -> dict:
    """Build one session's graph from its normalized `{vendor, session_id, parent_session_id,
    source, events}` dict and upsert its `session_graph` row. Idempotent: a re-delivery overwrites
    the row with the freshly rebuilt graph (build_graph is pure)."""
    graph = build_graph(session)
    session_id = session["session_id"]
    now = datetime.now(timezone.utc)
    row = s.get(SessionGraph, session_id)
    if row is None:
        row = SessionGraph(session_id=session_id, built_at=now)
        s.add(row)
    row.parent_session_id = session.get("parent_session_id")
    row.graph_json = graph
    row.schema_version = graph.get("schema_version") or SCHEMA_VERSION
    row.built_at = now
    return graph


def persist_graph(s: DbSession, session_id: str, parent_session_id: str | None, graph: dict) -> None:
    """Upsert an already-built graph dict into its session_graph row (GraphFold.fold built it, so we
    don't rebuild). Idempotent -- overwrites on re-delivery."""
    now = datetime.now(timezone.utc)
    row = s.get(SessionGraph, session_id)
    if row is None:
        row = SessionGraph(session_id=session_id, built_at=now)
        s.add(row)
    row.parent_session_id = parent_session_id
    row.graph_json = graph
    row.schema_version = graph.get("schema_version") or SCHEMA_VERSION
    row.built_at = now


def db_resolver(s: DbSession, catalog: QueryCatalog) -> Callable[[list[str]], dict[str, dict]]:
    """The resolver `GraphContext.load_tree` calls: {session_id: graph_json} for a batch of ids."""
    def resolve(session_ids: list[str]) -> dict[str, dict]:
        if not session_ids:
            return {}
        rows = s.execute(
            catalog.get("graph", "session_graphs_by_id"), {"session_ids": list(session_ids)}
        ).mappings().all()
        return {r["session_id"]: r["graph_json"] for r in rows}
    return resolve


def load_family_from_rows(s: DbSession, catalog: QueryCatalog, master_session_id: str):
    """Assemble the in-memory Family for a master from its `session_graph` rows (source of truth).
    Returns (GraphContext, Family) or (None, None) if the master row is absent. Caller unloads."""
    master_row = s.get(SessionGraph, master_session_id)
    if master_row is None:
        return None, None
    context = GraphContext()
    context.load_self(master_row.graph_json)
    context.load_tree(db_resolver(s, catalog))
    return context, context.family


def materialize_tree(s: DbSession, catalog: QueryCatalog, master_session_id: str) -> dict | None:
    """Assemble the family from `session_graph` rows and upsert the flat `session_tree` blob.
    Idempotent and re-triggerable: a late child that has since landed is picked up on the next call."""
    context, family = load_family_from_rows(s, catalog, master_session_id)
    if family is None:
        return None
    try:
        graphs = {g.session_id: g.to_dict() for g in family.all_graphs()}
    finally:
        context.unload_tree()
    tree_json: dict[str, Any] = {"master_session_id": master_session_id, "graphs": graphs}
    now = datetime.now(timezone.utc)
    row = s.get(SessionTree, master_session_id)
    if row is None:
        row = SessionTree(master_session_id=master_session_id, built_at=now)
        s.add(row)
    row.tree_json = tree_json
    row.member_count = len(graphs)
    row.built_at = now
    return tree_json
