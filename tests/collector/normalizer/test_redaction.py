import json

from collector.normalizer.claude_code import ClaudeCodeNormalizer
from collector.normalizer.config import NormalizerConfig
from fixtures.normalizer import sessions

_SECRET_PATTERNS = [r"sk-[A-Za-z0-9]+"]
_OMITTED = "[CONTENT OMITTED]"


def test_redaction_scrubs_extracted_field_and_raw_passthrough(normalize_one):
    normalizer = ClaudeCodeNormalizer(NormalizerConfig(redaction_patterns=_SECRET_PATTERNS))
    src = sessions.simple_session()
    src.records[0]["message"]["content"] = "my key is sk-abc123, don't leak it"

    session = normalize_one(normalizer, src)
    user_event = next(e for e in session.events if e.event_type == "user_message")

    assert "sk-abc123" not in user_event.payload["text"]
    assert "[REDACTED]" in user_event.payload["text"]
    assert "sk-abc123" not in str(user_event.payload["raw"])


def test_default_config_redacts_nothing(normalize_one):
    normalizer = ClaudeCodeNormalizer()
    src = sessions.simple_session()
    src.records[0]["message"]["content"] = "my key is sk-abc123"

    session = normalize_one(normalizer, src)
    user_event = next(e for e in session.events if e.event_type == "user_message")

    assert user_event.payload["text"] == "my key is sk-abc123"


def test_redaction_applies_to_tool_input(normalize_one):
    normalizer = ClaudeCodeNormalizer(NormalizerConfig(redaction_patterns=_SECRET_PATTERNS))
    src = sessions.tool_pairing_session()
    src.records[1]["message"]["content"][1]["input"]["command"] = "curl -H 'Authorization: sk-abc123'"

    session = normalize_one(normalizer, src)
    bash_call = next(e for e in session.events if e.event_type == "tool_call" and e.payload["tool_name"] == "Bash")

    assert "sk-abc123" not in bash_call.payload["input"]["command"]
    assert "[REDACTED]" in bash_call.payload["input"]["command"]


def test_write_content_scrubbed_from_input_and_raw(normalizer, normalize_one):
    """Unconditional: no redaction_patterns configured, no secret in the content -- still
    must not ship, because it's generated content written to disk (issue #23)."""
    src = sessions.simple_session()
    src.records[1]["message"]["content"].append(
        {
            "type": "tool_use",
            "id": "toolu_write1",
            "name": "Write",
            "input": {"file_path": "src/app.py", "content": "SECRET_KEY = 'super-secret-value'\n"},
        }
    )

    session = normalize_one(normalizer, src)
    write_call = next(e for e in session.events if e.event_type == "tool_call" and e.payload["tool_name"] == "Write")

    assert write_call.payload["input"]["content"] == _OMITTED
    assert "super-secret-value" not in json.dumps(write_call.payload["raw"])
    # input_chars still reflects the true generated size, not the placeholder's
    assert write_call.payload["input_chars"] > len(_OMITTED)


def test_edit_content_scrubbed_from_input_and_raw(normalizer, normalize_one):
    src = sessions.tool_pairing_session()

    session = normalize_one(normalizer, src)
    edit_call = next(e for e in session.events if e.event_type == "tool_call" and e.payload["tool_name"] == "Edit")
    file_change = next(e for e in session.events if e.event_type == "file_change")
    edit_assistant = next(e for e in session.events if e.event_type == "assistant_message" and e.record_id == "evt-4")

    assert edit_call.payload["input"]["old_string"] == _OMITTED
    assert edit_call.payload["input"]["new_string"] == _OMITTED
    assert "return 2" not in json.dumps(edit_call.payload["raw"])
    # file_change and the originating assistant_message share the same raw record -- both
    # must be scrubbed too, not just the tool_call that happens to get tested first.
    assert "return 2" not in json.dumps(file_change.payload["raw"])
    assert "return 2" not in json.dumps(edit_assistant.payload["raw"])


def test_multi_edit_content_scrubbed(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[1]["message"]["content"].append(
        {
            "type": "tool_use",
            "id": "toolu_multi1",
            "name": "MultiEdit",
            "input": {
                "file_path": "src/app.py",
                "edits": [
                    {"old_string": "return 1", "new_string": "return 2"},
                    {"old_string": "return 3", "new_string": "return 4"},
                ],
            },
        }
    )

    session = normalize_one(normalizer, src)
    multi_call = next(
        e for e in session.events if e.event_type == "tool_call" and e.payload["tool_name"] == "MultiEdit"
    )

    for edit in multi_call.payload["input"]["edits"]:
        assert edit["old_string"] == _OMITTED
        assert edit["new_string"] == _OMITTED
    assert "return 2" not in json.dumps(multi_call.payload["raw"])


def test_bash_input_unaffected_by_generated_content_scrub(normalizer, normalize_one):
    """The scrub is scoped to Write/Edit/MultiEdit only -- it must never touch other tools."""
    src = sessions.tool_pairing_session()
    session = normalize_one(normalizer, src)
    bash_call = next(e for e in session.events if e.event_type == "tool_call" and e.payload["tool_name"] == "Bash")
    assert bash_call.payload["input"]["command"] == "pytest -q"


def test_redact_generated_file_content_can_be_disabled(normalize_one):
    normalizer = ClaudeCodeNormalizer(NormalizerConfig(redact_generated_file_content=False))
    src = sessions.tool_pairing_session()

    session = normalize_one(normalizer, src)
    edit_call = next(e for e in session.events if e.event_type == "tool_call" and e.payload["tool_name"] == "Edit")

    assert edit_call.payload["input"]["new_string"] == "return 2"
    assert "return 2" in json.dumps(edit_call.payload["raw"])
