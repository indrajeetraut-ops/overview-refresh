"""Stable memory_usage() domain contract."""
from __future__ import annotations

import pytest

import analysis.causal_graph.named_api as named_api
from analysis.causal_graph.models import Family, Graph, Node, NodeType


def _usage(model: str, effort: str | None, values: tuple[int, int, int, int, int]) -> dict:
    return {
        "message_id": f"message-{model}-{values[0]}",
        "model": model,
        "effort": effort,
        "input_tokens": values[0],
        "output_tokens": values[1],
        "cache_read_tokens": values[2],
        "cache_write_tokens": values[3],
        "estimated_reasoning_tokens": values[4],
    }


def _memory_file(path: str, operation: str, *, kind: str = "entry", hunks=None) -> dict:
    return {
        "path": path,
        "extension": ".md",
        "artifact_class": "documentation",
        "operation": operation,
        "hunks": [] if hunks is None else hunks,
        "is_memory": True,
        "memory_kind": kind,
    }


def _add_response(
    graph: Graph,
    *,
    assistant_id: str,
    usage: dict,
    tools: list[Node],
    other_actions: list[Node] | None = None,
) -> None:
    actions = [*tools, *(other_actions or [])]
    assistant = Node(
        id=assistant_id,
        type=NodeType.ASSISTANT,
        attrs=usage,
        children=[node.id for node in actions],
        parent_id="turn:1",
    )
    graph.add(assistant)
    for node in actions:
        node.parent_id = assistant_id
        graph.add(node)


def _family() -> Family:
    master = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    master.add(Node(id="turn:1", type=NodeType.TURN, attrs={"turn_num": 1}, children=["a1", "a2"]))
    _add_response(
        master,
        assistant_id="a1",
        usage=_usage("opus", "high", (10, 20, 30, 40, 5)),
        tools=[
            Node(id="read", type=NodeType.TOOL, attrs={
                "memory_access": True,
                "files": [_memory_file("/home/.claude/projects/-repo/memory/notes.md", "read")],
            }),
            Node(id="edit", type=NodeType.TOOL, attrs={
                "memory_access": True,
                "files": [_memory_file(
                    "/home/.claude/projects/-repo/memory/notes.md",
                    "edit",
                    hunks=[{"start": 2, "len": 4, "old_len": 2}],
                )],
            }),
        ],
    )
    _add_response(
        master,
        assistant_id="a2",
        usage=_usage("sonnet", None, (1, 2, 3, 4, 1)),
        tools=[Node(id="write", type=NodeType.TOOL, attrs={
            "memory_access": True,
            "files": [_memory_file(
                "/home/.claude/projects/-repo/memory/MEMORY.md", "create", kind="index",
            )],
        })],
        other_actions=[Node(id="bash", type=NodeType.TOOL, attrs={"tool_name": "Bash"})],
    )

    child = Graph(
        session_id="child", vendor="claude_code", source="jsonl", built="complete",
        parent_session_id="master",
    )
    child.add(Node(id="turn:1", type=NodeType.TURN, attrs={"turn_num": 1}, children=["ca1"]))
    _add_response(
        child,
        assistant_id="ca1",
        usage=_usage("haiku", "low", (100, 200, 300, 400, 50)),
        tools=[Node(id="child-read", type=NodeType.TOOL, attrs={
            "memory_access": True,
            "files": [_memory_file("/home/.claude/projects/-repo/memory/child.md", "read")],
        })],
    )
    return Family(master=master, children={"child": child})


