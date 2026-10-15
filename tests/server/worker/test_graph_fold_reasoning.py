"""reasoning_tokens_of / info.reasoning_tokens (F5, mvp-gap-closure-plan "Track reasoning
rollup"): the fold-side sum of each assistant node's own `estimated_reasoning_tokens`
(`analysis/causal_graph/reasoning.py`, already attached per-node at build time) -- the piece
`aggregate.reasoning_share` was waiting on (`TODO(F5-worker-gap)`, now closed).
"""
from analysis.causal_graph.models import Family, Graph, Node, NodeType
from server.worker.graph_fold import reasoning_tokens_of


def _assistant_node(node_id: str, estimated_reasoning_tokens, reasoning_source=None) -> Node:
    return Node(id=node_id, type=NodeType.ASSISTANT,
               attrs={"estimated_reasoning_tokens": estimated_reasoning_tokens,
                     "reasoning_source": reasoning_source})


def test_sums_reasoning_tokens_across_assistant_nodes():
    graph = Graph(session_id="s1", vendor="claude_code", source="jsonl", built="complete")
    graph.add(_assistant_node("a1", 120, "estimated"))
    graph.add(_assistant_node("a2", 80, "reported"))
    family = Family(master=graph, children={})

    assert reasoning_tokens_of(family, "s1") == 200


def test_a_node_with_no_source_contributes_zero_not_a_crash():
    """`estimated_reasoning_tokens` is `None` when the upstream `output_tokens` field itself
    was missing (reasoning.py's own contract-guard branch) -- a data gap for that one node,
    not grounds to fail the whole session's rollup."""
    graph = Graph(session_id="s1", vendor="claude_code", source="jsonl", built="complete")
    graph.add(_assistant_node("a1", None, None))
    graph.add(_assistant_node("a2", 50, "estimated"))
    family = Family(master=graph, children={})

    assert reasoning_tokens_of(family, "s1") == 50


def test_non_assistant_nodes_are_ignored():
    graph = Graph(session_id="s1", vendor="claude_code", source="jsonl", built="complete")
    graph.add(_assistant_node("a1", 30, "estimated"))
    graph.add(Node(id="t1", type=NodeType.TOOL, attrs={"estimated_reasoning_tokens": 999}))
    family = Family(master=graph, children={})

    assert reasoning_tokens_of(family, "s1") == 30


def test_a_session_with_no_assistant_activity_is_zero():
    graph = Graph(session_id="s1", vendor="claude_code", source="jsonl", built="complete")
    family = Family(master=graph, children={})

    assert reasoning_tokens_of(family, "s1") == 0


def test_a_session_with_no_graph_at_all_is_zero():
    """`_self_graph` returns `None` when `family.resolve(session_id)` can't find this session's
    own graph (e.g. an otel_only stub with no JSONL fold ever run) -- same floor every other
    `_of` helper in this module already applies."""
    graph = Graph(session_id="other", vendor="claude_code", source="jsonl", built="complete")
    family = Family(master=graph, children={})

    assert reasoning_tokens_of(family, "s1") == 0
