"""GraphContext lifecycle, batching, ownership, and universal identity."""
from __future__ import annotations

import pytest

from analysis.causal_graph import GraphContext, GraphContextError, get_graph_context
from analysis.causal_graph.models import Graph, Node, NodeType


def graph_dict(session_id: str, parent: str | None = None, children: tuple[str, ...] = ()) -> dict:
    graph = Graph(session_id=session_id, parent_session_id=parent, vendor="claude_code", source="jsonl", built="complete")
    root = Node(id="turn:1", type=NodeType.TURN, attrs={"turn_num": 1})
    graph.add(root)
    graph.roots.append(root.id)
    for index, child in enumerate(children):
        spawn = Node(id=f"spawn:{index}", type=NodeType.SPAWN, refs={"child_session_id": child})
        graph.add(spawn)
        root.children.append(spawn.id)
        spawn.parent_id = root.id
    return graph.to_dict()


def test_load_self_does_not_load_referenced_children_and_is_idempotent() -> None:
    context = GraphContext()
    first = context.load_self(graph_dict("master-self", children=("child-self",)))
    second = context.load_self(graph_dict("master-self", children=("child-self",)))
    assert first["loaded_session_ids"] == ["master-self"]
    assert first["unresolved_session_ids"] == ["child-self"]
    assert second["operations"][-1]["outcome"] == "unchanged"
    assert get_graph_context("master-self") is context
    assert get_graph_context("child-self") is None
    context.unload_tree()


def test_load_tree_batches_breadth_first_and_resolves_every_session_id() -> None:
    context = GraphContext()
    context.load_self(graph_dict("master-bfs", children=("a-bfs", "b-bfs")))
    documents = {
        "a-bfs": graph_dict("a-bfs", "master-bfs", ("grand-bfs",)),
        "b-bfs": graph_dict("b-bfs", "master-bfs"),
        "grand-bfs": graph_dict("grand-bfs", "a-bfs"),
    }
    calls: list[list[str]] = []

    def resolve(ids: list[str]) -> dict[str, dict]:
        calls.append(ids)
        return {sid: documents[sid] for sid in ids}

    report = context.load_tree(resolve)
    assert calls == [["a-bfs", "b-bfs"], ["grand-bfs"]]
    assert report["state"] == "complete"
    assert context.resolve("grand-bfs").parent_session_id == "a-bfs"  # type: ignore[union-attr]
    assert get_graph_context("grand-bfs") is context
    context.load_tree(lambda _ids: pytest.fail("loaded sessions must not be resolved again"))
    context.unload_tree()


def test_load_tree_passes_wide_level_as_one_list_batch() -> None:
    children = tuple(f"wide-{index}" for index in range(63))
    context = GraphContext()
    context.load_self(graph_dict("master-wide", children=children))
    calls: list[list[str]] = []

    def resolve(ids: list[str]) -> dict[str, dict]:
        calls.append(ids)
        return {sid: graph_dict(sid, "master-wide") for sid in ids}

    context.load_tree(resolve)
    assert calls == [sorted(children)]
    context.unload_tree()


def test_missing_resolver_entry_is_partial() -> None:
    context = GraphContext()
    context.load_self(graph_dict("master-partial", children=("found-partial", "missing-partial")))
    report = context.load_tree(lambda _ids: {"found-partial": graph_dict("found-partial", "master-partial")})
    assert report["state"] == "partial"
    assert report["unresolved_session_ids"] == ["missing-partial"]
    context.unload_tree()


@pytest.mark.parametrize(
    ("resolver", "code"),
    [
        (lambda _ids: (_ for _ in ()).throw(RuntimeError("down")), "resolver_failed"),
        (lambda _ids: {"wrong": graph_dict("wrong", "master-bad")}, "resolver_result_invalid"),
        (lambda _ids: {"child-bad": graph_dict("different", "master-bad")}, "resolver_result_invalid"),
        (lambda _ids: {"child-bad": graph_dict("child-bad", "unrelated")}, "resolver_result_invalid"),
    ],
)
def test_resolver_failures_are_stable_and_do_not_register(resolver, code: str) -> None:
    context = GraphContext()
    context.load_self(graph_dict("master-bad", children=("child-bad",)))
    with pytest.raises(GraphContextError) as raised:
        context.load_tree(resolver)
    assert raised.value.code == code
    assert context.resolve("child-bad") is None
    assert get_graph_context("child-bad") is None
    context.unload_tree()


def test_batch_validation_is_atomic() -> None:
    context = GraphContext()
    context.load_self(graph_dict("master-atomic", children=("good-atomic", "bad-atomic")))
    with pytest.raises(GraphContextError) as raised:
        context.load_tree(lambda _ids: {
            "good-atomic": graph_dict("good-atomic", "master-atomic"),
            "bad-atomic": {"session_id": "bad-atomic"},
        })
    assert raised.value.code == "graph_validation_failed"
    assert context.resolve("good-atomic") is None
    context.unload_tree()


