"""Assemble a normalizer envelope into a :class:`Family` and scope queries over it.

The input is the normalizer's per-session output envelope ``{meta_info, sessions: [...]}``
where each ``sessions[]`` member is one session ``{vendor, session_id, source,
parent_session_id, events[]}`` (see ``src/common/events/normalized_schema.json`` for the
per-event payload contract). Each session is turned into a :class:`Graph` by Step 1's
:func:`build_graph`; the family groups them by ``parent_session_id`` (design
causal-graph-design.md §3.2):

* **master** — the session whose ``parent_session_id`` is null.
* **children** — every other session (descendant subagent graphs), keyed by ``session_id``.

Cross-session refs stay late-bound: a spawn node carries ``child_session_id`` in ``refs``
and :meth:`Family.resolve` walks it at query time. A spawn whose child is not among the
loaded sessions (partial family, OTEL-live) is fine — ``resolve`` returns None and the
caller marks the result ``partial`` rather than blocking (design §3.2).

Scope filtering (``self`` | ``tree`` (DEFAULT) | subagent session-id) is exposed here as the
canonical helper. NOTE: ops.py has an equivalent internal ``_graphs_in_scope``; reconciling
the two is a later cleanup — do not edit ops.py for it.
"""
from __future__ import annotations

import structlog

from .builder import _build_graph_model
from .config import ReasoningConfig
from .models import Family, Graph, Node
from .query_config import QueryConfig, load_query_config

__all__ = ["build_family", "graphs_in_scope", "nodes_in_scope"]

_log = structlog.get_logger(module="analysis.causal_graph.family")


def build_family(
    envelope: dict, config: ReasoningConfig | None = None, *, query_config: QueryConfig | None = None,
) -> Family:
    """Assemble the normalizer envelope into a :class:`Family` (design §3.2, §7).

    The master is the session with ``parent_session_id`` null; the rest are descendant
    subagent graphs, keyed by ``session_id``. If more than one session has a null
    ``parent_session_id``, the first (in envelope order) is taken as master and the others
    are treated as children — an assumption reported via a warning log.

    ``config`` (reasoning-estimate constants) is forwarded to every :func:`build_graph` call;
    ``None`` uses the built-in default for all of them.
    """
    sessions = envelope.get("sessions") or []
    if not sessions:
        raise ValueError("envelope has no sessions to assemble into a family")

    masters = [s for s in sessions if s.get("parent_session_id") is None]
    if not masters:
        raise ValueError("envelope has no master session (parent_session_id is null)")
    if len(masters) > 1:
        _log.warning(
            "multiple_master_sessions",
            count=len(masters),
            chosen=masters[0].get("session_id"),
            others=[s.get("session_id") for s in masters[1:]],
        )

    master_session = masters[0]
    if query_config is None:
        query_config = load_query_config()
    master = _build_graph_model(master_session, config, query_config=query_config)

    children: dict[str, Graph] = {}
    for session in sessions:
        if session is master_session:
            continue
        graph = _build_graph_model(session, config, query_config=query_config)
        children[graph.session_id] = graph

    _log.debug(
        "family_built",
        master=master.session_id,
        children=list(children.keys()),
    )
    return Family(master=master, children=children)


SCOPE_KEYWORDS = ("self", "tree", "subagents")
_MAX_IDS_IN_ERROR = 4


def _spawned_session_ids(family: Family) -> set[str]:
    """Every child session id the family's spawn nodes reference, loaded or not.

    The discriminator between "a subagent that exists but is not loaded" and "a typo": the
    first is a real state under the partial-family contract, the second is a mistake, and
    returning an empty result for both is what made a bad scope look like a real answer.
    """
    return {
        ref
        for graph in family.all_graphs()
        for node in graph.nodes.values()
        if (ref := node.refs.get("child_session_id"))
    }


def graphs_in_scope(family: Family, scope: str = "tree") -> list[Graph]:
    """The graphs a query touches under ``scope`` (design §3.2).

    ```
    self          master only
    tree          master + all descendants          (DEFAULT)
    subagents     descendants only, master excluded
    <session-id>  one named child
    ```

    ``subagents`` is plural on purpose: ``budget``'s ``by="subagent"`` groups results *per*
    child and is a different thing, so the two must not share a spelling.

    Raises ``ValueError`` for a scope that is neither a keyword nor a session the family knows
    about. It used to return ``[]``, which reported a typo as a legitimate empty answer --
    ``--scope subagent`` (singular) came back as zero tokens rather than a mistake.
    """
    if scope == "self":
        return [family.master]
    if scope == "tree":
        return family.all_graphs()
    if scope == "subagents":
        return list(family.children.values())

    graph = family.resolve(scope)
    if graph is not None:
        return [graph]
    if scope in _spawned_session_ids(family):
        # Spawned but not loaded: a partial family (OTEL-live, or a child not yet built).
        # Empty is the honest answer here and the caller reports `partial` (design §3.2).
        return []

    known = sorted(family.children)
    # Truncated: a real family runs to dozens of subagents and dumping every id buries the
    # part that actually helps -- the keywords.
    if not known:
        hint = "this family has no subagents"
    elif len(known) <= _MAX_IDS_IN_ERROR:
        hint = f"a subagent session id ({', '.join(known)})"
    else:
        shown = ", ".join(known[:_MAX_IDS_IN_ERROR])
        hint = f"a subagent session id ({shown}, and {len(known) - _MAX_IDS_IN_ERROR} more)"
    raise ValueError(
        f"unknown scope {scope!r}. Expected one of {', '.join(SCOPE_KEYWORDS)}, or {hint}."
    )


def nodes_in_scope(family: Family, scope: str = "tree") -> list[Node]:
    """Every node across the graphs in ``scope`` (design §3.2), master-first, node order."""
    return [
        node
        for graph in graphs_in_scope(family, scope)
        for node in graph.nodes.values()
    ]
