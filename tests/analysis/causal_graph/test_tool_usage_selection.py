"""Tool-usage contract cases represented by the public graph fields.

The named wrapper is intentionally not implemented until its contract review is approved. These
tests pin the graph facts that its thin QuerySpec translation must use: harness names, extracted
commands, completed outcomes, and the full recorded shell invocation.
"""
from analysis.causal_graph.models import Family, Graph, Node, NodeType
from analysis.causal_graph.query import query


def _family() -> Family:
    graph = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    tools = (
        Node(
            id="bash-status", type=NodeType.TOOL,
            attrs={
                "tool_name": "Bash", "cmd": ["git", "sed"],
                "input": {"command": "git status | sed -n '1,20p'"},
                "result": {"outcome": "ok", "process": {"interrupted": False}},
            },
        ),
        Node(
            id="bash-push", type=NodeType.TOOL,
            attrs={
                "tool_name": "Bash", "cmd": ["git"],
                "input": {"command": "git push origin feature/query"},
                "result": {"outcome": "error", "error_text": "remote rejected"},
            },
        ),
        Node(
            id="read", type=NodeType.TOOL,
            attrs={"tool_name": "Read", "cmd": [], "result": {"outcome": "ok"}},
        ),
        Node(
            id="unpaired", type=NodeType.TOOL,
            attrs={"tool_name": "web_search", "cmd": [], "is_error": False},
        ),
    )
    for tool in tools:
        graph.add(tool)
    return Family(master=graph)


def _ids(where: str | None = None) -> list[str]:
    result = query(
        _family(),
        {
            "version": "1",
            "scope": {"kind": "tree"},
            "select": {"type": "Tool", **({"where": where} if where else {})},
            "return": {"kind": "nodes", "verbosity": "ref"},
        },
    )
    return [item.identity.node_id for item in result.items]


def test_harness_selection_is_distinct_from_extracted_command_selection():
    assert _ids("Tool.name == 'Bash'") == ["bash-status", "bash-push"]
    assert _ids("Tool.cmd == 'git'") == ["bash-status", "bash-push"]
    assert _ids("Tool.cmd == 'sed'") == ["bash-status"]


def test_outcome_selection_includes_success_and_failure_but_not_unpaired_calls():
    assert _ids("Tool.outcome == 'success'") == ["bash-status", "read"]
    assert _ids("Tool.outcome == 'failure'") == ["bash-push"]
    assert _ids("Tool.outcome == 'unknown'") == ["unpaired"]


def test_complete_shell_invocation_supports_bounded_re2_matching():
    assert _ids("Tool.input.command MATCHES '^git\\\\s+push\\\\b'") == ["bash-push"]
