"""Public tool_usage() wrapper contract."""
import pytest

import analysis.causal_graph.named_api as named_api
from analysis.causal_graph.models import Family, Graph, Node, NodeType


def _family() -> Family:
    graph = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    for node in (
        Node(id="bash-status", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["git", "sed"], "duration_ms": 5, "exit_status": 0,
            "input": {"command": "git status | sed -n '1,20p'"},
            "result": {"outcome": "ok", "process": {"interrupted": False}},
        }),
        Node(id="bash-push", type=NodeType.TOOL, attrs={
            "tool_name": "Bash", "cmd": ["git"], "duration_ms": 8, "exit_status": 1,
            "input": {"command": "git push origin feature/query"}, "tool_denial_kind": "permission-rule",
            "result": {"outcome": "denied", "error_text": "permission denied"},
        }),
        Node(id="read", type=NodeType.TOOL, attrs={
            "tool_name": "Read", "cmd": [], "result": {"outcome": "ok"},
        }),
        Node(id="unpaired", type=NodeType.TOOL, attrs={
            "tool_name": "web_search", "cmd": [], "is_error": False,
        }),
    ):
        graph.add(node)
    return Family(master=graph)


def test_tool_usage_selects_harness_command_outcome_and_complete_invocation():
    harness = named_api.tool_usage(_family(), tool_class="harness", name="Bash")
    assert harness["count"] == 2
    assert [tool["identity"]["node_id"] for tool in harness["tools"]] == ["bash-status", "bash-push"]

    command = named_api.tool_usage(
        _family(), tool_class="command", name="git", outcome="failure",
        invocation_matches=r"^git\s+push\b",
    )
    assert command["count"] == 1
    assert command["tools"] == [{
        "identity": {"session_id": "master", "node_id": "bash-push"},
        "harness_name": "Bash", "commands": ["git"],
        "invocation": "git push origin feature/query", "outcome": "failure",
        "duration_ms": 8, "exit_status": 1, "denial_kind": "permission-rule",
        "failure_message": "permission denied",
    }]


def test_tool_usage_all_includes_unknown_and_limit_reports_prelimit_count():
    result = named_api.tool_usage(_family(), limit=2)

    assert result["count"] == 4
    assert result["returned_count"] == 2
    assert result["truncated"] is True
    assert [tool["outcome"] for tool in result["tools"]] == ["success", "failure"]


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"tool_class": "unknown"}, "invalid_tool_selector"),
        ({"name": "git"}, "invalid_tool_selector"),
        ({"outcome": "unknown"}, "invalid_tool_outcome"),
        ({"invocation_matches": "("}, "invalid_invocation_regex"),
        ({"limit": 0}, "invalid_tool_limit"),
    ],
)
def test_tool_usage_rejects_invalid_public_inputs(kwargs, code):
    with pytest.raises(named_api.NamedApiError) as exc:
        named_api.tool_usage(_family(), **kwargs)
    assert exc.value.code == code
