"""Retained lifecycle report and process-local metric contracts."""
from __future__ import annotations

from copy import deepcopy

import pytest

from analysis.causal_graph import (
    GraphContext,
    GraphContextError,
    budget,
    get_load_report,
    get_metric,
)
from analysis.causal_graph.query import query
from .test_context import graph_dict


def _series(snapshot: dict, name: str) -> list[dict]:
    return [item for item in snapshot["metrics"] if item["name"] == name]


def test_reports_retain_timestamped_history_across_unload_and_new_context_reload() -> None:
    master = "master-report-reload"
    first = GraphContext()
    returned = first.load_self(graph_dict(master, children=("missing-report",)))
    assert returned == get_load_report(master)
    first.load_tree(lambda _ids: {})
    first.unload_tree()

    second = GraphContext()
    second.load_self(graph_dict(master))
    report = get_load_report(master)
    assert [item["operation"] for item in report["operations"]] == ["load_self", "load_tree", "unload_tree", "load_self"]
    assert report["created_at"] <= report["last_updated_at"]
    for operation in report["operations"]:
        assert operation["started_at"] <= operation["completed_at"]
        assert operation["duration_ms"] >= 0
        assert operation["outcome"] in {"success", "unchanged", "failure"}
    second.unload_tree()


def test_aggregate_reports_are_isolated_deep_copy_snapshots() -> None:
    one, two = GraphContext(), GraphContext()
    one.load_self(graph_dict("master-report-one"))
    two.load_self(graph_dict("master-report-two"))
    reports = get_load_report()
    assert {"master-report-one", "master-report-two"} <= reports.keys()
    reports["master-report-one"]["operations"].clear()
    assert get_load_report("master-report-one")["operations"]
    one.unload_tree()
    two.unload_tree()


def test_failed_lifecycle_operation_is_reported_and_metered() -> None:
    context = GraphContext()
    context.load_self(graph_dict("master-report-failure", children=("child-report-failure",)))
    with pytest.raises(GraphContextError):
        context.load_tree(lambda _ids: (_ for _ in ()).throw(RuntimeError("resolver unavailable")))
    report = get_load_report("master-report-failure")
    assert report["operations"][-1]["outcome"] == "failure"
    assert report["operations"][-1]["error_code"] == "resolver_failed"
    metrics = get_metric("master-report-failure")
    assert _series(metrics, "causal_graph.failures")
    assert _series(metrics, "causal_graph.resolver.calls")[0]["labels"]["outcome"] == "failure"
    context.unload_tree()


def test_metrics_cover_lifecycle_query_named_api_cache_and_gauges() -> None:
    context = GraphContext()
    context.load_self(graph_dict("master-metrics"))
    spec = {"version": "1", "scope": {"kind": "self"}, "select": {"type": "Turn"}, "return": {"kind": "nodes", "verbosity": "summary"}}
    query(context.family, spec)
    budget(context.family)
    budget(context.family)
    metrics = get_metric("master-metrics")
    for name in (
        "causal_graph.lifecycle.calls", "causal_graph.lifecycle.duration_ms",
        "causal_graph.graphs.loaded", "causal_graph.nodes.loaded",
        "causal_graph.loaded.sessions", "causal_graph.loaded.nodes",
        "causal_graph.query.calls", "causal_graph.named_api.calls", "causal_graph.budget.cache",
    ):
        assert _series(metrics, name), name
    assert {item["labels"]["outcome"] for item in _series(metrics, "causal_graph.budget.cache")} == {"hit", "miss"}
    copied = deepcopy(metrics)
    copied["metrics"].clear()
    assert get_metric("master-metrics")["metrics"]
    context.unload_tree()


def test_metric_getter_requires_one_known_master_argument() -> None:
    with pytest.raises(TypeError):
        get_metric()  # type: ignore[call-arg]
    with pytest.raises(GraphContextError) as raised:
        get_metric("unknown-observability-master")
    assert raised.value.code == "master_session_not_found"


def test_metric_series_are_isolated_by_master_and_record_query_failures() -> None:
    first, second = GraphContext(), GraphContext()
    first.load_self(graph_dict("master-metric-isolation-one"))
    second.load_self(graph_dict("master-metric-isolation-two"))
    with pytest.raises(Exception):
        query(first.family, {"version": "wrong"})
    first_calls = _series(get_metric("master-metric-isolation-one"), "causal_graph.query.calls")
    second_calls = _series(get_metric("master-metric-isolation-two"), "causal_graph.query.calls")
    assert first_calls[0]["labels"]["outcome"] == "failure"
    assert second_calls == []
    first.unload_tree()
    second.unload_tree()
