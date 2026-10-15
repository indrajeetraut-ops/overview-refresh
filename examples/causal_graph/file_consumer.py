"""CLI-layout example: only graph retrieval differs from the in-memory consumer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from consumer_flow import configure_example_logging, run_consumer


def file_source(master_path: Path):
    """Return one master dictionary and its CLI-layout resolver."""
    master_graph = json.loads(master_path.read_text(encoding="utf-8"))
    children_dir = master_path.parent / master_graph["session_id"] / "subagents"

    def resolve_graphs(session_ids: list[str]) -> dict[str, dict]:
        resolved: dict[str, dict] = {}
        for session_id in session_ids:
            path = children_dir / f"{session_id}.json"
            if path.is_file():
                resolved[session_id] = json.loads(path.read_text(encoding="utf-8"))
        return resolved

    return master_graph, resolve_graphs


def main() -> int:
    configure_example_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("master", type=Path, help="persisted master graph JSON")
    args = parser.parse_args()
    master_graph, resolve_graphs = file_source(args.master)
    print(json.dumps(run_consumer(master_graph, resolve_graphs), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
