"""Public gitops() wrapper contract."""
import pytest

import analysis.causal_graph.named_api as named_api
from analysis.causal_graph.models import Family, Graph, Node, NodeType


def _family() -> Family:
    graph = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    for node in (
        Node(id="commit", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["git"], "input": {"command": "git commit -m query"},
            "result": {"git": {"op": "commit", "sha": "abc123", "kind": "committed"}},
        }),
        Node(id="push", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["git"], "input": {"command": "git push origin query"},
            "result": {"git": {"op": "push", "branch": "query"}},
        }),
        Node(id="pr", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["gh"], "input": {"command": "gh pr create"},
            "result": {"git": {"op": "pr", "number": 7, "url": "https://example.test/pr/7", "action": "created"}},
        }),
        Node(id="branch", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["git"], "input": {"command": "git switch query"},
            "result": {"git": {"op": "branch", "ref": "query", "action": "created"}},
        }),
        Node(id="unstructured", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["git"], "input": {"command": "git status"},
            "result": {"outcome": "ok"},
        }),
    ):
        graph.add(node)
    return Family(master=graph)


def test_gitops_returns_only_recognized_operations_and_stable_details():
    result = named_api.gitops(_family())

    assert result["count"] == 4
    assert result["returned_count"] == 4
    assert [item["operation"] for item in result["operations"]] == ["commit", "push", "pr", "branch"]
    assert [item["details"] for item in result["operations"]] == [
        {"sha": "abc123", "kind": "committed"},
        {"branch": "query"},
        {"number": 7, "url": "https://example.test/pr/7", "action": "created"},
        {"ref": "query", "action": "created"},
    ]


@pytest.mark.parametrize("operation", ["commit", "push", "pr", "branch"])
def test_gitops_filters_each_operation_kind(operation):
    result = named_api.gitops(_family(), operation=operation)
    assert result["count"] == 1
    assert result["operations"][0]["operation"] == operation


def test_gitops_limit_reports_prelimit_count_and_truncation():
    result = named_api.gitops(_family(), limit=2)
    assert result["count"] == 4
    assert result["returned_count"] == 2
    assert result["truncated"] is True


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"operation": "status"}, "invalid_git_operation"),
        ({"operation": True}, "invalid_git_operation"),
        ({"limit": 0}, "invalid_git_limit"),
    ],
)
def test_gitops_rejects_invalid_public_inputs(kwargs, code):
    with pytest.raises(named_api.NamedApiError) as exc:
        named_api.gitops(_family(), **kwargs)
    assert exc.value.code == code