def test_memory_usage_deduplicates_response_usage_and_separates_reads_writes_and_mixed_work():
    report = named_api.memory_usage(_family(), scope="self")

    assert report["version"] == "1"
    assert report["kind"] == "memory_usage"
    assert report["status"] == "complete"
    assert report["counts"] == {
        "tool_calls": 3,
        "distinct_files": 2,
        "responses": 2,
        "tool_calls_by_activity": {"read": 1, "write": 2, "read_write": 0},
        "file_operations": {"read": 1, "create": 1, "edit": 1, "delete": 0, "unknown": 0},
    }
    assert report["usage"]["associated"] == {
        "input_tokens": 11,
        "output_tokens": 22,
        "cache_read_tokens": 33,
        "cache_write_tokens": 44,
        "reasoning_tokens": 6,
        "reasoning_included_in_output_tokens": True,
    }
    assert report["usage"]["memory_only"]["input_tokens"] == 10
    assert report["usage"]["mixed"]["input_tokens"] == 1
    assert [row["key"] for row in report["breakdowns"]["by_model"]] == [
        {"model": "opus"}, {"model": "sonnet"},
    ]
    assert [row["key"] for row in report["breakdowns"]["by_activity"]] == [
        {"activity": "read_write"}, {"activity": "write"},
    ]
    assert report["files"][0]["path"].endswith("memory/notes.md")
    assert report["files"][0]["operations"] == {
        "read": 1, "create": 0, "edit": 1, "delete": 0, "unknown": 0,
    }
    assert report["files"][0]["edit_line_totals"] == {
        "status": "available", "net_delta": 2,
    }
    for rows in report["breakdowns"].values():
        for token_field in (
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
            "reasoning_tokens",
        ):
            assert sum(row["usage"][token_field] for row in rows) == report["usage"]["associated"][token_field]


def test_memory_usage_uses_ref_only_talium_query(monkeypatch):
    calls = []
    real_query = named_api.query

    def recording_query(family, spec):
        calls.append(spec)
        return real_query(family, spec)

    monkeypatch.setattr(named_api, "query", recording_query)
    named_api.memory_usage(_family())
    assert calls == [{
        "version": "1",
        "scope": {"kind": "tree"},
        "select": {"type": "Tool", "where": "Tool.memory_access == true"},
        "return": {"kind": "nodes", "verbosity": "ref"},
    }]


@pytest.mark.parametrize(
    ("hunks", "expected"),
    [
        ([{"start": 1, "len": 2, "old_len": 2}, {"start": 2, "len": 1, "old_len": 1}],
         {"status": "unavailable", "net_delta": None, "reason": "overlapping_hunks"}),
        ([{"start": "one", "len": 2, "old_len": 1}],
         {"status": "unavailable", "net_delta": None, "reason": "invalid_hunks"}),
        ([], {"status": "not_reported", "net_delta": None, "reason": "no_edit_hunks"}),
    ],
)
def test_memory_usage_reports_unreliable_edit_line_totals(hunks, expected):
    family = _family()
    family.master.nodes["edit"].attrs["files"][0]["hunks"] = hunks
    report = named_api.memory_usage(family, scope="self")
    assert report["files"][0]["edit_line_totals"] == expected


@pytest.mark.parametrize(
    ("scope", "tool_calls", "input_tokens"),
    [("self", 3, 11), ("subagents", 1, 100), ("tree", 4, 111),
     ({"kind": "session", "session_id": "child"}, 1, 100)],
)
def test_memory_usage_supports_every_common_scope(scope, tool_calls, input_tokens):
    report = named_api.memory_usage(_family(), scope=scope)
    assert report["counts"]["tool_calls"] == tool_calls
    assert report["usage"]["associated"]["input_tokens"] == input_tokens


def test_memory_usage_reports_empty_result_and_invalid_scope():
    graph = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    report = named_api.memory_usage(Family(master=graph))
    assert report["counts"]["tool_calls"] == 0
    assert report["files"] == []
    with pytest.raises(named_api.NamedApiError) as exc:
        named_api.memory_usage(_family(), scope="wrong")
    assert exc.value.code == "invalid_scope"


def test_memory_usage_marks_missing_parent_partial_without_losing_file_counts():
    family = _family()
    family.master.add(Node(id="orphan", type=NodeType.TOOL, attrs={
        "memory_access": True,
        "files": [_memory_file("/home/.claude/projects/-repo/memory/orphan.md", "edit")],
    }))

    report = named_api.memory_usage(family, scope="self")
    assert report["status"] == "partial"
    assert report["counts"]["tool_calls"] == 4
    assert report["counts"]["responses"] == 2
    assert report["warnings"][-1]["code"] == "memory_usage_parent_missing"


def test_memory_usage_does_not_mutate_or_attach_cache_to_family():
    family = _family()
    before = [graph.to_dict() for graph in family.all_graphs()]
    named_api.memory_usage(family)
    assert [graph.to_dict() for graph in family.all_graphs()] == before
    assert not hasattr(family, "_causal_graph_query_context")
