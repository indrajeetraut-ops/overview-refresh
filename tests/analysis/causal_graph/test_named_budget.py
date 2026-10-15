"""Public budget() wrapper contract and its memory-only cache."""
import pytest

import analysis.causal_graph.named_api as named_api
from analysis.causal_graph.models import Family, Graph, Node, NodeType


def _assistant(node_id: str, *, model: str, usage: tuple[int, int, int, int, int], parent_id: str) -> Node:
    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning = usage
    return Node(
        id=node_id,
        type=NodeType.ASSISTANT,
        parent_id=parent_id,
        attrs={
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "estimated_reasoning_tokens": reasoning,
        },
    )


def _turn(node_id: str, number: int, mode: str, children: list[str]) -> Node:
    return Node(
        id=node_id,
        type=NodeType.TURN,
        children=children,
        attrs={"turn_num": number, "permission_mode": mode},
    )


def _family() -> Family:
    master = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    master.add(_turn("turn:1", 1, "default", ["m1"]))
    master.add(_assistant("m1", model="model-a", usage=(10, 5, 2, 1, 3), parent_id="turn:1"))
    master.add(_turn("turn:2", 2, "plan", ["m2"]))
    master.add(_assistant("m2", model="model-b", usage=(20, 8, 4, 0, 2), parent_id="turn:2"))
    master.roots.extend(["turn:1", "turn:2"])

    child = Graph(
        session_id="child", vendor="claude_code", source="jsonl", built="complete",
        parent_session_id="master",
    )
    child.add(_turn("turn:1", 1, "default", ["c1"]))
    child.add(_assistant("c1", model="model-a", usage=(7, 4, 1, 0, 1), parent_id="turn:1"))
    child.roots.append("turn:1")
    return Family(master=master, children={"child": child})


def _usage(input_tokens: int, output_tokens: int, cache_read: int, cache_write: int, reasoning: int) -> dict:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "reasoning_tokens": reasoning,
        "reasoning_included_in_output_tokens": True,
    }


def test_budget_groups_model_usage_and_never_double_counts_reasoning():
    result = named_api.budget(_family(), by="model")

    assert result["kind"] == "budget"
    assert result["scope"] == {"kind": "tree"}
    assert result["rows"] == [
        {"key": {"model": "model-a"}, "usage": _usage(17, 9, 3, 1, 4)},
        {"key": {"model": "model-b"}, "usage": _usage(20, 8, 4, 0, 2)},
    ]


def test_budget_filters_and_groups_by_permission_turn_and_subagent():
    family = _family()

    by_permission = named_api.budget(family, by="permission_mode", model="model-a")
    assert by_permission["rows"] == [
        {"key": {"permission_mode": "default"}, "usage": _usage(17, 9, 3, 1, 4)},
    ]

    by_turn = named_api.budget(
        family,
        by="turn",
        turn={"session_id": "master", "turn_num": 2},
    )
    assert by_turn["rows"] == [
        {"key": {"session_id": "master", "turn_num": 2}, "usage": _usage(20, 8, 4, 0, 2)},
    ]

    by_subagent = named_api.budget(family, by="subagent", scope="subagents")
    assert by_subagent["rows"] == [
        {"key": {"session_id": "child"}, "usage": _usage(7, 4, 1, 0, 1)},
    ]


def test_budget_caches_reports_and_reuses_turn_rollups(monkeypatch):
    family = _family()
    calls = 0
    real_query = named_api.query

    def counted_query(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_query(*args, **kwargs)

    monkeypatch.setattr(named_api, "query", counted_query)
    first = named_api.budget(family, by="model")
    second = named_api.budget(family, by="model")
    named_api.budget(family, by="turn")

    assert calls == 2
    assert first == second
    context = family._causal_graph_query_context
    assert len(context.budget_turn_rollups) == 3
    first["rows"][0]["usage"]["input_tokens"] = -1
    assert named_api.budget(family, by="model")["rows"][0]["usage"]["input_tokens"] == 17


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"by": "tool"}, "invalid_budget_axis"),
        ({"scope": "not-a-scope"}, "invalid_scope"),
        ({"model": ""}, "invalid_filter"),
        ({"turn": {"session_id": "master"}}, "invalid_filter"),
    ],
)
def test_budget_rejects_invalid_public_inputs(kwargs, code):
    with pytest.raises(named_api.NamedApiError) as exc:
        named_api.budget(_family(), **kwargs)
    assert exc.value.code == code
    assert exc.value.to_dict()["api"] == "budget"
