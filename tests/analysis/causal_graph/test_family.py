"""Behavioural tests for family assembly + scope -- each fails if the change is reverted.

Loads the hand-authored tiny_family fixture (a normalized envelope: one master session that
spawns one subagent, plus the subagent session) and checks: master/child assembly by
parent_session_id, resolve() on a present child and a missing id, self/tree scope membership,
and that the master's spawn node's refs.child_session_id equals the child's session_id (the
late-bound cross-session link).
"""
import json
from pathlib import Path

import pytest

from analysis.causal_graph.family import (
    build_family,
    graphs_in_scope,
    nodes_in_scope,
)
from analysis.causal_graph.models import Family, NodeType

FIXTURE = Path(__file__).parents[2] / "fixtures" / "causal_graph" / "tiny_family.json"

MASTER_ID = "sess-master-0001"
CHILD_ID = "agent-child-001"


@pytest.fixture
def envelope() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def family(envelope):
    return build_family(envelope)


# --- assembly --------------------------------------------------------------

def test_master_and_single_child(family):
    assert family.master.session_id == MASTER_ID
    assert family.master.parent_session_id is None
    assert list(family.children.keys()) == [CHILD_ID]
    assert len(family.all_graphs()) == 2


def test_child_records_parent(family):
    assert family.children[CHILD_ID].parent_session_id == MASTER_ID


# --- resolve ---------------------------------------------------------------

def test_resolve_present_child(family):
    child = family.resolve(CHILD_ID)
    assert child is not None
    assert child.session_id == CHILD_ID


def test_resolve_master(family):
    assert family.resolve(MASTER_ID) is family.master


def test_resolve_missing_is_none(family):
    assert family.resolve("missing") is None


# --- scope -----------------------------------------------------------------

def test_scope_self_excludes_child(family):
    graphs = graphs_in_scope(family, "self")
    ids = [g.session_id for g in graphs]
    assert ids == [MASTER_ID]
    assert CHILD_ID not in ids


def test_scope_tree_includes_child(family):
    ids = [g.session_id for g in graphs_in_scope(family, "tree")]
    assert MASTER_ID in ids
    assert CHILD_ID in ids


def test_scope_default_is_tree(family):
    assert [g.session_id for g in graphs_in_scope(family)] == [
        g.session_id for g in graphs_in_scope(family, "tree")
    ]


def test_scope_subagent_id(family):
    ids = [g.session_id for g in graphs_in_scope(family, CHILD_ID)]
    assert ids == [CHILD_ID]


def test_scope_subagents_excludes_the_master(family):
    """`subagents` is the complement of `self`: what did the delegated work cost?

    Plural on purpose -- `budget`'s `by="subagent"` groups results PER child and is a
    different thing, so the two must not share a spelling.
    """
    ids = [g.session_id for g in graphs_in_scope(family, "subagents")]
    assert ids == [CHILD_ID]
    assert family.master.session_id not in ids


def test_scope_typo_raises_naming_the_valid_values(family):
    """This used to return [], so `--scope subagent` (singular) reported zero tokens rather
    than a mistake -- a wrong answer that looked like a real one."""
    with pytest.raises(ValueError) as exc:
        graphs_in_scope(family, "subagent")          # the singular typo that started this
    msg = str(exc.value)
    assert "unknown scope 'subagent'" in msg
    assert "self, tree, subagents" in msg
    assert CHILD_ID in msg, "the error must name the family's real child ids"


def test_scope_spawned_but_unloaded_child_is_empty(family):
    """The partial-family contract: a child the master genuinely spawned but which is not
    loaded is a real state (OTEL-live, or not yet built), not a typo. Empty, never an error."""
    partial = Family(master=family.master, children={})
    assert graphs_in_scope(partial, CHILD_ID) == []


def test_nodes_in_scope_self_vs_tree(family):
    self_nodes = nodes_in_scope(family, "self")
    tree_nodes = nodes_in_scope(family, "tree")
    assert len(tree_nodes) > len(self_nodes)


# --- late-bound cross-session ref ------------------------------------------

def test_spawn_ref_links_to_child(family):
    spawns = [n for n in family.master.nodes.values() if n.type == NodeType.SPAWN]
    assert len(spawns) == 1
    child_ref = spawns[0].refs["child_session_id"]
    assert child_ref == CHILD_ID
    assert family.resolve(child_ref) is family.children[CHILD_ID]


# --- partial family (missing child does not fail assembly) -----------------

def test_partial_family_missing_child(envelope):
    """Dropping the subagent session leaves the spawn ref dangling; build must not fail."""
    envelope["sessions"] = [
        s for s in envelope["sessions"] if s["session_id"] != CHILD_ID
    ]
    family = build_family(envelope)
    assert family.children == {}
    spawn = next(n for n in family.master.nodes.values() if n.type == NodeType.SPAWN)
    assert family.resolve(spawn.refs["child_session_id"]) is None


def test_self_and_subagents_partition_tree(family):
    """`self` and `subagents` are complements: together they are exactly `tree`.

    A partition, not an overlap — proves no graph is double-counted or dropped. Verified on
    the real corpus too: 372,658 + 9,271 = 381,929 output tokens on a 33-subagent session.
    """
    ids = lambda scope: {g.session_id for g in graphs_in_scope(family, scope)}  # noqa: E731
    assert ids("self") | ids("subagents") == ids("tree")
    assert ids("self") & ids("subagents") == set()
