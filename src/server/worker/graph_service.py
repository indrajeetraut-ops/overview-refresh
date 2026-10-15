"""GraphService — the single reusable read accessor for a session's causal-graph family
(combined sub-plan §2b; "the tree is a reusable read surface", storage design FROZEN).

`load_family(session_id)` returns the in-memory `Family` (master + all descendants), assembled either
from the one-fetch `session_tree` blob (fast path) or, when it is absent/stale, from the per-session
`session_graph` rows (source of truth). Decoupled from the fold/rollup: any consumer -- the dashboard,
future analytics, `GraphFold.rollup_from_family` -- goes through this; loading a tree never needs a rollup.
"""
from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from analysis.causal_graph import GraphContext
from analysis.causal_graph.models import Family
from server.storage.queries import QueryCatalog
from server.worker.graph_build import load_family_from_rows


class GraphService:
    def __init__(self, catalog: QueryCatalog) -> None:
        self._catalog = catalog

    def root_master_of(self, s: DbSession, session_id: str) -> str | None:
        """The root master for any session id (a subagent may be passed in); None if unknown."""
        row = s.execute(
            self._catalog.get("graph", "root_master_of"), {"session_id": session_id}
        ).mappings().first()
        return row["session_id"] if row else None

    def load_family(self, s: DbSession, session_id: str) -> Family | None:
        """The family for the tree `session_id` belongs to. One fetch when the `session_tree` blob is
        present; otherwise assembled from `session_graph` rows. Returns None if the session is unknown."""
        master = self.root_master_of(s, session_id)
        if master is None:
            return None

        tree = s.execute(
            self._catalog.get("graph", "session_tree_by_master"), {"master_session_id": master}
        ).mappings().first()
        if tree is not None:
            graphs = (tree["tree_json"] or {}).get("graphs") or {}
            if master in graphs:
                context = GraphContext()
                context.load_self(graphs[master])
                context.load_tree(lambda ids: {i: graphs[i] for i in ids if i in graphs})
                family = context.family
                context.unload_tree()
                return family

        # Fallback: assemble from the source-of-truth rows (the family object survives unload_tree).
        context, family = load_family_from_rows(s, self._catalog, master)
        if family is None:
            return None
        context.unload_tree()
        return family
