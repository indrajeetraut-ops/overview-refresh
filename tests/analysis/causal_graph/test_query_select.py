from analysis.causal_graph.models import Family, Graph, Node, NodeType
from analysis.causal_graph.query import query


def _family():
    graph = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    turn = Node(id="turn:1", type=NodeType.TURN, attrs={"turn_num": 1, "status": "interrupted"}, children=["a"])
    assistant = Node(id="a", type=NodeType.ASSISTANT, attrs={"model": "opus"}, parent_id="turn:1", children=["t"])
    tool = Node(id="t", type=NodeType.TOOL, attrs={"tool_name": "Read", "cmd": [], "is_error": False}, parent_id="a")
    for node in (turn, assistant, tool): graph.add(node)
    graph.roots.append(turn.id)
    return Family(master=graph)


def test_select_returns_session_scoped_node_result_identity():
    result = query(_family(), {"version": "1", "scope": {"kind": "tree"}, "select": {"type": "Tool", "where": "Tool.name == 'Read'"}, "return": {"kind": "nodes", "verbosity": "summary"}})
    assert result.status == "complete"
    assert result.items[0].identity.model_dump(exclude_none=True) == {"session_id": "master", "node_id": "t"}
