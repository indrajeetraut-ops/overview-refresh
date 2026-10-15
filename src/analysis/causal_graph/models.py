"""Node/Graph/Family carrier types for the causal graph — the frozen contract.

The Graph envelope is the persisted, published shape (design causal-graph-design.md §4.3;
published in src/common/contracts/causal_graph/schemas/graph.schema.json) and is read by
other modules, so Node is a pydantic model that validates on load. Graph and Family are
in-process containers assembled at build/load time. Graph owns the explicit
to_dict()/from_dict() that convert between the in-memory node index (keyed by id, for O(1)
lookup and traversal) and the list-shaped persisted envelope (§4.1, §4.3).

Design decision (plan Step 0): one Node with an open `attrs` dict rather than a dataclass
per type — it matches the envelope 1:1 and keeps persisted graph data vendor-neutral.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field

from . import counters

__all__ = ["NodeType", "Node", "Graph", "Family", "SCHEMA_VERSION"]

# 4.3.0 -- additive: configured harness-memory classification adds `is_memory` and
# `memory_kind` to matching tool file records and the queryable `memory_access:true` marker to
# their Tool node. Write results also refine update operations to edit when the result says so.
# 4.2.0 -- additive: a `compaction` node type. A context compaction (a harness event, not part
# of any turn) becomes a session-level node carrying token deltas / trigger / duration and
# `usage_known:false` (the summarization call's own usage is absent from the JSONL). It is NOT in
# `roots` -- that field remains the ordered turn index -- and is discovered by type or through
# non-containment refs. A 4.2 reader accepts prior 4.x envelopes; older readers are not expected
# to accept a new enum member.
# 4.1.0 -- additive: configured shell command extraction persists an ordered `attrs.cmd`
# list on every tool node and graph metadata records the configuration revision used.
# 4.0.0 -- BREAKING. Turn identity is now the session-scoped pair
# `(session_id, turn_num)`. Inside one Graph the persisted local id is `turn:{turn_num}` and
# the same 1-based ordinal is explicit in `attrs.turn_num`; `attrs.prompt_id` remains vendor
# provenance only. Family-wide consumers must pair every local node id with `session_id`.
# Sibling agent sessions routinely reuse local ids, so treating a bare id as family-global
# silently dropped nodes and could connect a child to a parent from another session.
# 3.0.0 -- BREAKING. Turn node identity changed: a turn's `id` is now
# `"{prompt_id}:{run_ordinal}"`, not the bare `promptId` -- identity depending on a vendor field
# is what let a turn-boundary collision go silent (plan Step 1; design §4.1 "Turn id"). The
# vendor value survives, demoted to `attrs.prompt_id`, queryable but no longer identity. Every
# turn id a graph built before this version carries is invalid under the new rule; `roots`,
# `children`, `parent_id` and any external reference to a turn id from an old graph do not
# resolve against a new one. Stored graphs are rebuilt, not migrated -- rebuilding the corpus is
# cheap and there is no old->new id mapping to write when the old set already contained
# duplicates (design §4.3 "Contract change"). `Graph.add` is also first-wins as of this version
# (previously last-wins, undocumented as a defect) -- see `counters.py`.
# 2.1.0 -- additive only: new attrs on turn (`permission_mode`, `plan_mode`, `attribution`),
# assistant (`attribution_skill`, `attribution_plugin`, `attribution_agent`, `effort`), tool
# (`result`), and spawn (`result`). No field removed, none retyped, no existing field's meaning
# changed -- a 2.0.0 reader ignores the new attrs and reads the graph the same way it always did.
# 2.0.0 -- `parent_id` added to Node, and turns carry a nested `attrs.tokens` rollup. Both are
# additive, but a consumer that reconstructed the tree from `children` alone, or that assumed a
# turn had no usage of its own, reads the graph differently now -- so it is a major bump rather
# than a quiet addition.
# 1.0.0 -- first published graph contract.
SCHEMA_VERSION = "4.3.0"


class NodeType(str, Enum):
    """The node kinds. Values are the strings that ride in the envelope."""

    TURN = "turn"
    USER_PROMPT = "user_prompt"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SPAWN = "spawn"
    COMPACTION = "compaction"


class Node(BaseModel):
    """One graph node.

    `attrs` is builder-extracted and fixed-shape per type; `meta` is the open bag later
    features (labeler, budget cache, cohort) write to without reshaping the node; `refs`
    holds non-containment node/session references — e.g. a spawn's late-bound
    `child_session_id` or a compaction's local turn ids (design §4.1).
    """

    id: str
    type: NodeType
    ts: str | None = None
    attrs: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)
    children: list[str] = Field(default_factory=list)  # ids, intra-session containment
    # The containment parent, or None on a turn or session-level out-of-band node. `children`
    # alone points
    # only downward, so a query result could not say which turn a row belonged to and every
    # per-turn grouping needed a second call and a join. With both, (id, parent_id) is an
    # adjacency list and a consumer can rebuild the tree from any query's output.
    parent_id: str | None = None
    refs: dict = Field(default_factory=dict)


def _node_dict_without_budget_cache(node: "Node") -> dict:
    """``node.model_dump()``, excluding the retired legacy ``meta["budget"]`` cache key."""
    d = node.model_dump(mode="json")
    if "budget" in d.get("meta", {}):
        d["meta"] = {k: v for k, v in d["meta"].items() if k != "budget"}
    return d


@dataclass
class Graph:
    """One session's span tree, in memory.

    `nodes` is indexed by id; the persisted envelope (§4.3) serializes it as an ordered
    list. to_dict()/from_dict() are the round-trip between the two shapes and are the only
    place the envelope layout is written down in code.
    """

    session_id: str
    vendor: str  # "claude_code" | "codex"
    source: str  # "jsonl" | "otel"
    built: str  # "complete" | "partial"
    parent_session_id: str | None = None  # set for subagent graphs
    meta: dict = field(default_factory=dict)  # session-scoped: title, objective, budget (§4.3)
    nodes: dict[str, Node] = field(default_factory=dict)  # by id
    roots: list[str] = field(default_factory=list)  # turn ids, in order
    schema_version: str = SCHEMA_VERSION

    def add(self, node: Node) -> None:
        """Index a node by its id -- first-wins.

        An id already present keeps its existing node; the collision is counted and logged
        (`counters.node_collision`) rather than silently overwritten. Previously "last write
        wins" (undocumented as a defect): that let a duplicated turn id silently replace an
        earlier turn's subtree, orphaning everything beneath it -- 1,974,633 tokens on one
        measured session (plan Step 1/3, design §6 "First-wins inserts over last-wins").
        """
        if node.id in self.nodes:
            counters.node_collision(
                session_id=self.session_id, vendor=self.vendor,
                node_id=node.id, node_type=node.type.value,
            )
            return
        self.nodes[node.id] = node

    def to_dict(self) -> dict:
        """The persisted envelope (§4.3): nodes flattened to an ordered list.

        The retired legacy ``meta["budget"]`` key is still stripped defensively. The current
        budget cache belongs to a private in-memory Family query context, so it never reaches a
        Graph or Node envelope. The live graph objects are untouched; only this serialized dict
        excludes the obsolete key.
        """
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "parent_session_id": self.parent_session_id,
            "vendor": self.vendor,
            "source": self.source,
            "built": self.built,
            "meta": {k: v for k, v in self.meta.items() if k != "budget"},
            "nodes": [_node_dict_without_budget_cache(n) for n in self.nodes.values()],
            "roots": self.roots,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Graph":
        """Rebuild from an envelope. Unknown top-level keys are ignored (must-ignore)."""
        nodes = [Node.model_validate(n) for n in d.get("nodes", [])]
        return cls(
            session_id=d["session_id"],
            vendor=d["vendor"],
            source=d["source"],
            built=d["built"],
            parent_session_id=d.get("parent_session_id"),
            meta=dict(d.get("meta", {})),
            nodes={n.id: n for n in nodes},
            roots=list(d.get("roots", [])),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )


@dataclass
class Family:
    """A master graph plus its descendant subagent graphs, keyed by session_id.

    resolve() follows a spawn's `child_session_id` ref; it returns None when the child is
    not loaded (late-bound / partial family), which the caller reports as `partial` rather
    than blocking (design §3.2).
    """

    master: Graph
    children: dict[str, Graph] = field(default_factory=dict)  # by session_id

    def resolve(self, session_id: str) -> Graph | None:
        """Return the graph for a session id within this family, or None if absent."""
        if session_id == self.master.session_id:
            return self.master
        return self.children.get(session_id)

    def all_graphs(self) -> list[Graph]:
        """Every graph in the family, master first."""
        return [self.master, *self.children.values()]
