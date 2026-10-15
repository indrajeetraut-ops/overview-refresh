"""Handwritten select facade across its complete filter and shaping surface."""
from __future__ import annotations

import json

import pytest

from analysis.causal_graph import select
from analysis.causal_graph.models import Family, Graph, Node, NodeType


def family(*, missing_child: bool = False) -> Family:
    graph = Graph(session_id="select-master", vendor="claude_code", source="jsonl", built="complete")
    turn = Node(id="turn:1", type=NodeType.TURN, ts="2026-01-01T00:00:00Z", attrs={"turn_num": 1, "duration_ms": 30}, children=["read", "bash", "edit"])
    read = Node(id="read", type=NodeType.TOOL, ts="2026-01-01T00:00:01Z", parent_id=turn.id, attrs={"tool_name": "Read", "cmd": ["cat"], "duration_ms": 5, "input": {"command": "cat README.md"}, "result": {"outcome": "ok"}})
    bash = Node(id="bash", type=NodeType.TOOL, ts="2026-01-01T00:00:02Z", parent_id=turn.id, attrs={"tool_name": "Bash", "cmd": ["git", "status"], "duration_ms": 20, "input": {"command": "git status"}, "result": {"outcome": "error"}})
    edit = Node(id="edit", type=NodeType.TOOL, ts=None, parent_id=turn.id, attrs={"tool_name": "Edit", "cmd": [], "duration_ms": 10, "input": {"command": ""}})
    compaction = Node(
        id="compact-1", type=NodeType.COMPACTION, ts="2026-01-01T00:00:03Z",
        attrs={
            "trigger": "auto", "tokens_before": 1000, "tokens_after": 100,
            "tokens_reclaimed": 900, "duration_ms": 1500, "usage_known": False,
        },
        refs={"triggering_turn_id": "turn:1", "resumed_turn_id": "turn:1"},
    )
    for node in (turn, read, bash, edit, compaction):
        graph.add(node)
    graph.roots.append(turn.id)
    if missing_child:
        spawn = Node(id="spawn", type=NodeType.SPAWN, refs={"child_session_id": "not-loaded"})
        graph.add(spawn)
        turn.children.append(spawn.id)
    return Family(master=graph)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("Tool.name == 'Read'", ["read"]),
        ("Tool.name != 'Read'", ["bash", "edit"]),
        ("Tool.duration_ms > 10", ["bash"]),
        ("Tool.duration_ms >= 10", ["bash", "edit"]),
        ("Tool.duration_ms < 10", ["read"]),
        ("Tool.duration_ms <= 10", ["read", "edit"]),
        ("Tool.name IN ['Read', 'Edit']", ["read", "edit"]),
        ("Tool.name STARTS_WITH 'Ba'", ["bash"]),
        ("Tool.name ENDS_WITH 'dit'", ["edit"]),
        ("Tool.input.command CONTAINS 'status'", ["bash"]),
        ("Tool.input.command MATCHES '^cat .*[.]md$'", ["read"]),
        ("CASEFOLD(Tool.name) == 'bash'", ["bash"]),
        ("LENGTH(Tool.cmd) == 2", ["bash"]),
        ("Tool.name == 'Read' OR Tool.name == 'Edit'", ["read", "edit"]),
        ("Tool.duration_ms >= 10 AND NOT Tool.name == 'Edit'", ["bash"]),
        ("Tool.ts == null", ["edit"]),
    ],
)
def test_select_supports_every_public_filter_operation(expression: str, expected: list[str]) -> None:
    result = select(family(), "Tool", where=expression, scope="self", verbosity="ref")
    assert [item.identity.node_id for item in result.items or []] == expected


def test_select_fields_order_limit_and_serializable_result() -> None:
    result = select(
        family(), "Tool", fields=["Tool.input.command"],
        order_by=[{"field": "Tool.duration_ms", "direction": "desc"}], limit=2,
    )
    assert [item.identity.node_id for item in result.items or []] == ["bash", "edit"]
    assert result.truncated is True
    assert result.items[0].fields["Tool.input.command"] == "git status"
    json.dumps(result.model_dump(mode="json"))


def test_select_nulls_sort_last_for_both_directions() -> None:
    for direction in ("asc", "desc"):
        result = select(family(), "Tool", order_by=[{"field": "Tool.ts", "direction": direction}])
        assert result.items[-1].identity.node_id == "edit"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"where": "Tool.made_up == 1"},
        {"fields": ["Tool.made_up"]},
        {"order_by": [{"field": "Tool.made_up", "direction": "asc"}]},
        {"where": "Tool.name =="},
        {"limit": 0},
    ],
)
def test_select_rejects_invalid_fields_expressions_and_limits(kwargs: dict) -> None:
    with pytest.raises(Exception):
        select(family(), "Tool", **kwargs)


def test_select_marks_tree_partial_when_a_referenced_child_is_unloaded() -> None:
    result = select(family(missing_child=True), "Tool")
    assert result.status == "partial"
    assert result.warnings[0].code == "child_graph_unloaded"


def test_select_compaction_supports_filters_fields_and_full_shape() -> None:
    result = select(
        family(),
        "Compaction",
        where="Compaction.trigger == 'auto' AND Compaction.tokens_reclaimed >= 900",
        scope="self",
        fields=["Compaction.tokens_before", "Compaction.usage_known"],
        verbosity="full",
    )
    assert result.status == "complete"
    assert result.kind == "nodes"
    assert len(result.items or []) == 1
    item = result.items[0]
    assert item.identity.model_dump(exclude_none=True) == {
        "session_id": "select-master", "node_id": "compact-1",
    }
    assert item.fields["Compaction.tokens_before"] == 1000
    assert item.fields["Compaction.usage_known"] is False
    assert item.node["refs"] == {
        "triggering_turn_id": "turn:1", "resumed_turn_id": "turn:1",
    }
