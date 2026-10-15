"""Repeatable, non-CI-latency benchmark for the causal-graph QuerySpec APIs.

Run from the repository root:
    PYTHONPATH=src venv/bin/python tests/analysis/causal_graph/benchmark_query_api.py
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import tracemalloc
from typing import Any, Callable

import analysis.causal_graph.named_api as named_api
from analysis.causal_graph.command_extractors import extract_commands
from analysis.causal_graph.models import Family, Graph, Node, NodeType
from analysis.causal_graph.query import query
from analysis.causal_graph.query_config import load_query_config


def benchmark_family(turns: int) -> Family:
    """Build a deterministic ~2,000-node family without reading a private corpus."""
    master = Graph(session_id="bench-master", vendor="claude_code", source="jsonl", built="complete")
    child = Graph(
        session_id="bench-child", vendor="claude_code", source="jsonl", built="complete",
        parent_session_id="bench-master",
    )
    child.add(Node(id="turn:1", type=NodeType.TURN, attrs={"turn_num": 1}, children=["child-assistant"]))
    child.add(Node(
        id="child-assistant", type=NodeType.ASSISTANT, parent_id="turn:1",
        attrs={"model": "bench-model", "input_tokens": 1, "output_tokens": 1,
               "cache_read_tokens": 0, "cache_write_tokens": 0, "estimated_reasoning_tokens": 0},
    ))
    child.roots.append("turn:1")

    for number in range(1, turns + 1):
        turn_id, prompt_id, assistant_id, tool_id = (
            f"turn:{number}", f"prompt:{number}", f"assistant:{number}", f"tool:{number}",
        )
        turn_children = [prompt_id, assistant_id]
        assistant_children = [tool_id]
        if number == 1:
            assistant_children.append("spawn:1")
        master.add(Node(
            id=turn_id, type=NodeType.TURN, children=turn_children,
            attrs={"turn_num": number, "permission_mode": "default", "plan_mode": False},
        ))
        master.add(Node(id=prompt_id, type=NodeType.USER_PROMPT, parent_id=turn_id, attrs={"text": "benchmark"}))
        master.add(Node(
            id=assistant_id, type=NodeType.ASSISTANT, parent_id=turn_id, children=assistant_children,
            attrs={"model": "bench-model", "input_tokens": 10, "output_tokens": 5,
                   "cache_read_tokens": 1, "cache_write_tokens": 0, "estimated_reasoning_tokens": 1},
        ))
        master.add(Node(
            id=tool_id, type=NodeType.TOOL, parent_id=assistant_id,
            attrs={"tool_name": "Bash", "cmd": ["git", "sed"],
                   "input": {"command": f"git status --short | sed -n '{number},$p'"},
                   "duration_ms": number % 17, "exit_status": 0, "result": {"outcome": "ok"}},
        ))
        master.roots.append(turn_id)
    master.add(Node(
        id="spawn:1", type=NodeType.SPAWN, parent_id="assistant:1",
        attrs={"agent_type": "benchmark", "status": "completed"},
        refs={"child_session_id": "bench-child"},
    ))
    return Family(master=master, children={child.session_id: child})


def _percentile(samples: list[float], percentile: float) -> float:
    return sorted(samples)[math.ceil(percentile * len(samples)) - 1]


def _measure(action: Callable[[], Any], repeats: int) -> dict[str, Any]:
    samples: list[float] = []
    peak = 0
    result: Any = None
    for _ in range(repeats):
        tracemalloc.start()
        started = time.perf_counter_ns()
        result = action()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
        _, current_peak = tracemalloc.get_traced_memory()
        peak = max(peak, current_peak)
        tracemalloc.stop()
    encoded = json.dumps(result.model_dump(mode="json") if hasattr(result, "model_dump") else result).encode()
    return {
        "p50_ms": statistics.median(samples),
        "p95_ms": _percentile(samples, 0.95),
        "peak_allocation_bytes": peak,
        "result_bytes": len(encoded),
    }


def run_benchmark(*, turns: int = 500, repeats: int = 11) -> dict[str, Any]:
    family = benchmark_family(turns)
    config = load_query_config()  # Verify the benchmark uses the mandatory production configuration.
    cases = {
        "select_only": {"version": "1", "scope": {"kind": "tree"}, "select": {"type": "Tool"}, "return": {"kind": "nodes", "verbosity": "ref"}},
        "four_predicate_filter": {"version": "1", "scope": {"kind": "tree"}, "select": {"type": "Tool", "where": "Tool.name == 'Bash' AND Tool.duration_ms >= 0 AND Tool.exit_status == 0 AND Tool.cmd == 'git'"}, "return": {"kind": "nodes", "verbosity": "ref"}},
        "regex_command": {"version": "1", "scope": {"kind": "tree"}, "select": {"type": "Tool", "where": "Tool.input.command MATCHES '^git[[:space:]]+status'"}, "return": {"kind": "nodes", "verbosity": "ref"}},
        "contains_walk": {"version": "1", "scope": {"kind": "tree"}, "select": {"type": "Turn"}, "walk": [{"relation": "contains", "type": "Tool"}], "return": {"kind": "nodes", "verbosity": "ref"}},
        "spawned_walk": {"version": "1", "scope": {"kind": "self"}, "select": {"type": "Spawn"}, "walk": [{"relation": "spawned", "type": "Assistant"}], "return": {"kind": "nodes", "verbosity": "ref"}},
        "group_median": {"version": "1", "scope": {"kind": "tree"}, "select": {"type": "Tool"}, "group_by": ["Tool.name"], "aggregates": [{"name": "median_duration", "function": "MEDIAN", "field": "Tool.duration_ms"}], "return": {"kind": "groups", "verbosity": "summary"}},
        "full_output": {"version": "1", "scope": {"kind": "tree"}, "select": {"type": "Tool"}, "return": {"kind": "nodes", "verbosity": "full"}},
    }
    measurements = {name: _measure(lambda spec=spec: query(family, spec), repeats) for name, spec in cases.items()}
    bash_config = config.command_extractors["Bash"]
    bash_extraction = _measure(
        lambda: [
            extract_commands(
                bash_config.parser, f"git status --short | sed -n '{number},$p'",
                basename_style=bash_config.basename_style, command_case=bash_config.command_case,
            ).commands
            for number in range(1, turns + 1)
        ],
        repeats,
    )
    bash_extraction["tools_per_family"] = turns
    bash_extraction["p50_us_per_tool"] = (bash_extraction["p50_ms"] * 1_000) / turns
    budget_cold = _measure(lambda: named_api.budget(family), repeats)
    real_query = named_api.query
    warm_query_calls = 0

    def counted_query(*args: Any, **kwargs: Any) -> Any:
        nonlocal warm_query_calls
        warm_query_calls += 1
        return real_query(*args, **kwargs)

    named_api.query = counted_query
    try:
        budget_warm = _measure(lambda: named_api.budget(family), repeats)
    finally:
        named_api.query = real_query
    full_result = query(family, cases["full_output"])
    return {
        "benchmark_version": "1",
        "node_count": sum(len(graph.nodes) for graph in family.all_graphs()),
        "turn_count": turns,
        "repeats": repeats,
        "measurements": measurements,
        "bash_extraction": bash_extraction,
        "budget": {"cold": budget_cold, "warm": budget_warm, "warm_query_calls": warm_query_calls},
        "structural": {
            "result_cap": 10_000,
            "full_result_items": len(full_result.items or []),
            "full_result_within_cap": len(full_result.items or []) <= 10_000,
            "regex_inspected_bytes": sum(
                len(node.attrs["input"]["command"])
                for node in family.master.nodes.values()
                if node.type is NodeType.TOOL
            ),
            "powershell_measurement": "not_run: requires pwsh host runtime",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, default=500)
    parser.add_argument("--repeats", type=int, default=11)
    args = parser.parse_args()
    print(json.dumps(run_benchmark(turns=args.turns, repeats=args.repeats), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
