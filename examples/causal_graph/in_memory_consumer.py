"""Worker-style example: graph JSON dictionaries arrived from an in-memory data source."""
from __future__ import annotations

import json

from consumer_flow import configure_example_logging, run_consumer


def _node(node_id: str, node_type: str, *, attrs=None, children=None, parent_id=None, refs=None) -> dict:
    return {
        "id": node_id, "type": node_type, "ts": None, "attrs": attrs or {}, "meta": {},
        "children": children or [], "parent_id": parent_id, "refs": refs or {},
    }


MASTER_GRAPH = {
    "schema_version": "4.3.0", "session_id": "memory-master", "parent_session_id": None,
    "vendor": "claude_code", "source": "jsonl", "built": "complete", "meta": {},
    "nodes": [
        _node("turn:1", "turn", attrs={"turn_num": 1, "permission_mode": "default"}, children=["assistant-1"]),
        _node("assistant-1", "assistant", attrs={"message_id": "memory-master-response", "model": "demo-model", "input_tokens": 10, "output_tokens": 4, "cache_read_tokens": 2, "cache_write_tokens": 0, "estimated_reasoning_tokens": 1}, children=["tool-1", "spawn-1"], parent_id="turn:1"),
        _node("tool-1", "tool", attrs={"tool_name": "Bash", "cmd": ["git"], "input": {"command": "git status"}, "result": {"outcome": "error"}}, parent_id="assistant-1"),
        _node("spawn-1", "spawn", attrs={"agent_type": "Explore", "status": "completed"}, parent_id="assistant-1", refs={"child_session_id": "memory-child"}),
    ],
    "roots": ["turn:1"],
}

CHILD_GRAPH = {
    "schema_version": "4.3.0", "session_id": "memory-child", "parent_session_id": "memory-master",
    "vendor": "claude_code", "source": "jsonl", "built": "complete", "meta": {},
    "nodes": [
        _node("turn:1", "turn", attrs={"turn_num": 1, "permission_mode": "default"}, children=["assistant-1"]),
        _node("assistant-1", "assistant", attrs={"message_id": "memory-child-response", "model": "demo-child-model", "input_tokens": 5, "output_tokens": 2, "cache_read_tokens": 0, "cache_write_tokens": 0, "estimated_reasoning_tokens": 0}, children=["tool-1"], parent_id="turn:1"),
        _node("tool-1", "tool", attrs={"tool_name": "Read", "cmd": [], "input": {"file_path": "/home/.claude/projects/-repo/memory/MEMORY.md"}, "result": {"outcome": "ok"}, "memory_access": True, "files": [{"path": "/home/.claude/projects/-repo/memory/MEMORY.md", "extension": ".md", "artifact_class": "documentation", "operation": "read", "hunks": [], "is_memory": True, "memory_kind": "index"}]}, parent_id="assistant-1"),
    ],
    "roots": ["turn:1"],
}


def main() -> int:
    configure_example_logging()
    database_rows = {"memory-child": CHILD_GRAPH}

    def resolve_graphs(session_ids: list[str]) -> dict[str, dict]:
        return {session_id: database_rows[session_id] for session_id in session_ids if session_id in database_rows}

    print(json.dumps(run_consumer(MASTER_GRAPH, resolve_graphs), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