def test_active_ownership_conflict_and_multiple_contexts() -> None:
    first, second, third = GraphContext(), GraphContext(), GraphContext()
    first.load_self(graph_dict("master-owner"))
    second.load_self(graph_dict("master-other"))
    assert get_graph_context("master-owner") is first
    assert get_graph_context("master-other") is second
    with pytest.raises(GraphContextError) as raised:
        third.load_self(graph_dict("master-owner"))
    assert raised.value.code == "session_ownership_conflict"
    first.unload_tree()
    second.unload_tree()


def test_unload_descendant_subtree_and_whole_tree_release_registry() -> None:
    context = GraphContext()
    context.load_self(graph_dict("master-unload", children=("middle-unload", "sibling-unload")))
    docs = {
        "middle-unload": graph_dict("middle-unload", "master-unload", ("leaf-unload",)),
        "sibling-unload": graph_dict("sibling-unload", "master-unload"),
        "leaf-unload": graph_dict("leaf-unload", "middle-unload"),
    }
    context.load_tree(lambda ids: {sid: docs[sid] for sid in ids})
    report = context.unload("middle-unload")
    assert report["operations"][-1]["affected_session_ids"] == ["leaf-unload", "middle-unload"]
    assert context.resolve("sibling-unload") is not None
    assert get_graph_context("leaf-unload") is None
    with pytest.raises(GraphContextError) as raised:
        context.unload("master-unload")
    assert raised.value.code == "master_unload_forbidden"
    context.unload_tree()
    assert get_graph_context("master-unload") is None
    with pytest.raises(GraphContextError) as raised:
        _ = context.family
    assert raised.value.code == "context_not_ready"


def test_cycle_and_descendant_ownership_conflicts_are_rejected() -> None:
    owner = GraphContext()
    owner.load_self(graph_dict("occupied-child"))
    context = GraphContext()
    context.load_self(graph_dict("master-cycle", children=("occupied-child", "cycle-child")))
    with pytest.raises(GraphContextError) as raised:
        context.load_tree(lambda ids: {
            sid: graph_dict(sid, "master-cycle", ("master-cycle",) if sid == "cycle-child" else ())
            for sid in ids
        })
    assert raised.value.code in {"resolver_result_invalid", "session_ownership_conflict"}
    assert context.resolve("cycle-child") is None
    context.unload_tree()
    owner.unload_tree()


def test_descendant_ownership_conflict_is_atomic() -> None:
    owner = GraphContext()
    owner.load_self(graph_dict("owned-descendant"))
    context = GraphContext()
    context.load_self(graph_dict("master-descendant-owner", children=("owned-descendant",)))
    with pytest.raises(GraphContextError) as raised:
        context.load_tree(lambda _ids: {"owned-descendant": graph_dict("owned-descendant", "master-descendant-owner")})
    assert raised.value.code == "session_ownership_conflict"
    assert context.resolve("owned-descendant") is None
    context.unload_tree()
    owner.unload_tree()


def test_same_master_can_reload_into_a_new_context_after_unload() -> None:
    first = GraphContext()
    first.load_self(graph_dict("master-reload"))
    first.unload_tree()
    second = GraphContext()
    second.load_self(graph_dict("master-reload"))
    assert get_graph_context("master-reload") is second
    second.unload_tree()


def test_subtree_unload_invalidates_family_budget_cache() -> None:
    from analysis.causal_graph import budget

    context = GraphContext()
    context.load_self(graph_dict("master-cache", children=("child-cache",)))
    context.load_tree(lambda _ids: {"child-cache": graph_dict("child-cache", "master-cache")})
    budget(context.family)
    assert hasattr(context.family, "_causal_graph_query_context")
    context.unload("child-cache")
    assert not hasattr(context.family, "_causal_graph_query_context")
    context.unload_tree()


def test_loader_accepts_older_schema_in_current_major() -> None:
    document = graph_dict("master-schema-backward")
    document["schema_version"] = "4.1.0"
    context = GraphContext()
    report = context.load_self(document)
    assert report["loaded_session_ids"] == ["master-schema-backward"]
    assert context.resolve("master-schema-backward").schema_version == "4.1.0"  # type: ignore[union-attr]
    context.unload_tree()


@pytest.mark.parametrize("version", ["3.9.0", "4.4.0", "5.0.0", "4.2", "invalid", None])
def test_loader_rejects_old_major_future_and_malformed_schema_versions(version) -> None:
    document = graph_dict(f"master-schema-{version}")
    document["schema_version"] = version
    context = GraphContext()
    with pytest.raises(GraphContextError) as raised:
        context.load_self(document)
    assert raised.value.code == "graph_validation_failed"
    assert raised.value.details == {
            "supported_major": 4, "maximum": "4.3.0", "actual": version,
    }
