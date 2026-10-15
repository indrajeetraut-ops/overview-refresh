"""The 'rollup' job handler (ingest.md §5, §7): recompute a parent session's subagent economics
from the causal-graph family, and propagate the change up the tree.

Triggered by `ring_rollup` (config/queries/ingest.yaml): the ingest fold handler rings a parent when
a subagent commits, and rings a session when it (re)folds *and* has children. Runs inside dispatch.py's
per-job transaction; the primary fold happens at arrival in the ingest handler, never here.

Graph-based: materialize the family tree from the `session_graph` rows the arrival folds persisted,
load it via GraphService, and recompute the parent's tree-scoped `session_info` (subagents map,
inclusive tokens) from it. No children `session_info` are read -- the graph is the source.
"""
from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from common import git_ident
from server.storage.queries import QueryCatalog
from server.storage.schema import Session as SessionRow

from .graph_build import materialize_tree
from .graph_fold import GraphFold
from .graph_service import GraphService
from .persistence import persist_session_info, row_to_state


def make_rollup_handler(fold: GraphFold):
    """Bind the 'rollup' handler to a GraphFold. dispatch.py calls it as
    `handler(s, catalog, payload)` (the fold rides in the closure)."""

    def rollup(s: DbSession, catalog: QueryCatalog, payload: dict) -> None:
        session_id = payload["session_id"]
        service = GraphService(catalog)
        # Lock the parent row so a concurrent arrival-fold of the same session serializes here.
        s.execute(catalog.get("ingest", "lock_session"), {"session_id": session_id})
        row = s.get(SessionRow, session_id)
        if row is None:
            # Rung by a child before this parent's own msg has folded. Its own fold will ring
            # this again (ingest handler's has-children check), so dropping the job is safe.
            return
        state = row_to_state(row)

        child_ids = s.execute(catalog.get("fold", "children_of"), {"session_id": session_id}).scalars().all()
        if child_ids:
            # (Re)materialize the family's tree from the source-of-truth rows, then load it and
            # recompute this parent's tree-scoped session_info from the graph.
            root = service.root_master_of(s, session_id)
            if root is not None:
                materialize_tree(s, catalog, root)
            family = service.load_family(s, session_id)
            if family is not None:
                delta = fold.rollup_from_family(state, family)
                persist_session_info(s, session_id, delta.session_info)

                repo_id = delta.session_info.repo_id
                if repo_id:
                    kind, label = git_ident.describe(repo_id)
                    s.execute(catalog.get("ingest", "upsert_repository"), {
                        "repo_id": repo_id, "kind": kind, "label": label,
                        "provenance": delta.session_info.repo_id_provenance,
                    })

        if state.parent_session_id:  # propagate up: this session's inclusive total may have changed
            s.execute(catalog.get("ingest", "ring_rollup"), {"session_id": state.parent_session_id})

    return rollup
