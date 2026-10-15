"""Raw tokenizer counts for the visible half of an assistant response.

The transcript bills thinking inside `output_tokens` and never breaks it out. Counting what
is visible -- reply text, tool names and arguments -- lets a consumer subtract and recover
what the thinking cost. See docs/design/notes/claude_token_usage.md.

Two properties carry the whole feature and both are easy to break silently:

  * counting happens *before* the scrubs, so redacted Write/Edit bodies are still counted;
  * the count is raw, with no vendor multiplier applied, because the tiktoken-to-Anthropic
    ratio depends on which model wrote the reply.
"""
import pytest

from collector.normalizer.claude_code import ClaudeCodeNormalizer
from collector.normalizer.config import NormalizerConfig
from collector.normalizer.tokens import count_visible_output
from fixtures.normalizer import sessions

_ENC = "o200k_base"


def _assistants(session):
    return [e for e in session.events if e.event_type == "assistant_message"]


def _session_with(blocks, usage=None):
    src = sessions.simple_session()
    src.records = [
        src.records[0],
        {
            "type": "assistant",
            "uuid": "evt-a",
            "parentUuid": "evt-1",
            "timestamp": "2026-07-09T18:00:00.000Z",
            "message": {
                "role": "assistant",
                "model": "claude-opus-5",
                "id": "msg_counted",
                "content": blocks,
                "usage": usage or {"input_tokens": 10, "output_tokens": 500},
                "stop_reason": "tool_use",
            },
        },
    ]
    return src


# -- what counts as visible ------------------------------------------------------------


def test_counts_text_and_tool_arguments():
    out = count_visible_output(
        [
            {"type": "text", "text": "Running the tests."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}},
        ],
        _ENC,
    )
    assert out["estimated_visible_tokens"] > 0
    assert out["tool_call_count"] == 1
    assert out["tool_arg_chars"] == len('Bash{"command": "pytest -q"}')


def test_thinking_is_excluded():
    """Counting thinking would defeat the point -- its cost is what we are solving for."""
    without = count_visible_output([{"type": "text", "text": "hello"}], _ENC)
    with_thinking = count_visible_output(
        [
            {"type": "text", "text": "hello"},
            {"type": "thinking", "thinking": "", "signature": "x" * 5000},
        ],
        _ENC,
    )
    assert with_thinking["estimated_visible_tokens"] == without["estimated_visible_tokens"]


def test_empty_content_counts_zero_not_null():
    """0 means "nothing visible here", null means "not counted" -- a consumer must be able
    to tell a thinking-only record from a disabled counter."""
    out = count_visible_output([], _ENC)
    assert out["estimated_visible_tokens"] == 0
    assert out["tool_call_count"] == 0


def test_unserializable_tool_input_still_counts():
    """A tool argument that will not serialize is still real output; dropping it would
    undercount the response and inflate the thinking derived from it."""
    out = count_visible_output(
        [{"type": "tool_use", "name": "Weird", "input": {"x": {1, 2, 3}}}], _ENC
    )
    assert out["estimated_visible_tokens"] > 0
    assert out["tool_call_count"] == 1


# -- the ordering guarantee ------------------------------------------------------------


def test_counted_before_redaction_of_generated_content():
    """The load-bearing one. A Write body is generated output -- billed to output_tokens --
    and the generated-content scrub removes it. Counting after the scrub would undercount
    exactly the largest responses, and the shortfall would surface as phantom thinking."""
    body = "def solve():\n    return 42\n" * 400
    normalizer = ClaudeCodeNormalizer(NormalizerConfig(redact_generated_file_content=True))
    src = _session_with(
        [{"type": "tool_use", "name": "Write", "input": {"file_path": "/a.py", "content": body}}]
    )
    (session,) = list(normalizer.normalize(src))
    (event,) = _assistants(session)

    # counted: the body is in the number
    assert event.payload["tool_arg_chars"] > len(body)
    assert event.payload["estimated_visible_tokens"] > 1000

    # and still redacted: the number did not come at the cost of the scrub
    assert body not in str(event.payload["raw"])


def test_text_cap_does_not_shrink_the_count():
    """Assistant text is capped on the way into `raw`. The count is taken from the
    unscrubbed message, so a capped reply is still counted at full length."""
    long_text = "word " * 4000
    normalizer = ClaudeCodeNormalizer(NormalizerConfig(assistant_text_len=100))
    src = _session_with([{"type": "text", "text": long_text}])
    (session,) = list(normalizer.normalize(src))
    (event,) = _assistants(session)

    assert event.payload["text_truncated"] is True
    assert event.payload["estimated_visible_tokens"] > 1000


# -- shape on the event ----------------------------------------------------------------


def test_encoding_name_ships_on_every_event():
    """A count is only interpretable against the vocabulary that produced it, and the
    encoding will eventually change."""
    normalizer = ClaudeCodeNormalizer()
    (session,) = list(normalizer.normalize(_session_with([{"type": "text", "text": "hi"}])))
    assert _assistants(session)[0].payload["output_tokenizer"] == "o200k_base"


def test_counting_can_be_switched_off():
    normalizer = ClaudeCodeNormalizer(NormalizerConfig(count_estimated_visible_tokens=False))
    (session,) = list(normalizer.normalize(_session_with([{"type": "text", "text": "hi"}])))
    payload = _assistants(session)[0].payload
    assert payload["estimated_visible_tokens"] is None
    assert payload["output_tokenizer"] is None


def test_count_is_raw_with_no_vendor_multiplier_applied():
    """Storing a converted number would freeze one model family's ratio into the data and
    could never be corrected once content is redacted. The multiplier belongs downstream."""
    import tiktoken

    text = "The quick brown fox jumps over the lazy dog. " * 20
    expected = len(tiktoken.get_encoding(_ENC).encode(text))
    out = count_visible_output([{"type": "text", "text": text}], _ENC)
    assert out["estimated_visible_tokens"] == expected


def test_counts_are_per_record_so_they_sum_across_a_split_response():
    """Claude Code splits one response across records. Each record counts its own blocks;
    summing over a message_id gives the response total."""
    normalizer = ClaudeCodeNormalizer()
    whole = count_visible_output(
        [
            {"type": "text", "text": "Running the tests."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}},
        ],
        _ENC,
    )
    a = count_visible_output([{"type": "text", "text": "Running the tests."}], _ENC)
    b = count_visible_output(
        [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}], _ENC
    )
    assert a["estimated_visible_tokens"] + b["estimated_visible_tokens"] == whole["estimated_visible_tokens"]
