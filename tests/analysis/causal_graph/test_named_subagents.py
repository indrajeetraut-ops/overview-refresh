"""Public subagents() wrapper contract."""
import pytest

import analysis.causal_graph.named_api as named_api
from analysis.causal_graph.models import Family, Graph, Node, NodeType


def _family() -> Family:
    master = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    master.add(Node(id="turn:1", type=NodeType.TURN, children=["spawn"], attrs={"turn_num": 1}))
    master.add(Node(
        id="spawn", type=NodeType.SPAWN, parent_id="turn:1", ts="2026-08-26T10:00:00Z",
        attrs={
            "agent_type": "Explore", "description": "inspect the repository", "prompt_char_count": 22,
            "requested_model": "claude-sonnet", "resolved_model": "claude-sonnet",
            "run_in_background": False, "status": "completed", "duration_ms": 5000,
        },
        refs={"child_session_id": "child-not-loaded"},
    ))
    master.roots.append("turn:1")
    return Family(master=master)


def test_subagents_reports_master_spawn_records_without_loading_children():
    result = named_api.subagents(_family())

    assert result == {
        "version": "1", "kind": "subagents", "status": "complete", "scope": {"kind": "self"},
        "count": 1,
        "subagents": [{
            "session_id": "child-not-loaded", "turn_id": {"session_id": "master", "turn_num": 1},
            "spawn_id": "spawn", "spawned_at": "2026-08-26T10:00:00Z", "agent_type": "Explore",
            "description": "inspect the repository", "prompt_char_count": 22,
            "requested_model": "claude-sonnet", "resolved_model": "claude-sonnet",
            "run_in_background": False, "status": "completed", "duration_ms": 5000,
        }],
        "warnings": [],
    }


@pytest.mark.parametrize("scope", ["tree", "subagents", {"kind": "self"}])
def test_subagents_rejects_non_self_scope(scope):
    with pytest.raises(named_api.NamedApiError) as exc:
        named_api.subagents(_family(), scope=scope)
    assert exc.value.code == "invalid_subagent_scope"
