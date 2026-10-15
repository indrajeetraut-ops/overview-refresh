"""Aggregation facts required by the future list_tools() named API."""
from analysis.causal_graph.models import Family, Graph, Node, NodeType
from analysis.causal_graph.query import query


def _family() -> Family:
    graph = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    for node in (
        Node(id="read", type=NodeType.TOOL, attrs={"tool_name": "Read", "cmd": []}),
        Node(id="mcp", type=NodeType.TOOL, attrs={
            "tool_name": "mcp__drive__search", "cmd": [],
        }),
        Node(id="bash-one", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["git", "git", "sed"],
        }),
        Node(id="bash-two", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["git"],
        }),
    ):
        graph.add(node)
    return Family(master=graph)


def _counts(field: str) -> dict[str, int]:
    result = query(
        _family(),
        {
            "version": "1",
            "scope": {"kind": "tree"},
            "select": {"type": "Tool"},
            "group_by": [field],
            "aggregates": [{"name": "invocation_count", "function": "COUNT"}],
            "return": {"kind": "groups", "verbosity": "ref"},
        },
    )
    return {group.key[field]: group.aggregates["invocation_count"] for group in result.groups}


def test_harness_inventory_includes_mcp_tools_and_counts_each_tool_call():
    assert _counts("Tool.name") == {
        "Read": 1,
        "mcp__drive__search": 1,
        "Bash": 2,
    }


def test_command_inventory_counts_each_distinct_command_once_per_tool_call():
    assert _counts("Tool.cmd") == {"git": 2, "sed": 1}
