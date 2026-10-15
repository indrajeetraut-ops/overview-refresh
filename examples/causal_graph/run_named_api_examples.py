"""Run every approved named API against the committed sample graph."""
from __future__ import annotations

import json
from pathlib import Path

from analysis.causal_graph import GraphContext, budget, gitops, goods, list_tools, memory_usage, subagents, tool_usage, turns
from consumer_flow import configure_example_logging
from file_consumer import file_source


_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE = _ROOT / "examples/causal_graph/graphs/query_api_sample.json"


def main() -> int:
    configure_example_logging()
    master, resolver = file_source(_SAMPLE)
    context = GraphContext(); context.load_self(master); context.load_tree(resolver)
    family = context.family
    reports = {"budget": budget(family), "goods": goods(family),
               "memory_usage": memory_usage(family), "list_tools": list_tools(family),
               "tool_usage": tool_usage(family, outcome="failure"),
               "turns": turns(family, turn_id={"session_id": "query-api-sample", "turn_num": 1}),
               "subagents": subagents(family), "gitops": gitops(family)}
    context.unload_tree()
    print(json.dumps(reports, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
