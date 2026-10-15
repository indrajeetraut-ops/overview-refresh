"""Shared fixtures — tiny local trace files so every test runs offline.

The jsonl fixture mirrors the real Claude Code transcript shape (a snapshot event with
no sessionId, a user turn, and assistant turns carrying nested message.model / usage /
content blocks). The parquet fixture mirrors the flat one-row-per-exchange shape.
"""
from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from trace_utils.engine import Engine
from trace_utils.sources import ResolvedSource

# session id equals the filename stem, as in one-file-per-session datasets
SESSION_ID = "sess-1"

_EVENTS = [
    {"type": "file-history-snapshot", "messageId": "m0", "snapshot": {"timestamp": "2026-01-01T00:00:00Z"}},
    {"type": "user", "sessionId": SESSION_ID, "uuid": "u1", "timestamp": "2026-01-01T00:00:00Z",
     "permissionMode": "default", "gitBranch": "main",
     "message": {"role": "user", "content": "make a sound when idle"}},
    {"type": "assistant", "sessionId": SESSION_ID, "uuid": "a1", "timestamp": "2026-01-01T00:00:05Z",
     "message": {"role": "assistant", "model": "claude-opus-4-6",
                 "usage": {"output_tokens": 42, "input_tokens": 100},
                 "content": [{"type": "text", "text": "okay"}]}},
    {"type": "assistant", "sessionId": SESSION_ID, "uuid": "a2", "timestamp": "2026-01-01T00:00:06Z",
     "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                 "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "echo hi"}}]}},
]


@pytest.fixture
def engine():
    e = Engine(token=None)
    yield e
    e.close()


@pytest.fixture
def jsonl_source(tmp_path) -> ResolvedSource:
    path = tmp_path / f"{SESSION_ID}.jsonl"
    path.write_text("\n".join(json.dumps(ev) for ev in _EVENTS) + "\n")
    return ResolvedSource(
        repo_id="local/test", fmt="jsonl", uris=[str(path)],
        files=[path.name], local=True,
    )


@pytest.fixture
def parquet_source(tmp_path) -> ResolvedSource:
    path = tmp_path / "train.parquet"
    table = pa.table({
        "id": ["s1", "s2"],
        "model": ["claude-opus-4-6", "claude-sonnet-4-6"],
        "user_prompt": ["make a sound when idle", "refactor the parser"],
        "assistant_response": ["okay", "done"],
        "timestamp": ["2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"],
    })
    pq.write_table(table, path)
    return ResolvedSource(
        repo_id="local/test-pq", fmt="parquet", uris=[str(path)],
        files=[path.name], local=True,
    )
