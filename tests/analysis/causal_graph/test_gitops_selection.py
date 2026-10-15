"""Selection facts required by the future gitops() named API."""
from analysis.causal_graph.models import Family, Graph, Node, NodeType
from analysis.causal_graph.query import query


def _family() -> Family:
    graph = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    for node in (
        Node(id="commit", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["git"], "input": {"command": "git commit -m 'query'"},
            "result": {"git": {"op": "commit", "sha": "abc123", "kind": "committed"}},
        }),
        Node(id="push", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["git"], "input": {"command": "git push origin feature/query"},
            "result": {"git": {"op": "push", "branch": "feature/query"}},
        }),
        Node(id="pr", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["gh"], "input": {"command": "gh pr create"},
            "result": {"git": {"op": "pr", "number": 8, "url": "https://example.test/pr/8", "action": "created"}},
        }),
        Node(id="branch", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["git"], "input": {"command": "git switch feature/query"},
            "result": {"git": {"op": "branch", "ref": "feature/query", "action": "created"}},
        }),
        Node(id="unstructured", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["git"], "input": {"command": "git status"},
            "result": {"outcome": "ok"},
        }),
    ):
        graph.add(node)
    return Family(master=graph)


def _ids(where: str) -> list[str]:
    result = query(
        _family(),
        {
            "version": "1",
            "scope": {"kind": "tree"},
            "select": {"type": "Tool", "where": where},
            "return": {
                "kind": "nodes", "verbosity": "summary",
                "fields": ["Tool.result.git"],
            },
        },
    )
    return [item.identity.node_id for item in result.items]


def test_gitops_operation_filter_selects_recognized_operation_kind_only():
    assert _ids("Tool.result.git.op == 'commit'") == ["commit"]
    assert _ids("Tool.result.git.op IN ['commit', 'push', 'pr', 'branch']") == ["commit", "push", "pr", "branch"]
