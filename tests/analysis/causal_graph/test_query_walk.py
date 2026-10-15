from analysis.causal_graph.models import Family, Graph, Node, NodeType
from analysis.causal_graph.query import query


def _family():
    master = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    turn = Node(id="turn:1", type=NodeType.TURN, attrs={"turn_num": 1}, children=["a"])
    assistant = Node(id="a", type=NodeType.ASSISTANT, parent_id="turn:1", children=["tool", "spawn"])
    tool = Node(id="tool", type=NodeType.TOOL, parent_id="a", attrs={"tool_name": "Bash", "cmd": ["git"], "is_error": True})
    spawn = Node(id="spawn", type=NodeType.SPAWN, parent_id="a", refs={"child_session_id": "child"})
    for node in (turn, assistant, tool, spawn): master.add(node)
    master.roots.append(turn.id)
    child = Graph(session_id="child", vendor="claude_code", source="jsonl", built="complete", parent_session_id="master")
    child_tool = Node(id="tool", type=NodeType.TOOL, attrs={"tool_name": "Read", "cmd": [], "is_error": True})
    child.add(child_tool)
    return Family(master=master, children={"child": child})


def test_within_contains_and_spawned_walks_are_session_scoped():
    family = _family()
    within = query(family, {"version": "1", "scope": {"kind": "tree"}, "select": {"type": "Tool", "where": "Tool.name == 'Bash'"}, "walk": [{"relation": "within", "type": "Turn"}], "return": {"kind": "nodes", "verbosity": "ref"}})
    assert within.items[0].identity.turn_num == 1
    spawned = query(family, {"version": "1", "scope": {"kind": "tree"}, "select": {"type": "Spawn"}, "walk": [{"relation": "spawned", "type": "Tool", "where": "Tool.name == 'Read'"}], "return": {"kind": "nodes", "verbosity": "ref"}})
    assert spawned.items[0].identity.session_id == "child" and spawned.items[0].identity.node_id == "tool"
