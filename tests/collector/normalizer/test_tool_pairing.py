from fixtures.normalizer import sessions


def test_bash_tool_call_paired_with_result(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    bash_call = next(e for e in session.events if e.event_type == "tool_call" and e.payload["tool_name"] == "Bash")
    assert bash_call.record_id == "toolu_bash1"
    assert bash_call.parent_id == "evt-2"
    assert bash_call.payload["input"] == {"command": "pytest -q"}
    assert bash_call.payload["exit_status"] == 1
    assert bash_call.payload["duration_ms"] == 850
    assert bash_call.payload["bytes_read"] == len("1 failed, 4 passed".encode("utf-8"))


def test_edit_tool_call_and_derived_file_change(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.tool_pairing_session())

    edit_call = next(e for e in session.events if e.event_type == "tool_call" and e.payload["tool_name"] == "Edit")
    assert edit_call.record_id == "toolu_edit1"
    assert edit_call.payload["exit_status"] == 0
    assert edit_call.payload["files"][0]["path"] == "src/app.py"
    assert edit_call.payload["files"][0]["operation"] == "edit"
    # lines_added/lines_removed are the exact +/- counts, computed from the structuredPatch `lines`
    # body before it is dropped (restored 2026-08-30 so session_info.files keeps code-churn counts;
    # the hunk sizes below include context and can't reproduce them).
    assert edit_call.payload["files"][0]["lines_added"] == 1
    assert edit_call.payload["files"][0]["lines_removed"] == 1
    assert edit_call.payload["files"][0]["hunks"] == [{"start": 10, "len": 1, "old_len": 1}]

    file_change = next(e for e in session.events if e.event_type == "file_change")
    assert file_change.payload["path"] == "src/app.py"
    assert file_change.payload["extension"] == ".py"
    assert file_change.payload["operation"] == "edit"
    assert file_change.payload["lines_added"] == 1  # exact +/- counts, restored 2026-08-30
    assert file_change.payload["lines_removed"] == 1
    assert file_change.payload["hunks"] == [{"start": 10, "len": 1, "old_len": 1}]
    # net delta is recoverable from stored data, which is the point of old_len
    hunk = file_change.payload["hunks"][0]
    assert hunk["len"] - hunk["old_len"] == 0
    assert file_change.payload["derived_from_event_id"] == "toolu_edit1"
    assert file_change.record_id is None
    assert file_change.parent_id == "evt-4"


def test_event_order_matches_file_order(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    assert [e.event_type for e in session.events] == [
        "session_start",
        "user_message",
        "assistant_message",
        "tool_call",
        "tool_result",
        "assistant_message",
        "tool_call",
        "file_change",
        "tool_result",
    ]


def test_unresolved_tool_call_keeps_null_placeholders(normalizer, normalize_one):
    src = sessions.tool_pairing_session()
    src.records = src.records[:2]  # drop both tool_result records
    session = normalize_one(normalizer, src)
    bash_call = next(e for e in session.events if e.event_type == "tool_call")
    assert bash_call.payload["exit_status"] is None
    assert bash_call.payload["duration_ms"] is None
    assert bash_call.payload["bytes_read"] is None


def test_mcp_tool_name_parsed(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[1]["message"]["content"].append(
        {"type": "tool_use", "id": "toolu_mcp1", "name": "mcp__github__search_issues", "input": {"query": "bug"}}
    )
    session = normalize_one(normalizer, src)
    mcp_call = next(e for e in session.events if e.event_type == "tool_call" and e.payload["tool_name"].startswith("mcp__"))
    assert mcp_call.payload["mcp_server"] == "github"
    assert mcp_call.payload["mcp_tool"] == "search_issues"
