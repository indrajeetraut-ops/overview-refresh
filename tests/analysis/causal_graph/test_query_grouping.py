from analysis.causal_graph.models import Family, Graph, Node, NodeType
from analysis.causal_graph.query import query


def _family():
    graph = Graph(session_id="s", vendor="claude_code", source="jsonl", built="complete")
    for index, name in enumerate(("Read", "Read", "Bash")):
        graph.add(Node(id=str(index), type=NodeType.TOOL, attrs={"tool_name": name, "cmd": ["git"] if name == "Bash" else [], "duration_ms": index + 1, "is_error": False}))
    return Family(master=graph)


def test_group_count_having_order_and_members():
    result = query(_family(), {"version": "1", "scope": {"kind": "tree"}, "select": {"type": "Tool"}, "group_by": ["Tool.name"], "aggregates": [{"name": "count", "function": "COUNT"}], "having": "count > 1", "order_by": [{"field": "count", "direction": "desc"}], "return": {"kind": "groups", "verbosity": "ref", "include_members": True}})
    assert result.groups[0].key == {"Tool.name": "Read"}
    assert result.groups[0].aggregates == {"count": 2}
    assert len(result.groups[0].members) == 2


def test_aggregate_only_median():
    result = query(_family(), {"version": "1", "scope": {"kind": "tree"}, "select": {"type": "Tool"}, "aggregates": [{"name": "median_duration", "function": "MEDIAN", "field": "Tool.duration_ms"}], "return": {"kind": "aggregate"}})
    assert result.aggregate == {"median_duration": 2}
