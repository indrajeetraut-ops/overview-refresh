"""Thinking blocks: signature dropped, structure kept (spec: docs/modules/normalizer.md §6.4, §10).

Two facts about real Claude Code transcripts drive this, both measured across the reference
corpus (134 files, agent versions 2.1.202 through 2.1.220):

1. The thinking *plaintext* is never written. All 2,382 thinking blocks carry
   `"thinking": ""`. Claude Code strips it before the transcript is persisted, so the
   reasoning->action edge is not reconstructable from the transcript at any version.
2. What remains is an opaque `signature` totalling 6,547,484 chars -- 3.7x all attachment
   content combined, and useless: its length correlates with the response's output_tokens at
   only r=0.37.

So the signature goes and everything structural stays. In particular the *event* stays: 3,299
events in the corpus have a parent_id pointing at a thinking record's uuid, so dropping the
event would re-open the dangling-parent hole that emitting tool_result just closed. Folding a
response's blocks into one node is the graph builder's job, keyed on message.id -- not the
normalizer's.
"""
import json

import pytest

from collector.normalizer.claude_code import ClaudeCodeNormalizer
from collector.normalizer.config import NormalizerConfig
from fixtures.normalizer import sessions

ALL_BUILDERS = [
    sessions.simple_session,
    sessions.tool_pairing_session,
    sessions.denied_tool_session,
    sessions.mixed_model_subagent_session,
    sessions.api_error_and_compaction_session,
    sessions.passthrough_session,
]


def _thinking_session(text: str = "Let me consider the approach.", signature: str = "sig-abc-123"):
    src = sessions.simple_session()
    src.records[1]["message"]["content"] = [
        {"type": "thinking", "thinking": text, "signature": signature},
        {"type": "text", "text": "Here is the answer."},
    ]
    return src


# -- the signature is gone -----------------------------------------------------------------


def test_signature_stripped_from_raw(normalizer, normalize_one):
    session = normalize_one(normalizer, _thinking_session())
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    blocks = assistant.payload["raw"]["message"]["content"]
    thinking = next(b for b in blocks if b["type"] == "thinking")
    assert "signature" not in thinking
    assert "sig-abc-123" not in json.dumps([e.model_dump() for e in session.events])


def test_thinking_text_itself_is_kept(normalizer, normalize_one):
    """Only the signature goes. The `thinking` field stays, so if Claude Code ever restores
    the plaintext it flows through with no further change."""
    session = normalize_one(normalizer, _thinking_session())
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    thinking = next(b for b in assistant.payload["raw"]["message"]["content"] if b["type"] == "thinking")
    assert thinking["thinking"] == "Let me consider the approach."
    assert assistant.payload["thinking_char_count"] == len("Let me consider the approach.")


def test_redacted_thinking_signature_also_stripped(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[1]["message"]["content"] = [
        {"type": "redacted_thinking", "data": "opaque-blob", "signature": "sig-redacted-9"}
    ]
    session = normalize_one(normalizer, src)
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    block = assistant.payload["raw"]["message"]["content"][0]
    assert "signature" not in block
    assert block["data"] == "opaque-blob"  # the block's own payload is untouched


def test_scrub_is_unconditional():
    """Not gated on redact_generated_file_content -- that flag governs generated source, which
    an operator might legitimately want. The signature is dead weight, not a privacy knob."""
    permissive = ClaudeCodeNormalizer(NormalizerConfig(redact_generated_file_content=False))
    (session,) = list(permissive.normalize(_thinking_session()))
    assert "sig-abc-123" not in json.dumps([e.model_dump() for e in session.events])


def test_non_thinking_blocks_untouched(normalizer, normalize_one):
    """A signature on some other block type is not this scrub's business."""
    src = sessions.simple_session()
    src.records[1]["message"]["content"] = [{"type": "text", "text": "hi", "signature": "keep-me"}]
    session = normalize_one(normalizer, src)
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["raw"]["message"]["content"][0]["signature"] == "keep-me"


# -- structure is preserved ----------------------------------------------------------------


def test_thinking_record_still_emits_its_event(normalizer, normalize_one):
    """Load-bearing: 3,299 events in the reference corpus have a parent_id pointing at a
    thinking record's uuid. Dropping the event re-opens the dangling-parent hole."""
    session = normalize_one(normalizer, _thinking_session())
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.record_id == "evt-2"
    assert assistant.payload["content_kind"] == "mixed"
    assert assistant.payload["block_types"] == ["thinking", "text"]


@pytest.mark.parametrize("build", ALL_BUILDERS, ids=lambda b: b.__name__)
def test_spine_still_resolves(normalizer, build):
    for session in normalizer.normalize(build()):
        known = {e.record_id for e in session.events if e.record_id is not None}
        dangling = [e.event_type for e in session.events if e.parent_id is not None and e.parent_id not in known]
        assert dangling == []


# -- has_thinking --------------------------------------------------------------------------


def test_has_thinking_true_for_thinking_block(normalizer, normalize_one):
    session = normalize_one(normalizer, _thinking_session())
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["has_thinking"] is True


def test_has_thinking_true_for_redacted_thinking(normalizer, normalize_one):
    src = sessions.simple_session()
    src.records[1]["message"]["content"] = [{"type": "redacted_thinking", "data": "opaque"}]
    session = normalize_one(normalizer, src)
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["has_thinking"] is True
    assert assistant.payload["thinking_char_count"] is None  # no plaintext field at all


def test_has_thinking_false_without_one(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.simple_session())
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["has_thinking"] is False


def test_empty_thinking_is_zero_not_null(normalizer, normalize_one):
    """What every current Claude Code version actually emits. The 0/null distinction is the
    reason thinking_char_count survives: null means no block, 0 means a block with no text."""
    session = normalize_one(normalizer, _thinking_session(text=""))
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["has_thinking"] is True
    assert assistant.payload["thinking_char_count"] == 0
