"""Message text caps (spec: docs/modules/normalizer.md §10).

Text is the surviving intent signal -- whether the agent is about to review, implement, or fix.
Claude Code strips the thinking plaintext before persisting the transcript (empty in all 2,382
blocks observed), so assistant text is what a task classifier will have to work from. The cap
bounds the tail without touching the common case: median assistant text is 93 chars and 88% of
blocks are already under 1k.

User text is two populations, and only one is a prompt. A typed prompt is the session's primary
intent signal and ships whole. An injected record -- `isMeta`: skill bodies, task notifications,
command output, system reminders -- is capped; the largest observed was a 151,597-char skill
body arriving as a single user message.
"""
import json

import pytest

from collector.normalizer.claude_code import ClaudeCodeNormalizer
from collector.normalizer.config import NormalizerConfig
from fixtures.normalizer import sessions

LONG_ASSISTANT = "Here is my plan for the refactor. " * 200      # 6,600 chars
LONG_INJECTION = "# Update Config Skill\nModify settings.json. " * 400  # ~17,600 chars
LONG_PROMPT = "Please review the following specification carefully. " * 100  # 5,200 chars


def _run(config, src):
    (session,) = list(ClaudeCodeNormalizer(config).normalize(src))
    return session


def _assistant_session(text=LONG_ASSISTANT):
    src = sessions.simple_session()
    src.records[1]["message"]["content"] = [{"type": "text", "text": text}]
    return src


def _user_session(text, *, is_meta):
    src = sessions.simple_session()
    record = {
        "type": "user",
        "uuid": "evt-u",
        "parentUuid": "evt-2",
        "timestamp": "2026-07-09T14:01:00.000Z",
        "message": {"role": "user", "content": text},
    }
    if is_meta:
        record["isMeta"] = True
    else:
        record["origin"] = {"kind": "human"}
    src.records.append(record)
    return src


def _appended_user(session):
    """simple_session() already carries a prompt at evt-1; select the record these tests
    append, not the first user_message in the stream."""
    return next(e for e in session.events if e.event_type == "user_message" and e.record_id == "evt-u")


def _serialized(session):
    return json.dumps([e.model_dump() for e in session.events])


# -- assistant text ------------------------------------------------------------------------


def test_assistant_text_capped_at_default(normalizer, normalize_one):
    session = normalize_one(normalizer, _assistant_session())
    event = next(e for e in session.events if e.event_type == "assistant_message")
    block = event.payload["raw"]["message"]["content"][0]
    assert len(block["text"]) == 1000
    assert event.payload["text_truncated"] is True


def test_char_count_survives_the_cap(normalizer, normalize_one):
    """Measured on the unscrubbed message, so volume is intact even when the text is not."""
    session = normalize_one(normalizer, _assistant_session())
    event = next(e for e in session.events if e.event_type == "assistant_message")
    assert event.payload["text_char_count"] == len(LONG_ASSISTANT)


def test_short_assistant_text_untouched(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.simple_session())
    event = next(e for e in session.events if e.event_type == "assistant_message")
    assert event.payload["raw"]["message"]["content"][0]["text"] == "Sure, I'll add that now."
    assert event.payload["text_truncated"] is False


@pytest.mark.parametrize("limit,expected", [(-1, len(LONG_ASSISTANT)), (0, 0), (250, 250)])
def test_assistant_cap_semantics(limit, expected):
    session = _run(NormalizerConfig(assistant_text_len=limit), _assistant_session())
    event = next(e for e in session.events if e.event_type == "assistant_message")
    assert len(event.payload["raw"]["message"]["content"][0]["text"]) == expected


def test_thinking_and_tool_use_blocks_unaffected(normalizer, normalize_one):
    """The cap targets `text` blocks only."""
    src = sessions.simple_session()
    src.records[1]["message"]["content"] = [
        {"type": "thinking", "thinking": "x" * 5000, "signature": "sig"},
        {"type": "tool_use", "id": "toolu_a", "name": "Read", "input": {"file_path": "a.py"}},
    ]
    session = normalize_one(normalizer, src)
    event = next(e for e in session.events if e.event_type == "assistant_message")
    blocks = {b["type"]: b for b in event.payload["raw"]["message"]["content"]}
    assert len(blocks["thinking"]["thinking"]) == 5000
    assert blocks["tool_use"]["input"] == {"file_path": "a.py"}


# -- injected user text --------------------------------------------------------------------


def test_injected_user_text_is_capped(normalizer, normalize_one):
    session = normalize_one(normalizer, _user_session(LONG_INJECTION, is_meta=True))
    event = _appended_user(session)
    assert event.payload["is_injected"] is True
    assert event.payload["text_truncated"] is True
    assert len(event.payload["text"]) == 1000
    assert event.payload["char_count"] == len(LONG_INJECTION)


