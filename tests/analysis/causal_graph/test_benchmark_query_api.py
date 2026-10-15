"""Structural checks for the repeatable benchmark; no wall-clock assertion belongs in CI."""
from .benchmark_query_api import run_benchmark


def test_benchmark_reports_required_structural_limits_and_warm_budget_behavior():
    report = run_benchmark(turns=4, repeats=3)

    assert set(report["measurements"]) == {
        "select_only", "four_predicate_filter", "regex_command", "contains_walk",
        "spawned_walk", "group_median", "full_output",
    }
    assert report["budget"]["warm_query_calls"] == 0
    assert report["structural"]["full_result_within_cap"] is True
    assert report["structural"]["regex_inspected_bytes"] > 0
    assert report["bash_extraction"]["tools_per_family"] == 4
