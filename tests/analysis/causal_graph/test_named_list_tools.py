"""Public list_tools() wrapper contract."""
import pytest

import analysis.causal_graph.named_api as named_api
from analysis.causal_graph.models import Family, Graph, Node, NodeType


def _family() -> Family:
    graph = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    for node in (
        Node(id="read", type=NodeType.TOOL, attrs={"tool_name": "Read", "cmd": []}),
        Node(id="mcp", type=NodeType.TOOL, attrs={"tool_name": "mcp__drive__search", "cmd": []}),
        Node(id="bash-one", type=NodeType.TOOL, attrs={"tool_name": "Bash", "cmd": ["git", "git", "sed"]}),
        Node(id="bash-two", type=NodeType.TOOL, attrs={"tool_name": "Bash", "cmd": ["git"]}),
    ):
        graph.add(node)
    return Family(master=graph)


def test_list_tools_reports_harness_and_command_entries_with_stable_counts():
    result = named_api.list_tools(_family())

    assert result == {
        "version": "1",
        "kind": "tool_list",
        "status": "complete",
        "scope": {"kind": "tree"},
        "count": 5,
        "tools": [
            {"tool_class": "harness", "name": "Bash", "invocation_count": 2},
            {"tool_class": "harness", "name": "Read", "invocation_count": 1},
            {"tool_class": "harness", "name": "mcp__drive__search", "invocation_count": 1},
            {"tool_class": "command", "name": "git", "invocation_count": 2},
            {"tool_class": "command", "name": "sed", "invocation_count": 1},
        ],
        "warnings": [],
    }


def test_list_tools_honors_scope_and_rejects_invalid_scope():
    assert named_api.list_tools(_family(), scope="self")["count"] == 5
    with pytest.raises(named_api.NamedApiError) as exc:
        named_api.list_tools(_family(), scope="bad")
    assert exc.value.code == "invalid_scope"