def test_injected_cap_reaches_raw_not_just_text(normalizer, normalize_one):
    """The regression this exists for: capping payload.text while leaving payload.raw whole
    ships the entire body anyway -- and raw is where the 151,597-char skill body rides."""
    session = normalize_one(normalizer, _user_session(LONG_INJECTION, is_meta=True))
    event = _appended_user(session)
    assert len(event.payload["raw"]["message"]["content"]) == 1000
    assert LONG_INJECTION[:1200] not in _serialized(session)


def test_typed_prompt_is_never_capped(normalizer, normalize_one):
    """A typed prompt is the primary intent signal. Long is not the same as injected."""
    session = normalize_one(normalizer, _user_session(LONG_PROMPT, is_meta=False))
    event = _appended_user(session)
    assert event.payload["is_injected"] is False
    assert event.payload["text_truncated"] is False
    assert event.payload["text"] == LONG_PROMPT
    assert event.payload["raw"]["message"]["content"] == LONG_PROMPT


@pytest.mark.parametrize(
    "marker",
    [
        {"isMeta": True},                          # skill body
        {"isCompactSummary": True},                # post-compaction summary
        {"origin": {"kind": "task-notification"}},  # notification
        {"promptSource": "system"},
    ],
    ids=["isMeta", "isCompactSummary", "origin.kind", "promptSource"],
)
def test_every_injection_marker_is_recognised(normalizer, normalize_one, marker):
    """No single marker covers the population -- isMeta misses the compaction summary
    (~30,000 chars) and the task-notification (20,320)."""
    src = sessions.simple_session()
    src.records.append(
        {"type": "user", "uuid": "evt-u", "parentUuid": "evt-2",
         "timestamp": "2026-07-09T14:01:00.000Z",
         "message": {"role": "user", "content": LONG_INJECTION}, **marker}
    )
    session = normalize_one(normalizer, src)
    event = _appended_user(session)
    assert event.payload["is_injected"] is True
    assert len(event.payload["text"]) == 1000


def test_unmarked_record_ships_whole(normalizer, normalize_one):
    """Fails open. The unmarked population is subagent task prompts -- 170 in the corpus,
    median 2,503 chars, max 8,784 -- which are the instruction handed to a subagent. Pure
    intent, and exactly what a cap must not eat."""
    src = sessions.simple_session()
    src.records.append(
        {"type": "user", "uuid": "evt-u", "parentUuid": "evt-2",
         "timestamp": "2026-07-09T14:01:00.000Z",
         "message": {"role": "user", "content": LONG_PROMPT}}
    )
    session = normalize_one(normalizer, src)
    event = _appended_user(session)
    assert event.payload["is_injected"] is False
    assert event.payload["text"] == LONG_PROMPT


@pytest.mark.parametrize("limit,expected", [(-1, len(LONG_INJECTION)), (0, 0), (250, 250)])
def test_injected_cap_semantics(limit, expected):
    session = _run(NormalizerConfig(user_meta_text_len=limit), _user_session(LONG_INJECTION, is_meta=True))
    event = _appended_user(session)
    assert len(event.payload["text"]) == expected


def test_caps_are_independent():
    """Assistant and injected-user text are separate policies, not one knob."""
    src = _assistant_session()
    src.records.append(
        {"type": "user", "uuid": "evt-u", "parentUuid": "evt-2", "isMeta": True,
         "timestamp": "2026-07-09T14:01:00.000Z",
         "message": {"role": "user", "content": LONG_INJECTION}}
    )
    session = _run(NormalizerConfig(assistant_text_len=-1, user_meta_text_len=100), src)
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    user = _appended_user(session)
    assert len(assistant.payload["raw"]["message"]["content"][0]["text"]) == len(LONG_ASSISTANT)
    assert len(user.payload["text"]) == 100


# -- tool results do not re-enter as user text ---------------------------------------------


def test_tool_result_never_becomes_a_user_message(normalizer, normalize_one):
    """Tool output is delivered to the model as a user-role record. It must stay on the
    tool_result path, where the body is scrubbed -- not fall through to user_message, which is
    uncapped for typed prompts. Confirmed across the corpus: 4,996 tool_result records, 0 with
    a text block alongside."""
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    user_texts = [e.payload["text"] for e in session.events if e.event_type == "user_message"]
    assert user_texts == ["Run the tests and fix the failure"]
    # Not "appears nowhere" -- stdout legitimately survives on the tool_result path, capped by
    # tool_result_len. What must not happen is its arriving as a user_message, which is
    # uncapped because typed prompts are intent.
    users = json.dumps([e.model_dump() for e in session.events if e.event_type == "user_message"])
    assert "1 failed, 4 passed" not in users
