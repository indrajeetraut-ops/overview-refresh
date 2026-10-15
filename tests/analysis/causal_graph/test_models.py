"""Step 0 contract tests: Node/Graph/Family construct, and Graph round-trips through the
§4.3 envelope shape. Assertions fail if the contract were reverted, not merely "tests pass".
"""
from analysis.causal_graph.models import SCHEMA_VERSION, Family, Graph, Node, NodeType


def test_schema_version_is_current():
    assert SCHEMA_VERSION == "4.3.0"


def _sample_graph() -> Graph:
    g = Graph(
        session_id="s1", vendor="claude_code", source="jsonl", built="complete",
        meta={"title": "Review system architecture documentation", "objective": None},
    )
    g.add(Node(id="turn:1", type=NodeType.TURN, children=["a1"],
               attrs={"turn_num": 1, "prompt_id": "p1", "status": "completed",
                      "duration_ms": 86408}))
    g.add(Node(id="a1", type=NodeType.ASSISTANT, ts="2026-07-10T00:25:32.739Z",
               attrs={"message_id": "m1", "model": "claude-opus-4-8",
                      "input_tokens": 10, "output_tokens": 2}))
    g.roots = ["turn:1"]
    return g


def test_node_construct_defaults():
    n = Node(id="x", type=NodeType.TOOL)
    assert n.ts is None
    assert n.attrs == {} and n.meta == {} and n.children == [] and n.refs == {}
    assert n.type == "tool"  # str-enum: compares equal to its value


def test_graph_to_dict_is_envelope_shape():
    d = _sample_graph().to_dict()
    assert d["schema_version"] == SCHEMA_VERSION
    assert set(d) == {
        "schema_version", "session_id", "parent_session_id", "vendor",
        "source", "built", "meta", "nodes", "roots",
    }
    assert isinstance(d["nodes"], list)  # envelope serializes nodes as a LIST, not an index
    assert d["nodes"][0]["type"] == "turn"  # enum -> string value
    assert d["roots"] == ["turn:1"]


def test_graph_round_trips():
    g = _sample_graph()
    g2 = Graph.from_dict(g.to_dict())
    assert g2.to_dict() == g.to_dict()  # exact round-trip
    assert isinstance(g2.nodes, dict)  # rebuilt as an id-indexed map in memory
    assert g2.nodes["a1"].attrs["message_id"] == "m1"
    assert g2.nodes["turn:1"].type is NodeType.TURN


def test_from_dict_ignores_unknown_keys():
    d = _sample_graph().to_dict()
    d["some_future_field"] = 123  # additive evolution / must-ignore
    g = Graph.from_dict(d)
    assert "a1" in g.nodes and g.schema_version == SCHEMA_VERSION


def test_from_dict_loads_a_2_0_0_era_envelope():
    """A graph built before the 2.1.0 attrs existed must still load: the new turn/assistant/
    tool/spawn attrs are additive, so an envelope stamped `2.0.0` -- with none of them present
    on any node's `attrs` -- is valid input, not a validation error (design §4.3, models.py
    version-history comment)."""
    d = {
        "schema_version": "2.0.0",
        "session_id": "s1",
        "parent_session_id": None,
        "vendor": "claude_code",
        "source": "jsonl",
        "built": "complete",
        "meta": {"title": "pre-2.1.0 session", "objective": None},
        "roots": ["t1"],
        "nodes": [
            {"id": "t1", "type": "turn", "children": ["a1"],
             "attrs": {"prompt_id": "p1", "status": "completed", "duration_ms": 86408}},
            {"id": "a1", "type": "assistant", "ts": "2026-07-10T00:25:32.739Z",
             "attrs": {"message_id": "m1", "model": "claude-opus-4-8",
                       "input_tokens": 10, "output_tokens": 2}},
        ],
    }
    g = Graph.from_dict(d)
    assert g.schema_version == "2.0.0"  # the envelope's own stamp, not upgraded on load
    assert g.nodes["t1"].attrs == {"prompt_id": "p1", "status": "completed",
                                    "duration_ms": 86408}  # no permission_mode/plan_mode/attribution
    assert g.nodes["a1"].attrs["model"] == "claude-opus-4-8"
    assert "attribution_skill" not in g.nodes["a1"].attrs and "effort" not in g.nodes["a1"].attrs


def test_family_resolve():
    master = _sample_graph()
    child = Graph(session_id="agent-1", vendor="claude_code", source="jsonl",
                  built="complete", parent_session_id="s1")
    fam = Family(master=master, children={"agent-1": child})
    assert fam.resolve("s1") is master
    assert fam.resolve("agent-1") is child
    assert fam.resolve("missing") is None  # late-bound / partial: never raises
    assert fam.all_graphs() == [master, child]
