from fixtures.normalizer import sessions


def test_session_start_synthesized_first(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.simple_session())
    start = session.events[0]
    assert start.event_type == "session_start"
    assert start.payload["cwd"] == "/Users/dev/repo"
    assert start.payload["git_branch"] == "main"
    assert start.record_id is None


def test_user_message_mapping(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.simple_session())
    event = next(e for e in session.events if e.event_type == "user_message")
    text = "Please add a health check endpoint"
    assert event.payload["text"] == text
    assert event.payload["char_count"] == len(text)
    assert event.payload["slash_command"] is None
    assert event.record_id == "evt-1"
    assert event.payload["raw"]["uuid"] == "evt-1"


def test_slash_command_detected(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[0]["message"]["content"] = "/review please"
    session = normalize_one(normalizer, src)
    event = next(e for e in session.events if e.event_type == "user_message")
    assert event.payload["slash_command"] == "/review"


def test_assistant_message_usage_fields(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.simple_session())
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["model"] == "claude-sonnet-5"
    assert assistant.payload["input_tokens"] == 120
    assert assistant.payload["output_tokens"] == 40
    assert assistant.payload["cache_read_tokens"] == 0
    assert assistant.payload["cache_write_tokens"] == 0
    assert assistant.payload["stop_reason"] == "end_turn"
    assert assistant.payload["duration_ms"] is None
    assert assistant.payload["reasoning_tokens"] is None
    assert assistant.payload["cache_miss_tokens"] is None
    assert assistant.record_id == "evt-2"
    assert assistant.parent_id == "evt-1"


def test_assistant_message_carries_the_vendor_request_id(normalizer, normalize_one):
    """Regression: `provider_request_id` was only ever read on `_make_api_error`'s records,
    so `otel.jsonl_requests_for_session` (the query behind a JSONL-only session's cost
    estimate) found it on error records alone -- every normal, successful session was
    silently unpriceable from JSONL alone, OTel present or not. The vendor's own request id
    (`req_...`, distinct from `message.id`'s `msg_...`) is present on essentially every real
    turn; it just needs to be read here the same way the error handler already reads it."""
    session = normalize_one(normalizer, sessions.simple_session())
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["provider_request_id"] == "req_fixture_01"


def test_content_kind_text_only(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.simple_session())
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["content_kind"] == "text"
    assert assistant.payload["block_types"] == ["text"]
    assert assistant.payload["text_char_count"] == len("Sure, I'll add that now.")
    assert assistant.payload["thinking_char_count"] is None


def test_content_kind_tool_use_only(normalizer, normalize_one):
    """tool_pairing_session's Edit-call record (evt-4) has a single tool_use block, no text."""
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    edit_assistant = next(e for e in session.events if e.event_type == "assistant_message" and e.record_id == "evt-4")
    assert edit_assistant.payload["content_kind"] == "tool_use"
    assert edit_assistant.payload["block_types"] == ["tool_use"]
    assert edit_assistant.payload["text_char_count"] is None
    assert edit_assistant.payload["thinking_char_count"] is None


def test_content_kind_mixed_text_and_tool_use(normalizer, normalize_one):
    """tool_pairing_session's Bash-call record (evt-2) narrates in text before the tool_use."""
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    bash_assistant = next(e for e in session.events if e.event_type == "assistant_message" and e.record_id == "evt-2")
    assert bash_assistant.payload["content_kind"] == "mixed"
    assert bash_assistant.payload["block_types"] == ["text", "tool_use"]
    assert bash_assistant.payload["text_char_count"] == len("Let me run the tests first.")


def test_content_kind_thinking_only(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[1]["message"]["content"] = [
        {"type": "thinking", "thinking": "Let me consider the approach.", "signature": "sig123"}
    ]
    session = normalize_one(normalizer, src)
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["content_kind"] == "thinking"
    assert assistant.payload["block_types"] == ["thinking"]
    assert assistant.payload["thinking_char_count"] == len("Let me consider the approach.")
    assert assistant.payload["text_char_count"] is None


def test_content_kind_redacted_thinking_classified_as_thinking(normalizer, normalize_one):
    """redacted_thinking has no plaintext (just an opaque `data` blob per the Claude API) --
    it still buckets as content_kind='thinking', but thinking_char_count stays None since
    there's nothing to measure."""
    src = sessions.simple_session()
    src.records[1]["message"]["content"] = [{"type": "redacted_thinking", "data": "opaque-blob"}]
    session = normalize_one(normalizer, src)
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["content_kind"] == "thinking"
    assert assistant.payload["block_types"] == ["redacted_thinking"]
    assert assistant.payload["thinking_char_count"] is None


def test_content_kind_empty(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[1]["message"]["content"] = []
    session = normalize_one(normalizer, src)
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["content_kind"] == "empty"
    assert assistant.payload["block_types"] == []


def test_cache_write_detail_and_server_tool_use(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[1]["message"]["usage"]["cache_creation"] = {
        "ephemeral_5m_input_tokens": 8,
        "ephemeral_1h_input_tokens": 2,
    }
    src.records[1]["message"]["usage"]["server_tool_use"] = {"web_search": 1}
    session = normalize_one(normalizer, src)
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["cache_write_detail"] == [
        {"ttl_s": 300, "tokens": 8},
        {"ttl_s": 3600, "tokens": 2},
    ]
    assert assistant.payload["server_tool_use"] == {"web_search": 1}


def test_cache_write_detail_omitted_when_no_cache_creation(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.simple_session())
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert "cache_write_detail" not in assistant.payload


def test_server_tool_use_omitted_when_all_zero(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[1]["message"]["usage"]["server_tool_use"] = {"web_search": 0}
    session = normalize_one(normalizer, src)
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert "server_tool_use" not in assistant.payload


def test_api_error_mapping(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.api_error_and_compaction_session())
    error = next(e for e in session.events if e.event_type == "api_error")
    assert error.payload["error_class"] == "overloaded"
    assert error.payload["http_status"] == 529
    assert error.payload["retryable"] is True
    assert error.payload["retry_count"] == 1
    assert error.payload["retry_after_ms"] == 2000
    assert error.payload["provider_request_id"] == "req-abc123"
    assert error.payload["message"] == "Overloaded"
    assert error.record_id == "evt-2"


def test_compaction_mapping(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.api_error_and_compaction_session())
    compaction = next(e for e in session.events if e.event_type == "compaction")
    assert compaction.payload["trigger"] == "auto"
    assert compaction.payload["tokens_before"] == 180000
    assert compaction.payload["tokens_after"] == 20000
    assert compaction.payload["tokens_reclaimed"] == 160000
    assert compaction.payload["duration_ms"] == 1500
    assert compaction.record_id == "evt-3"


def test_passthrough_events_never_dropped(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.passthrough_session())
    unknown_events = [e for e in session.events if e.event_type == "unknown"]
    assert len(unknown_events) == 3

    mode_event = next(e for e in unknown_events if e.payload["raw"]["type"] == "mode")
    assert mode_event.record_id is None  # no uuid on this record type

    future_event = next(e for e in unknown_events if e.payload["raw"]["type"] == "some-future-record-type")
    assert future_event.record_id == "evt-4"


# -- ai-title: session-level, plucked once (§6.2) -------------------------------------------


def _with_ai_title(*titles, at=2):
    """CC never emits ai-title at record 0 -- earliest observed is index 8 -- so the fixture
    inserts it after the session has started, which is what forces the backfill."""
    src = sessions.simple_session()
    for offset, title in enumerate(titles):
        src.records.insert(at + offset, {"type": "ai-title", "aiTitle": title, "sessionId": "session-simple"})
    return src


def _with_custom_title(*titles, at=2):
    """Regression (found live 2026-09-12): agent_version 2.1.260 renamed the record from
    `ai-title`/`aiTitle` to `custom-title`/`customTitle` -- same shape, new names. Every
    2.1.260 session in the stack DB (6/6) had lost its title before `_backfill_session_facts`
    was taught to read this key too."""
    src = sessions.simple_session()
    for offset, title in enumerate(titles):
        src.records.insert(at + offset, {"type": "custom-title", "customTitle": title, "sessionId": "session-simple"})
    return src


def test_ai_title_lifted_onto_session_start(normalizer, normalize_one):
    session = normalize_one(normalizer, _with_ai_title("Brainstorm event messaging architecture"))
    start = next(e for e in session.events if e.event_type == "session_start")
    assert start.payload["ai_title"] == "Brainstorm event messaging architecture"


def test_repeats_do_not_multiply_the_field(normalizer, normalize_one):
    """3-42 identical records per session in the corpus. The point is one field, not N."""
    session = normalize_one(normalizer, _with_ai_title(*(["Add Mermaid diagrams"] * 12)))
    starts = [e for e in session.events if e.event_type == "session_start"]
    assert len(starts) == 1
    assert starts[0].payload["ai_title"] == "Add Mermaid diagrams"


def test_first_title_wins(normalizer, normalize_one):
    """No observed session changes its title. If one ever does, the first is the label that
    described the work as it was happening."""
    session = normalize_one(normalizer, _with_ai_title("First goal", "Revised goal"))
    start = next(e for e in session.events if e.event_type == "session_start")
    assert start.payload["ai_title"] == "First goal"


def test_absent_title_is_null_not_missing(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.simple_session())
    start = next(e for e in session.events if e.event_type == "session_start")
    assert start.payload["ai_title"] is None


def test_records_still_ship_as_passthrough(normalizer, normalize_one):
    """§9 has no silent ignore-list: lifting the value does not delete the records."""
    session = normalize_one(normalizer, _with_ai_title(*(["Same title"] * 3)))
    passthrough = [e for e in session.events
                   if e.event_type == "unknown" and e.payload["raw"].get("type") == "ai-title"]
    assert len(passthrough) == 3


def test_subagent_sessions_have_no_title(normalizer):
    """No subagent stream in the corpus carries one; the parent's must not leak down."""
    for session in normalizer.normalize(sessions.mixed_model_subagent_session()):
        start = next(e for e in session.events if e.event_type == "session_start")
        assert start.payload["ai_title"] is None


def test_custom_title_lifted_onto_session_start(normalizer, normalize_one):
    """Regression: agent_version 2.1.260 emits `custom-title`/`customTitle` instead of
    `ai-title`/`aiTitle` -- confirmed live, every 2.1.260 session in the stack DB had no title
    before this. Same payload field (`ai_title`) either way; only the raw record shape changed."""
    session = normalize_one(normalizer, _with_custom_title("Wire the F2 trusted header"))
    start = next(e for e in session.events if e.event_type == "session_start")
    assert start.payload["ai_title"] == "Wire the F2 trusted header"


def test_custom_title_records_still_ship_as_passthrough(normalizer, normalize_one):
    session = normalize_one(normalizer, _with_custom_title(*(["Same title"] * 3)))
    passthrough = [e for e in session.events
                   if e.event_type == "unknown" and e.payload["raw"].get("type") == "custom-title"]
    assert len(passthrough) == 3
