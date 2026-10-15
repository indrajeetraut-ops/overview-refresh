"""Public turns() wrapper contract."""
import pytest

import analysis.causal_graph.named_api as named_api
from analysis.causal_graph.models import Family, Graph, Node, NodeType


def _family() -> Family:
    graph = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    graph.add(Node(
        id="turn:3", type=NodeType.TURN, ts="2026-08-26T10:00:00Z",
        children=["prompt", "assistant", "bash", "mcp", "spawn"],
        attrs={
            "turn_num": 3, "status": "complete", "duration_ms": 92,
            "permission_mode": "default", "plan_mode": False,
            "attribution": {"skill": ["repo"], "plugin": [], "agent": ["lead"], "effort": ["medium"]},
        },
    ))
    graph.add(Node(id="prompt", type=NodeType.USER_PROMPT, parent_id="turn:3", attrs={"preview": "inspect the tree"}))
    graph.add(Node(
        id="assistant", type=NodeType.ASSISTANT, parent_id="turn:3",
        attrs={"model": "gpt-5", "input_tokens": 10, "output_tokens": 4, "cache_read_tokens": 2,
               "cache_write_tokens": 1, "estimated_reasoning_tokens": 3},
    ))
    graph.add(Node(
        id="bash", type=NodeType.TOOL, parent_id="turn:3",
        attrs={"tool_name": "Bash", "cmd": ["git", "sed"], "result": {"outcome": "ok"}, "duration_ms": 9},
    ))
    graph.add(Node(
        id="mcp", type=NodeType.TOOL, parent_id="turn:3",
        attrs={"tool_name": "mcp__drive__search", "cmd": [], "result": {"outcome": "ok"}},
    ))
    graph.add(Node(
        id="spawn", type=NodeType.SPAWN, parent_id="turn:3", ts="2026-08-26T10:01:00Z",
        attrs={"agent_type": "research", "description": "inspect docs", "status": "started"},
        refs={"child_session_id": "child"},
    ))
    graph.roots.append("turn:3")
    return Family(master=graph)


def test_turns_returns_stable_turn_summary_and_contained_inventories():
    result = named_api.turns(_family(), turn_id={"session_id": "master", "turn_num": 3})

    assert result["kind"] == "turns"
    assert result["status"] == "complete"
    assert result["turn"]["identity"] == {"session_id": "master", "turn_num": 3}
    assert result["turn"]["models"] == ["gpt-5"]
    assert result["turn"]["usage"] == {
        "input_tokens": 10, "output_tokens": 4, "cache_read_tokens": 2, "cache_write_tokens": 1,
        "reasoning_tokens": 3, "reasoning_included_in_output_tokens": True,
    }
    assert result["turn"]["tool_count"] == 2
    assert result["turn"]["tools_used"] == [
        {"tool_class": "harness", "name": "Bash", "invocation_count": 1},
        {"tool_class": "harness", "name": "mcp__drive__search", "invocation_count": 1},
        {"tool_class": "command", "name": "git", "invocation_count": 1},
        {"tool_class": "command", "name": "sed", "invocation_count": 1},
    ]
    assert result["turn"]["subagent_spawns"] == {
        "count": 1,
        "spawns": [{"session_id": "child", "spawn_id": "spawn", "spawned_at": "2026-08-26T10:01:00Z",
                    "agent_type": "research", "description": "inspect docs", "status": "started"}],
    }
    assert result["turn"]["mcp_invocations"] == {
        "count": 1, "tools": [{"name": "mcp__drive__search", "invocation_count": 1}],
    }
    assert "nodes" not in result


@pytest.mark.parametrize("node_type, expected_ids", [
    ("tool", ["bash", "mcp"]),
    ("node", ["prompt", "assistant", "bash", "mcp", "spawn"]),
])
def test_turns_optionally_returns_contained_node_summaries(node_type, expected_ids):
    result = named_api.turns(_family(), turn_id={"session_id": "master", "turn_num": 3}, node_type=node_type)

    assert [node["identity"]["node_id"] for node in result["nodes"]] == expected_ids
    assert all(node["identity"]["session_id"] == "master" for node in result["nodes"])


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"turn_id": {"session_id": "master"}}, "invalid_turn_id"),
        ({"turn_id": {"session_id": "missing", "turn_num": 1}}, "turn_not_loaded"),
        ({"turn_id": {"session_id": "master", "turn_num": 2}}, "turn_not_loaded"),
        ({"turn_id": {"session_id": "master", "turn_num": 3}, "node_type": "turn"}, "invalid_turn_node_type"),
    ],
)
def test_turns_rejects_invalid_public_inputs(kwargs, code):
    with pytest.raises(named_api.NamedApiError) as exc:
        named_api.turns(_family(), **kwargs)
    assert exc.value.code == code
