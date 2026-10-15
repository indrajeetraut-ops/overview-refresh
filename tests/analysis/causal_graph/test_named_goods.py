"""Public goods() wrapper contract."""
import pytest

import analysis.causal_graph.named_api as named_api
from analysis.causal_graph.models import Family, Graph, Node, NodeType


def _family() -> Family:
    graph = Graph(session_id="master", vendor="claude_code", source="jsonl", built="complete")
    graph.add(Node(id="write-one", type=NodeType.TOOL, attrs={"files": [
        {
            "path": "/repo/src/app.py", "extension": ".py", "artifact_class": "code",
            "operation": "edit", "hunks": [{"start": 1, "len": 5, "old_len": 3}],
        },
        {
            "path": "/repo/README.md", "extension": ".md", "artifact_class": "documentation",
            "operation": "create", "hunks": [], "is_memory": True, "memory_kind": "index",
        },
    ]}))
    graph.add(Node(id="write-two", type=NodeType.TOOL, attrs={"files": [
        {
            "path": "/repo/src/app.py", "extension": ".py", "artifact_class": "code",
            "operation": "edit", "hunks": [{"start": 10, "len": 2, "old_len": 4}],
        },
        {
            "path": "/repo/config/settings.yaml", "extension": ".yaml", "artifact_class": "config",
            "operation": "edit", "hunks": [
                {"start": 3, "len": 4, "old_len": 4},
                {"start": 5, "len": 2, "old_len": 2},
            ],
        },
    ]}))
    graph.add(Node(id="remote", type=NodeType.TOOL, attrs={"files": [
        {
            "path": None, "remote_id": "document-id", "operation": "read",
            "extension": None, "artifact_class": "document", "hunks": [],
        },
        {
            "path": "/repo/src/ignored.py", "extension": ".py", "artifact_class": "code",
            "operation": "read", "hunks": [],
        },
    ]}))
    return Family(master=graph)


def test_goods_deduplicates_paths_but_preserves_each_change_and_line_calculation():
    result = named_api.goods(_family())

    assert result["status"] == "partial"
    assert [artifact["path"] for artifact in result["artifacts"]] == [
        "/repo/src/app.py", "/repo/config/settings.yaml",
    ]
    app = result["artifacts"][0]
    assert app["artifact_class"] == "code"
    assert app["changes"] == [
        {
            "operation": "edit",
            "source": {"session_id": "master", "node_id": "write-one"},
            "hunk_count": 1,
            "line_totals": {"status": "available", "net_delta": 2},
        },
        {
            "operation": "edit",
            "source": {"session_id": "master", "node_id": "write-two"},
            "hunk_count": 1,
            "line_totals": {"status": "available", "net_delta": -2},
        },
    ]
    assert result["artifacts"][1]["changes"][0]["line_totals"] == {
        "status": "unavailable", "net_delta": None, "reason": "overlapping_hunks",
    }
    assert result["warnings"][0]["code"] == "remote_artifact_excluded"


def test_goods_filters_by_artifact_class_or_raw_extension():
    assert [artifact["path"] for artifact in named_api.goods(_family(), artifact_class="code")["artifacts"]] == [
        "/repo/src/app.py",
    ]
    assert named_api.goods(_family(), extension=".md")["artifacts"] == []


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"artifact_class": "binary"}, "invalid_goods_filter"),
        ({"extension": "py"}, "invalid_goods_filter"),
        ({"scope": "wrong"}, "invalid_scope"),
    ],
)
def test_goods_rejects_invalid_public_filters(kwargs, code):
    with pytest.raises(named_api.NamedApiError) as exc:
        named_api.goods(_family(), **kwargs)
    assert exc.value.code == code
