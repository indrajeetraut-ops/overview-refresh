"""Run the approved standalone QuerySpec examples against the committed sample graph."""
from __future__ import annotations

import json
from pathlib import Path

from analysis.causal_graph import GraphContext
from analysis.causal_graph.query import query
from consumer_flow import configure_example_logging
from file_consumer import file_source


_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE = _ROOT / "examples/causal_graph/graphs/query_api_sample.json"
_SPECS = _ROOT / "examples/causal_graph/query_specs"


def main() -> int:
    configure_example_logging()
    master, resolver = file_source(_SAMPLE)
    context = GraphContext(); context.load_self(master); context.load_tree(resolver)
    family = context.family
    for path in sorted(_SPECS.glob("*.json")):
        result = query(family, json.loads(path.read_text(encoding="utf-8")))
        print(json.dumps({"example": path.name, "result": result.model_dump(mode="json")}, sort_keys=True))
    context.unload_tree()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
