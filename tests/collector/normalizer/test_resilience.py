"""Defensive-typing regressions: real Claude Code data doesn't always shape a field the
way most records do (source-adapter.md §7 "never crash on new input" / normalizer.md's
must-ignore stance). These lock in that a field present but wrong-typed degrades to "treat
as absent" rather than crashing the whole normalize() run -- found via a real crash where
`toolUseResult` was a plain string instead of the usual structured object.
"""
from fixtures.normalizer import sessions


def test_string_tool_use_result_does_not_crash(normalizer, normalize_one):
    src = sessions.tool_pairing_session()
    src.records[2]["toolUseResult"] = "just a plain string result, not a dict"

    session = normalize_one(normalizer, src)

    bash_call = next(e for e in session.events if e.event_type == "tool_call" and e.payload["tool_name"] == "Bash")
    assert bash_call.payload["exit_status"] == 1  # falls back to the tool_result block's is_error
    assert bash_call.payload["duration_ms"] is None


def test_non_dict_message_does_not_crash(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[0]["message"] = "not a dict at all"

    session = normalize_one(normalizer, src)

    assert session.events[1].event_type == "unknown"  # falls through to passthrough, not a crash


def test_non_dict_usage_does_not_crash(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[1]["message"]["usage"] = "not a dict"

    session = normalize_one(normalizer, src)

    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["input_tokens"] is None
    assert "cache_write_detail" not in assistant.payload
    assert "server_tool_use" not in assistant.payload


def test_non_dict_cache_creation_and_server_tool_use_do_not_crash(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[1]["message"]["usage"]["cache_creation"] = "oops a string"
    src.records[1]["message"]["usage"]["server_tool_use"] = ["oops", "a", "list"]

    session = normalize_one(normalizer, src)

    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert "cache_write_detail" not in assistant.payload
    assert "server_tool_use" not in assistant.payload


def test_non_list_structured_patch_does_not_crash(normalizer, normalize_one):
    src = sessions.tool_pairing_session()
    src.records[4]["toolUseResult"]["structuredPatch"] = "not a list"

    session = normalize_one(normalizer, src)

    edit_call = next(e for e in session.events if e.event_type == "tool_call" and e.payload["tool_name"] == "Edit")
    assert "hunks" not in edit_call.payload["files"][0]


def test_list_structured_patch_with_non_dict_entries_does_not_crash(normalizer, normalize_one):
    src = sessions.tool_pairing_session()
    src.records[4]["toolUseResult"]["structuredPatch"] = ["not a dict", {"newStart": 1, "newLines": 2}]

    session = normalize_one(normalizer, src)

    edit_call = next(e for e in session.events if e.event_type == "tool_call" and e.payload["tool_name"] == "Edit")
    assert edit_call.payload["files"][0]["hunks"] == [
        {"start": 1, "len": 2, "old_len": None}  # bad entry skipped; absent oldLines -> None
    ]


def test_non_dict_compact_metadata_does_not_crash(normalizer, normalize_one):
    src = sessions.api_error_and_compaction_session()
    src.records[2]["compactMetadata"] = "not a dict"

    session = normalize_one(normalizer, src)

    compaction = next(e for e in session.events if e.event_type == "compaction")
    assert compaction.payload["tokens_before"] is None
    assert compaction.payload["tokens_reclaimed"] is None


def test_non_int_api_error_status_does_not_crash(normalizer, normalize_one):
    src = sessions.api_error_and_compaction_session()
    src.records[1]["status"] = "529"  # a string, not an int

    session = normalize_one(normalizer, src)

    error = next(e for e in session.events if e.event_type == "api_error")
    assert error.payload["error_class"] == "other"


def test_non_dict_tool_use_input_does_not_crash(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[1]["message"]["content"].append(
        {"type": "tool_use", "id": "toolu_x", "name": "Bash", "input": "not a dict"}
    )

    session = normalize_one(normalizer, src)

    tool_call = next(e for e in session.events if e.event_type == "tool_call")
    assert tool_call.payload["input"] == {}
