"""Build one graph dictionary while keeping retrieval and storage outside the library."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from analysis.causal_graph import build_graph


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("normalized", type=Path, help="JSON file containing one normalized sessions[] member")
    parser.add_argument("destination", type=Path, help="caller-owned destination for the graph dictionary")
    args = parser.parse_args()

    normalized_session = json.loads(args.normalized.read_text(encoding="utf-8"))
    graph_dict = build_graph(normalized_session)
    assert isinstance(graph_dict, dict)
    args.destination.write_text(json.dumps(graph_dict, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"session_id": graph_dict["session_id"], "stored_at": str(args.destination)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
