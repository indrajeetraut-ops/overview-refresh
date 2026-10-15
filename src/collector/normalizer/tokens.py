"""Counts the visible output of an assistant response, with a local tokenizer.

Why this exists: the transcript reports `output_tokens` for a response but never says how
much of it was thinking. Thinking is billed inside that total, and its plaintext is stripped
before the transcript is written, so the only way to recover the split is to count what *is*
visible and subtract:

    thinking_tokens = output_tokens - visible_tokens - tool-call framing

That subtraction was validated against Anthropic's own tokenizer, not assumed: on a model
that can be both run and counted, `output_tokens` equals the token count of the visible reply
exactly. See docs/design/notes/claude_token_usage.md.

**We store the raw tokenizer count and apply no conversion here.** tiktoken is OpenAI's
vocabulary, not Anthropic's, and the gap between them depends on which model wrote the reply
-- roughly 1.13x on Sonnet 4.6 against roughly 1.52x on the Claude 5 family. A converted
number written into stored data would freeze one model's multiplier into every session and
could never be corrected once the content is redacted. A raw count stays true, and the
multiplier lives downstream where one edit re-derives the whole history.

Counting must happen *before* the scrubs. Write/Edit arguments are generated output tokens
and the generated-content scrub removes them, so counting afterwards would undercount
precisely the largest responses.
"""
from __future__ import annotations

import functools
import json

__all__ = ["VisibleOutput", "count_visible_output"]


class VisibleOutput(dict):
    """Counts for one assistant record. A dict so it merges straight into the payload."""


@functools.lru_cache(maxsize=4)
def _encoding(name: str):
    """Cached: building a tiktoken encoding is expensive and the vocab is immutable.

    Imported lazily so the dependency is only required when counting is switched on.
    """
    import tiktoken

    return tiktoken.get_encoding(name)


def _serialize_tool_call(block: dict) -> str:
    """The generated form of a tool call: its name followed by its arguments.

    Separators are `", "` / `": "` deliberately. The measurement that produced the framing
    constants used this form, so a change here silently invalidates them.
    """
    name = block.get("name") or ""
    try:
        args = json.dumps(block.get("input") or {}, separators=(", ", ": "), ensure_ascii=False)
    except (TypeError, ValueError):
        # A tool argument that will not serialize is still real output; fall back to the
        # repr rather than dropping it and undercounting the response.
        args = repr(block.get("input"))
    return name + args


def count_visible_output(content, encoding_name: str) -> VisibleOutput:
    """Tokenizes the visible blocks of one assistant record.

    Visible means what the model emitted and we can still see: reply text, and the name and
    arguments of each tool call. Thinking blocks are excluded -- recovering their cost is the
    entire point, so counting them here would defeat it. `content` is the *unscrubbed*
    message content.

    Per record, not per response. Claude Code splits one response across several records
    (thinking in one, text in another, tool calls in a third), so these are block-scoped and
    a consumer sums them across a `message_id` to get the response total -- the same
    cross-record derivation that turn segmentation does, and it belongs to the graph layer
    for the same reason.
    """
    if not isinstance(content, list):
        content = []

    text_parts: list[str] = []
    tool_parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text_parts.append(block.get("text") or "")
        elif kind == "tool_use":
            tool_parts.append(_serialize_tool_call(block))

    enc = _encoding(encoding_name)
    # disallowed_special=(): transcripts routinely contain text that looks like a special
    # token (docs about prompt formats, for one). Without this tiktoken raises instead of
    # counting it as ordinary characters, which is what it is here.
    encode = functools.partial(enc.encode, disallowed_special=())

    text = "".join(text_parts)
    tool_args = "".join(tool_parts)
    return VisibleOutput(
        estimated_visible_tokens=len(encode(text)) + len(encode(tool_args)) if (text or tool_args) else 0,
        output_tokenizer=encoding_name,
        tool_arg_chars=len(tool_args),
        tool_call_count=len(tool_parts),
    )
