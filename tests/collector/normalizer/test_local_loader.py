import json
from pathlib import Path

from collector.normalizer.local_loader import load_session_source


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def test_loads_a_simple_session(tmp_path):
    session_path = tmp_path / "session-aaa.jsonl"
    _write_jsonl(
        session_path,
        [
            {
                "type": "user",
                "uuid": "evt-1",
                "version": "2.3.1",
                "cwd": "/repo",
                "gitBranch": "main",
                "message": {"role": "user", "content": "hi"},
            },
            {
                "type": "assistant",
                "uuid": "evt-2",
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-5",
                    "content": [{"type": "text", "text": "hello"}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            },
        ],
    )

    src = load_session_source(session_path)

    assert src.vendor == "claude_code"
    assert src.session_id == "session-aaa"
    assert src.source == "batch"
    assert src.agent_version == "2.3.1"
    assert len(src.records) == 2
    assert src.subagent_streams == {}


def test_loads_subagents_and_nested_grandchild(tmp_path):
    session_path = tmp_path / "session-parent.jsonl"
    _write_jsonl(session_path, [{"type": "user", "uuid": "evt-1", "message": {"role": "user", "content": "go"}}])

    subagents_dir = tmp_path / "session-parent" / "subagents"
    _write_jsonl(
        subagents_dir / "agent-1.jsonl",
        [{"type": "user", "uuid": "sub-evt-1", "message": {"role": "user", "content": "explore"}}],
    )
    (subagents_dir / "agent-1.meta.json").write_text(
        json.dumps({"agentId": "agent-1", "toolUseId": "toolu_1", "agentType": "Explore"}), encoding="utf-8"
    )

    grandchild_dir = subagents_dir / "agent-1" / "subagents"
    _write_jsonl(
        grandchild_dir / "agent-2.jsonl",
        [{"type": "user", "uuid": "gc-evt-1", "message": {"role": "user", "content": "go deeper"}}],
    )
    (grandchild_dir / "agent-2.meta.json").write_text(
        json.dumps({"agentId": "agent-2", "toolUseId": "toolu_2", "agentType": "Explore"}), encoding="utf-8"
    )

    src = load_session_source(session_path)

    assert set(src.subagent_streams) == {"toolu_1"}
    child = src.subagent_streams["toolu_1"]
    assert child.agent_id == "agent-1"
    assert len(child.records()) == 1  # lazy: content is only read on call (models.py)
    assert set(child.subagent_streams) == {"toolu_2"}
    grandchild = child.subagent_streams["toolu_2"]
    assert grandchild.agent_id == "agent-2"


def test_subagent_without_tool_use_id_is_skipped(tmp_path):
    session_path = tmp_path / "session-parent.jsonl"
    _write_jsonl(session_path, [{"type": "user", "uuid": "evt-1", "message": {"role": "user", "content": "go"}}])

    subagents_dir = tmp_path / "session-parent" / "subagents"
    _write_jsonl(
        subagents_dir / "agent-1.jsonl",
        [{"type": "user", "uuid": "sub-evt-1", "message": {"role": "user", "content": "explore"}}],
    )
    (subagents_dir / "agent-1.meta.json").write_text(json.dumps({"agentId": "agent-1"}), encoding="utf-8")

    src = load_session_source(session_path)

    assert src.subagent_streams == {}


def test_matches_the_in_memory_fixture_for_the_same_shape(tmp_path):
    """The loader and the in-memory fixture builder should produce an equivalent
    SessionSource for the same logical session, so normalize() behaves identically
    whether records come from a real file or a test fixture."""
    from fixtures.normalizer import sessions

    fixture_src = sessions.simple_session()
    session_path = tmp_path / f"{fixture_src.session_id}.jsonl"
    _write_jsonl(session_path, fixture_src.records)

    loaded_src = load_session_source(session_path)

    assert loaded_src.records == fixture_src.records
    assert loaded_src.session_id == fixture_src.session_id
