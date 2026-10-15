"""context_curve_of / info.context_curve (D1-context-curve, d1-d3-implementation plan T1): the
per-turn context-window-size series SessionScreen's "Context over the session" chart reads. The
value is a turn's END-of-turn per-request context (`cache_read_tokens + cache_write_tokens` of the
turn's last assistant request) -- the same per-request context `carry_of` already computes but
throws away except for its last point. `compaction` flags a turn a compaction fired within.
"""
from analysis.causal_graph.models import Family, Graph, Node, NodeType
from server.worker.graph_fold import context_curve_of


def _turn(node_id: str, turn_num: int, ts: str, child_ids: list[str]) -> Node:
    return Node(id=node_id, type=NodeType.TURN, ts=ts,
                attrs={"turn_num": turn_num}, children=child_ids)


def _assistant(node_id: str, ts: str, cache_read: int, cache_write: int) -> Node:
    return Node(id=node_id, type=NodeType.ASSISTANT, ts=ts,
                attrs={"cache_read_tokens": cache_read, "cache_write_tokens": cache_write})


def _graph_with(*nodes: Node) -> Family:
    graph = Graph(session_id="s1", vendor="claude_code", source="jsonl", built="complete")
    for n in nodes:
        graph.add(n)
    return Family(master=graph, children={})


def test_series_is_end_of_turn_context_per_turn():
    """One point per turn; the value is the LAST assistant request's cache_read+cache_write in
    that turn (the context live at the end of the turn), and context grows turn over turn."""
    fam = _graph_with(
        _turn("t1", 1, "2026-01-01T00:00:00Z", ["a1"]),
        _assistant("a1", "2026-01-01T00:00:01Z", cache_read=0, cache_write=10_000),
        _turn("t2", 2, "2026-01-01T00:01:00Z", ["a2a", "a2b"]),
        _assistant("a2a", "2026-01-01T00:01:01Z", cache_read=10_000, cache_write=5_000),
        _assistant("a2b", "2026-01-01T00:01:30Z", cache_read=15_000, cache_write=3_000),
    )
    curve = context_curve_of(fam, "s1")
    assert [p["index"] for p in curve] == [1, 2]
    assert curve[0]["context_tokens"] == 10_000              # 0 + 10_000
    assert curve[1]["context_tokens"] == 18_000              # last request in t2: 15_000 + 3_000
    assert curve[1]["ts"] == "2026-01-01T00:01:30Z"          # end-of-turn timestamp
    assert all(p["compaction"] is False for p in curve)


def test_compaction_within_a_turn_is_flagged():
    fam = _graph_with(
        _turn("t1", 1, "2026-01-01T00:00:00Z", ["a1"]),
        _assistant("a1", "2026-01-01T00:00:30Z", cache_read=40_000, cache_write=2_000),
        Node(id="c1", type=NodeType.COMPACTION, ts="2026-01-01T00:00:20Z",
             attrs={"trigger": "auto", "tokens_before": 42_000, "tokens_after": 8_000}),
        _turn("t2", 2, "2026-01-01T00:01:00Z", ["a2"]),
        _assistant("a2", "2026-01-01T00:01:10Z", cache_read=8_000, cache_write=1_000),
    )
    curve = context_curve_of(fam, "s1")
    assert curve[0]["compaction"] is True    # compaction ts falls inside t1's [start, last-assistant]
    assert curve[1]["compaction"] is False


def test_a_tool_only_turn_contributes_no_point():
    """A turn whose descendants are all tool calls (no assistant request) has no context value
    to report -- it is skipped, not emitted as a zero."""
    fam = _graph_with(
        _turn("t1", 1, "2026-01-01T00:00:00Z", ["x1"]),
        Node(id="x1", type=NodeType.TOOL, ts="2026-01-01T00:00:01Z", attrs={}),
        _turn("t2", 2, "2026-01-01T00:01:00Z", ["a2"]),
        _assistant("a2", "2026-01-01T00:01:10Z", cache_read=5_000, cache_write=1_000),
    )
    curve = context_curve_of(fam, "s1")
    assert [p["index"] for p in curve] == [2]


def test_no_graph_is_empty_series():
    graph = Graph(session_id="other", vendor="claude_code", source="jsonl", built="complete")
    fam = Family(master=graph, children={})
    assert context_curve_of(fam, "s1") == []
