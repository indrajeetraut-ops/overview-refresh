"""ClaudeCodeNormalizer -- maps Claude Code records to common events.

One pass over a session's records, dispatching by CC `type` per the table. Tool-use/
tool-result pairing is emit-at-call, enrich-in-place: a tool_call (and any derived
file_change) is appended to the output the moment its tool_use block is seen, with
result-dependent fields (exit_status, duration_ms, bytes_read, hunks)
null; the matching tool_result later mutates that same payload dict in place. This keeps
file order intact without buffering, and anything left open at end-of-stream simply keeps
its null placeholders -- the "tool ran, result never arrived" case is never lost.

record_id tiers, pinned here: a `tool_use` block's own `id` -- not the enclosing assistant
record's `uuid` -- identifies its tool_call/sub_agent_spawn, because one assistant record
fans out to N such events and reusing the record uuid for all of them would collide once
msging stamps event_id = record_id.
Derived events with no natural vendor id (session_start, file_change) get record_id
= None, falling to msging's seq-based Tier-2 fallback.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

import structlog

from . import redact
from .config import NormalizerConfig
from .models import EventIn, NormalizedSession, SessionSource, SubagentStream
from .tokens import count_visible_output

log = structlog.get_logger(module="collector.normalizer.claude_code")

_SPAWN_TOOL_NAMES = {"Agent", "Task"}
_FILE_EDIT_TOOL_NAMES = {"Edit", "Write", "MultiEdit"}

_CONTENT_PLACEHOLDER = "[CONTENT OMITTED]"
_TRUNCATION_MARK = "…[TRUNCATED]"

# Key names whose value is a copy of a file, wherever they appear under `toolUseResult`,
# `mcpMeta`, or a tool_result block. Matched by NAME and walked recursively -- that is what
# lets a nested body be cut without losing the facts beside it.
#
# `content` is the entry that is not always a file: on an Agent result it is the subagent's
# report. It is governed here anyway because it is the same key, and the report is recoverable
# from the child session's own records. Raise tool_result_content_len to keep it.
#
# Structural knowledge of the vendor's shape, so it lives in code; the length POLICY lives in
# config -- the same split as _ATTACHMENT_CONTENT_FIELDS / attachment_len.
_TOOL_RESULT_CONTENT_KEYS = frozenset(
    {
        "content",  # Write: the whole written file. Agent: the subagent's report.
        "file",  # Read: {content, filePath, numLines, totalLines} -- only content goes.
        "lines",  # structuredPatch[].lines: the diff body.
        "originalFile",  # Edit: the pre-edit file, whole.
        "oldString",  # Edit
        "newString",  # Edit
        "fileContent",  # MCP read_file_content
        "text",  # MCP content blocks
        "base64",  # Read of an image: the blob. `type` and `dimensions` beside it survive.
        "data",  # an image block's `source.data`. Screenshots arrive here, nested two levels
        # inside a tool_result block's own content list, so only a recursive walk reaches them.
        # Capping instead of dropping shipped 31 x 1,012 chars of base64 -- a truncated PNG,
        # which is content and is not even a usable image.
    }
)

# Keys carrying *intent* -- what someone asked for, not what a tool returned. A plan submitted
# through ExitPlanMode and the prompt handed to a subagent are the record of what was being
# attempted, and truncating them loses the thing worth reading. They get their own policy so
# they can be kept whole without also un-capping `stdout`.
_TOOL_RESULT_INTENT_KEYS = frozenset({"plan", "prompt"})

# Field(s) holding the actual generated/written text, per tool. Scrubbing these is
# unconditional -- independent of config.redaction_patterns -- because content written to
# disk is categorically sensitive, not merely secret-shaped. MultiEdit is handled separately
# below since its content lives inside a list of edits, not flat fields.
_GENERATED_CONTENT_FIELDS = {
    "Write": ("content",),
    "Edit": ("old_string", "new_string"),
}

_TEXT_BLOCK_TYPES = {"text"}
_THINKING_BLOCK_TYPES = {"thinking", "redacted_thinking"}

# Where each attachment type keeps its body, as a path into the attachment dict.
# Structural knowledge of the vendor format, so it lives in code next to
# _GENERATED_CONTENT_FIELDS; the *length* policy is config (attachment_len).
#
# Only long-form fields are listed. Sibling metadata -- `names`, `skillCount`, `addedNames`,
# `filename`, `numLines`/`totalLines`, `exitCode`, `hookName` -- is short, is the part worth
# querying, and rides through untouched.
#
# A type absent from this map is not trusted to be safe: _cap_attachment applies the cap to
# every string field it finds. That default-deny is what stops a new Claude Code version from
# introducing a leak silently.
_ATTACHMENT_CONTENT_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "file": (("content", "file", "content"),),
    "already_read_file": (("content", "file", "content"),),
    "edited_text_file": (("snippet",),),
    "selected_lines_in_ide": (("content",),),
    "skill_listing": (("content",),),
    "hook_success": (("stdout",), ("stderr",), ("content",)),
    "hook_additional_context": (("content",),),
    "directory": (("content",),),
    "queued_command": (("prompt",),),
    "goal_status": (("condition",),),
    "read_truncation_notice": (("banner",),),
    "agent_listing_delta": (("addedLines",),),
    "deferred_tools_delta": (("addedLines",),),
    "task_reminder": (("content",),),
    # Metadata-only in every observed sample -- listed so they bypass the default-deny sweep
    # rather than having short fields needlessly clipped.
    "opened_file_in_ide": (),
    "compact_file_reference": (),
    "command_permissions": (),
    "date_change": (),
    "auto_mode": (),
}

# Attachment fields naming the artifact, most specific first.
_ATTACHMENT_PATH_FIELDS = ("filename", "path")

# Identity metadata promoted onto the payload, per type: (vendor field, emitted field).
# The body is capped; this is what stays queryable regardless. Without it, "which skills
# were in context", "which hook fired", "how many tools were added" all mean reaching into raw.
# Absent keys are simply not emitted -- a type is not required to carry every field listed.
_ATTACHMENT_METADATA_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "skill_listing": (("names", "skill_names"), ("skillCount", "skill_count")),
    "agent_listing_delta": (("addedTypes", "added_types"), ("removedTypes", "removed_types")),
    "deferred_tools_delta": (("addedNames", "added_names"), ("removedNames", "removed_names")),
    "hook_success": (
        ("hookName", "hook_name"),
        ("hookEvent", "hook_event"),
        ("exitCode", "exit_code"),
        ("durationMs", "duration_ms"),
    ),
    "hook_additional_context": (("hookName", "hook_name"), ("hookEvent", "hook_event")),
    "file": (("displayPath", "display_path"),),
    "already_read_file": (("displayPath", "display_path"),),
    "edited_text_file": (("displayPath", "display_path"),),
    "compact_file_reference": (("displayPath", "display_path"),),
    "directory": (("displayPath", "display_path"),),
    "selected_lines_in_ide": (
        ("displayPath", "display_path"),
        ("lineStart", "line_start"),
        ("lineEnd", "line_end"),
    ),
    "read_truncation_notice": (("toolUseID", "tool_use_id"),),
    "queued_command": (("commandMode", "command_mode"),),
    "task_reminder": (("itemCount", "item_count"),),
    "date_change": (("newDate", "new_date"),),
}

# Nested line-count, for the types that report it.
_ATTACHMENT_TOTAL_LINES_PATH = ("content", "file", "totalLines")

_ERROR_CLASS_BY_STATUS = {
    429: "rate_limit",
    529: "overloaded",
    401: "auth",
    403: "auth",
    408: "timeout",
    504: "timeout",
}


def _invert(class_to_values: dict[str, list[str]], *, lower: bool = False) -> dict[str, str]:
    """{class: [values]} -> {value: class}. First class listed wins a duplicate value, so an
    operator's ordering is the tie-break rather than dict iteration order."""
    out: dict[str, str] = {}
    for cls, values in class_to_values.items():
        for value in values:
            key = value.lower() if lower else value
            out.setdefault(key, cls)
    return out


def _classify_error(status) -> str:
    if not isinstance(status, int):
        return "other"
    if status in _ERROR_CLASS_BY_STATUS:
        return _ERROR_CLASS_BY_STATUS[status]
    if 500 <= status < 600:
        return "server"
    return "other"


def _as_dict(value) -> dict:
    """Real Claude Code records don't always shape a field the way most records do
    (the "never crash on new input" / must-ignore stance)
    -- e.g. toolUseResult is a plain string for some tools, a structured object for others.
    `X.get(k) or {}` looks safe but isn't: it only substitutes {} when the value is falsy,
    not when it's present-but-wrong-typed, so a truthy non-dict (a string, a list) still
    reaches a later `.get()` and crashes. This treats anything that isn't actually a dict
    as absent instead."""
    return value if isinstance(value, dict) else {}


def _classify_content(content: list) -> tuple[str, list[str]]:
    """Buckets one assistant record's content[] shape into a coarse content_kind (issue #24).
    Real Claude Code records emit one content block *type* per JSONL record for a turn --
    a thinking-only record, then a text-only record, then a tool_use-only record, all sharing
    one message.id -- so today's uniform `assistant_message` for all of them forces every
    consumer to re-parse raw just to tell a reasoning turn from a final answer from a tool
    call. block_types keeps the exact ordered list for anyone who needs finer detail (e.g.
    distinguishing `redacted_thinking`) without falling back to raw. Note: an Agent/Task
    tool_use block still counts as "tool_use" here even though it becomes a sub_agent_spawn,
    not a tool_call, event -- this is a shape classification, not a tool_call tally."""
    block_types = [b.get("type") for b in content if isinstance(b, dict) and b.get("type")]
    distinct = set(block_types)
    if not block_types:
        kind = "empty"
    elif distinct == {"tool_use"}:
        kind = "tool_use"
    elif distinct <= _TEXT_BLOCK_TYPES:
        kind = "text"
    elif distinct <= _THINKING_BLOCK_TYPES:
        kind = "thinking"
    else:
        kind = "mixed"
    return kind, block_types


def _hunks_from_structured_patch(structured_patch) -> list[dict]:
    """Where each edit landed, and how much it moved.

    `old_len` matters: without it a hunk records the region's new size but not its change, so
    the net delta -- `len - old_len` -- is not recoverable from stored data. The vendor's own
    `linesAdded` is absent on every Edit/Write result (checked: 0 of 1,690), so this is the
    only surviving signal of edit size.

    The patch itself is content, up to 58 KB with the file text in `lines[]`, and is dropped.
    Only these three integers per hunk survive.
    """
    if not isinstance(structured_patch, list):
        return []
    return [
        {"start": h.get("newStart"), "len": h.get("newLines"), "old_len": h.get("oldLines")}
        for h in structured_patch
        if isinstance(h, dict)
    ]


def _lines_added_removed(tool_use_result: dict) -> tuple[int | None, int | None]:
    """Exact added/removed line counts from a Write/Edit/MultiEdit result -- ported from the
    github normalizer. The vendor sets no linesAdded/linesRemoved on these results; the only
    source is `structuredPatch`'s per-hunk unified-diff `lines[]` body. Counts `+`/`-` prefixed
    lines (a `+++`/`---` header or a `\\ No newline` marker is naturally excluded). A `Write` create
    carries an empty patch -> falls back to the created content's own line count, removed=0.

    Computed here, before `_hunks_from_structured_patch` discards the `lines` body, and carried onto
    the file entry as `lines_added`/`lines_removed` so `session_info.files` keeps exact code-churn
    counts (the graph's hunk sizes include context lines and cannot reproduce them)."""
    structured_patch = tool_use_result.get("structuredPatch")
    if isinstance(structured_patch, list) and structured_patch:
        added = removed = 0
        for hunk in structured_patch:
            if not isinstance(hunk, dict):
                continue
            for line in hunk.get("lines") or []:
                if not isinstance(line, str) or not line:
                    continue
                if line.startswith("+") and not line.startswith("+++"):
                    added += 1
                elif line.startswith("-") and not line.startswith("---"):
                    removed += 1
        return added, removed
    if tool_use_result.get("type") == "create":
        content = tool_use_result.get("content")
        if isinstance(content, str):
            return (len(content.splitlines()) if content else 0), 0
    return None, None


def _scrub_tool_input(name: str, tool_input: dict) -> dict:
    """Replaces a Write/Edit/MultiEdit block's generated file content with a placeholder.
    Returns tool_input unchanged for every other tool -- this never touches file_path,
    command text, or any other field, only the specific fields known to carry written
    content for these three tool names."""
    if name in _GENERATED_CONTENT_FIELDS:
        scrubbed = dict(tool_input)
        for field in _GENERATED_CONTENT_FIELDS[name]:
            if field in scrubbed:
                scrubbed[field] = _CONTENT_PLACEHOLDER
        return scrubbed
    if name == "MultiEdit":
        edits = tool_input.get("edits")
        if isinstance(edits, list):
            scrubbed = dict(tool_input)
            scrubbed["edits"] = [
                {**edit, "old_string": _CONTENT_PLACEHOLDER, "new_string": _CONTENT_PLACEHOLDER}
                if isinstance(edit, dict)
                else edit
                for edit in edits
            ]
            return scrubbed
    return tool_input


def _truncate(value, limit: int):
    """Applies the length policy to one body value. -1 keeps it, 0 empties it, N clips it.
    Returns (new_value, original_chars, was_truncated). Handles the list-of-strings shape
    (`addedLines`, `hook_additional_context.content`) as well as plain strings, measuring the
    list by its serialized length so one policy covers both."""
    if isinstance(value, str):
        original = len(value)
        if limit < 0 or original <= limit:
            return value, original, False
        return value[:limit], original, True
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        original = sum(len(x) for x in value)
        if limit < 0 or original <= limit:
            return value, original, False
        kept, used = [], 0
        for item in value:
            if used + len(item) > limit:
                kept.append(item[: max(0, limit - used)])
                break
            kept.append(item)
            used += len(item)
        return [x for x in kept if x], original, True
    return value, 0, False


def _cap_attachment(attachment: dict, limit: int, unknown_limit: int) -> tuple[dict, int, bool]:
    """Applies the length policy to an attachment's body fields. Returns (capped,
    content_chars, truncated), where content_chars is the *pre-truncation* total -- the size
    signal survives even when every byte of the body is dropped.

    A known type is capped at `limit`, only at its mapped paths. A type absent from the map
    is capped at `unknown_limit` across *every* string field, because a body nobody has
    classified is exactly the one we cannot assume is safe. That asymmetry is the point: it is
    what stops a future Claude Code version from introducing a leak silently."""
    fields = _ATTACHMENT_CONTENT_FIELDS.get(attachment.get("type"))
    if fields is None:
        limit = unknown_limit
        fields = tuple(
            (k,)
            for k, v in attachment.items()
            if k != "type" and (isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v)))
        )
    total, truncated = 0, False
    out = attachment
    for path in fields:
        current = out
        for key in path[:-1]:
            current = current.get(key) if isinstance(current, dict) else None
            if current is None:
                break
        if not isinstance(current, dict) or path[-1] not in current:
            continue
        new_value, original, was_cut = _truncate(current[path[-1]], limit)
        total += original
        truncated = truncated or was_cut
        if was_cut:
            out = _set_in(out, path, new_value)
    return out, total, truncated


def _dig(data, path: tuple[str, ...]):
    """Reads a nested key path, returning None at the first non-dict or missing key."""
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _set_in(data: dict, path: tuple[str, ...], value) -> dict:
    """Copy-on-write nested set -- never mutates the caller's record."""
    if len(path) == 1:
        return {**data, path[0]: value}
    return {**data, path[0]: _set_in(data[path[0]], path[1:], value)}


def _cap_text_blocks(record: dict, limit: int) -> tuple[dict, bool]:
    """Truncates `text` blocks in a record's `message.content[]` to `limit`. Returns
    (record, was_truncated); the record is returned unchanged when nothing was cut, so the
    common case allocates nothing.

    Applies to assistant text and to injected (`isMeta`) user text -- see the two config knobs
    for why those are separate policies. String-valued `message.content` is handled too, since
    a user record's content is a bare string as often as it is a block list."""
    message = record.get("message")
    if not isinstance(message, dict) or limit < 0:
        return record, False

    content = message.get("content")
    if isinstance(content, str):
        capped, _original, cut = _truncate(content, limit)
        if not cut:
            return record, False
        return {**record, "message": {**message, "content": capped}}, True

    if not isinstance(content, list):
        return record, False
    new_blocks, truncated = [], False
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            capped, _original, cut = _truncate(block["text"], limit)
            if cut:
                truncated = True
                new_blocks.append({**block, "text": capped})
                continue
        new_blocks.append(block)
    if not truncated:
        return record, False
    return {**record, "message": {**message, "content": new_blocks}}, True


def _scrub_thinking_for_raw(record: dict) -> dict:
    """Returns a copy of an assistant record with every thinking block's `signature` dropped.

    The signature is an opaque blob and by far the largest thing the transcript carries:
    6,547,484 chars across 2,382 blocks in the reference corpus, 3.7x the size of all
    attachment content combined. It cannot be read and it cannot be decoded.

    Its *length*, however, does track thinking volume: over responses whose only visible
    content is a tool call (n=1,418, so no text to confound the count), output_tokens regresses
    on signature length at r=0.87, and the fitted intercept (289) lands on the measured
    output_tokens of the same shape with no thinking block at all (232). Since the transcript
    reports no thinking token count and the plaintext is empty, signature length is the only
    surviving proxy for how much reasoning a response did.

    Nothing is extracted before dropping it today, unlike every other scrub in this module.
    TODO(normalizer): emit `thinking_signature_chars` on assistant_message before the blob is
    dropped -- an int, ~30 bytes, replacing 3.44MB, and the only available handle on
    reasoning cost. Deferred only because it changes the published payload schema.
    The `thinking` field itself stays, so
    thinking_char_count keeps distinguishing "no thinking block" (null) from "thinking block
    present, no text" (0) -- which is what every current Claude Code version emits, the
    plaintext having been stripped before the transcript is written.

    Unconditional, for the same reason as _scrub_tool_result_for_raw: this is not a privacy
    knob an operator should be able to turn off, it is dead weight with no consumer.
    """
    message = record.get("message")
    if not isinstance(message, dict):
        return record
    content = message.get("content")
    if not isinstance(content, list):
        return record
    if not any(
        isinstance(b, dict) and b.get("type") in _THINKING_BLOCK_TYPES and "signature" in b for b in content
    ):
        return record
    return {
        **record,
        "message": {
            **message,
            "content": [
                {k: v for k, v in block.items() if k != "signature"}
                if isinstance(block, dict) and block.get("type") in _THINKING_BLOCK_TYPES
                else block
                for block in content
            ],
        },
    }


def _cap_result_tree(value, content_len: int, other_len: int, intent_len: int, depth: int = 0):
    """Walks a tool result applying two length policies: `content_len` to anything reached
    through a _TOOL_RESULT_CONTENT_KEYS name, `other_len` to every other string. Both use the
    -1/0/N semantics of `_truncate` (-1 whole, 0 empty, N clip), so either can be turned off
    from config alone.

    Structure is never removed -- only string bodies shrink. A dropped body leaves its key in
    place holding "", so a consumer can tell "the vendor sent no content" from "we cut it",
    and a clipped one carries a visible mark rather than reading as a short value.

    The recursion is the point. The scrub this replaces removed `toolUseResult` whole, on the
    stated theory that every key under it was output. That was false -- `gitOperation`,
    `filePath`, `type`, `status`, `agentId` and `resolvedModel` are facts about what happened,
    and they were going out with the file bodies. Walking by key name cuts the body and keeps
    its neighbours: Read's `file` loses `content` but keeps filePath/numLines, and
    `structuredPatch` loses `lines` but keeps each hunk's oldStart/newLines.
    """
    if depth > 8:
        return value
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in _TOOL_RESULT_CONTENT_KEYS:
                limit = content_len
            elif k in _TOOL_RESULT_INTENT_KEYS:
                limit = intent_len
            else:
                limit = other_len
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                # A list of strings is one body split across lines (structuredPatch[].lines),
                # so _truncate measures it whole rather than clipping each line to the limit.
                clipped, _, was_cut = _truncate(v, limit)
                out[k] = (clipped + [_TRUNCATION_MARK]) if was_cut and clipped else clipped
            elif isinstance(v, (dict, list)):
                # Policies are NOT propagated into the subtree: every key decides by its own
                # name. `file` is a container, so what actually gets cut inside it is
                # `file.content` -- while `file.filePath`, `numLines` and `totalLines` take
                # the ordinary policy and survive. Propagating instead emptied filePath on
                # 442 of 456 real Read results, which is the fact the whole walk exists to
                # keep.
                out[k] = _cap_result_tree(v, content_len, other_len, intent_len, depth + 1)
            else:
                clipped, _, was_cut = _truncate(v, limit)
                out[k] = (clipped + _TRUNCATION_MARK) if was_cut and clipped else clipped
        return out
    if isinstance(value, list):
        return [_cap_result_tree(x, content_len, other_len, intent_len, depth + 1) for x in value]
    clipped, _, was_cut = _truncate(value, other_len)
    return (clipped + _TRUNCATION_MARK) if was_cut and clipped else clipped


def _scrub_json_string_result(value, content_len: int, mcp_len: int, intent_len: int):
    """An MCP result arrives as a JSON *string*, not a dict, so key-name walking cannot see
    inside it. Parse it, walk the parsed form, and re-serialize -- the vendor's string shape is
    preserved, because reading it back is a consumer's decision and not this layer's.

    Returns None when the value is not a JSON string, so the caller falls through to the
    ordinary walk. Error text and rejection messages (`Error: Exit code 1`, `User rejected tool
    use` -- 378 of the 383 string results in the corpus) take that path and stay capped by
    `tool_result_len`, since they are output like `stdout`.

    Two lengths, deliberately different from the dict path. Content keys still apply, which is
    what cuts `fileContent` -- a Drive document's whole body, which was shipping 1,000 chars of
    a real doc. Everything else takes `mcp_len`, whole by default: an MCP listing is metadata
    end to end, and clipping it at 1,000 produced a mid-object truncation that was neither
    parseable nor useful.
    """
    if not isinstance(value, str):
        return None
    stripped = value.lstrip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, (dict, list)):
        return None
    return json.dumps(
        _cap_result_tree(parsed, content_len, mcp_len, intent_len), ensure_ascii=False
    )


def _scrub_tool_result_for_raw(
    record: dict, content_len: int, other_len: int, intent_len: int, mcp_len: int
) -> dict:
    """Returns a copy of a tool_result record with file bodies cut to the configured lengths
    and everything else left intact.

    Three places hold a body and all three go through the same two policies: `toolUseResult`;
    the record-level `mcpMeta`, which is the vendor's already-parsed form of an MCP result and
    where Drive/Gmail file metadata lives; and the `tool_result` block's own `content`.

    Config-gated by design. The version this replaces was unconditional, on the principle that
    "tool output is categorically never shipped, so it must not be switchable" -- a rule that
    discriminated on where a value came from rather than what it is, so a 178-byte "File
    created successfully at: /path" was discarded by the same rule that correctly discards a
    208 KB file. Meta is now kept by default; content is what gets cut.

    _merge_tool_result still reads the *unscrubbed* toolUseResult for its derivations before
    this runs, so exit_status/hunks/files on the tool_call payload are unaffected by either
    length.
    """
    scrubbed = dict(record)
    if "toolUseResult" in scrubbed:
        result = scrubbed["toolUseResult"]
        recoded = _scrub_json_string_result(result, content_len, mcp_len, intent_len)
        scrubbed["toolUseResult"] = (
            recoded
            if recoded is not None
            else _cap_result_tree(result, content_len, other_len, intent_len)
        )
    if "mcpMeta" in scrubbed:
        scrubbed["mcpMeta"] = _cap_result_tree(
            scrubbed["mcpMeta"], content_len, other_len, intent_len
        )
    message = scrubbed.get("message")
    if not isinstance(message, dict):
        return scrubbed
    content = message.get("content")
    if not isinstance(content, list):
        return scrubbed
    scrubbed["message"] = {
        **message,
        "content": [
            _cap_result_tree(block, content_len, other_len, intent_len)
            if isinstance(block, dict) and block.get("type") == "tool_result"
            else block
            for block in content
        ],
    }
    return scrubbed


def _scrub_record_for_raw(record: dict) -> dict:
    """Returns a copy of record with every Write/Edit/MultiEdit tool_use block's generated
    content scrubbed -- this is what every event derived from `record` (assistant_message,
    tool_call, file_change) uses as its `raw` passthrough, so the same content that's
    scrubbed from the extracted `input` field can never survive verbatim in `raw`."""
    message = record.get("message")
    if not isinstance(message, dict):
        return record
    content = message.get("content")
    if not isinstance(content, list):
        return record

    new_content = []
    changed = False
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in _FILE_EDIT_TOOL_NAMES:
            new_content.append({**block, "input": _scrub_tool_input(block.get("name"), _as_dict(block.get("input")))})
            changed = True
        else:
            new_content.append(block)
    if not changed:
        return record
    return {**record, "message": {**message, "content": new_content}}


class ClaudeCodeNormalizer:
    vendor = "claude_code"

    def __init__(self, config: NormalizerConfig | None = None) -> None:
        self.config = config or NormalizerConfig()
        self._redaction_patterns = redact.compile_patterns(self.config.redaction_patterns)
        self._mcp_pattern = re.compile(self.config.mcp_name_pattern)
        # Config gives class -> [values] because that reads better in YAML; lookups want the
        # inverse. Inverted once here rather than scanned per event.
        self._class_by_extension = _invert(self.config.artifact_class_by_extension, lower=True)
        self._class_by_basename = _invert(self.config.artifact_class_by_basename)
        self._class_by_mime = _invert(self.config.artifact_class_by_mime, lower=True)

    # -- artifact classification -------------------------------------------------

    def _classify_path(self, path: str) -> tuple[str, str]:
        """Returns (extension, artifact_class) for a local path. Extension is the raw suffix,
        always shipped alongside the label so a taxonomy change can be re-applied to stored
        data without re-ingesting. Basename is tried first for dotfiles and extensionless
        names -- Path(".gitignore").suffix is "", which no suffix map can reach."""
        name = Path(path).name
        extension = Path(path).suffix
        cls = self._class_by_basename.get(name)
        if cls is None and extension:
            cls = self._class_by_extension.get(extension.lower())
        if cls is None and not extension:
            cls = self._class_by_basename.get(name.lstrip("."))
        return extension, cls or "unknown"

    def _classify_mime(self, mime: str) -> str:
        return self._class_by_mime.get(mime.lower(), "unknown")

    def normalize(self, src: SessionSource) -> Iterator[NormalizedSession]:
        yield from self._normalize_stream(
            session_id=src.session_id,
            source=src.source,
            agent_version=src.agent_version,
            records=src.records,
            subagent_streams=src.subagent_streams,
            parent_session_id=None,
        )

    # -- the per-stream driver (recurses into subagents) -----------------------------------

    def _normalize_stream(
        self,
        *,
        session_id: str,
        source: str,
        agent_version: str | None,
        records: list[dict],
        subagent_streams: dict[str, SubagentStream],
        parent_session_id: str | None,
    ) -> Iterator[NormalizedSession]:
        events: list[EventIn] = []
        open_tool_calls: dict[str, dict] = {}
        pending_spawns: list[tuple[str, SubagentStream | None]] = []
        # Per stream, not per normalizer: message.id is unique within a session's transcript,
        # and a subagent's stream is its own session.
        seen_message_ids: set[str] = set()

        session_start_payload: dict | None = None

        for i, record in enumerate(records):
            redacted = redact.redact_record(record, self._redaction_patterns)
            if i == 0:
                session_start_event = self._make_session_start(redacted, parent_session_id)
                events.append(session_start_event)
                # Post-validation payload, not the local dict: pydantic copies on validation,
                # so the backfill below must target the object the event actually holds (same
                # reason _handle_spawn registers spawn_event.payload).
                session_start_payload = session_start_event.payload
            if session_start_payload is not None:
                self._backfill_session_facts(record, session_start_payload)
            self._dispatch(redacted, events, open_tool_calls, subagent_streams, pending_spawns, seen_message_ids)

        yield NormalizedSession(
            vendor=self.vendor,
            session_id=session_id,
            source=source,
            agent_version=agent_version,
            parent_session_id=parent_session_id,
            events=events,
        )

        for _tool_use_id, stream in pending_spawns:
            if stream is None:
                continue  # spawn emitted; the child stream never arrived -- nothing more to yield
            yield from self._normalize_stream(
                session_id=stream.agent_id,
                source=source,
                agent_version=agent_version,
                records=stream.records(),  # materialized only now -- one branch at a time
                subagent_streams=stream.subagent_streams,
                parent_session_id=session_id,
            )

    # -- per-record dispatch -----------------------------------------------------

    def _dispatch(
        self,
        record: dict,
        events: list[EventIn],
        open_tool_calls: dict[str, dict],
        subagent_streams: dict[str, SubagentStream],
        pending_spawns: list[tuple[str, SubagentStream | None]],
        seen_message_ids: set[str],
    ) -> None:
        rtype = record.get("type")
        if rtype == "user":
            self._handle_user(record, events, open_tool_calls)
        elif rtype == "assistant":
            self._handle_assistant(
                record, events, open_tool_calls, subagent_streams, pending_spawns, seen_message_ids
            )
        elif rtype == "system":
            self._handle_system(record, events)
        elif rtype == "attachment":
            events.append(self._make_attachment(record))
        elif rtype == "pr-link":
            events.append(self._make_pr_link(record))
        else:
            # mode / permission-mode / last-prompt / file-history-snapshot / ai-title /
            # custom-title / anything unrecognized -- shipped, never silently dropped.
            events.append(self._passthrough(record))

    def _handle_user(self, record: dict, events: list[EventIn], open_tool_calls: dict[str, dict]) -> None:
        content = _as_dict(record.get("message")).get("content")
        if isinstance(content, str):
            events.append(self._make_user_message(record, content))
            return
        if isinstance(content, list):
            # The raw passthrough every tool_result event below shares -- capped once per
            # record, before any of them is built, so a body can't survive in one sibling's
            # raw while another's is clipped.
            raw_record = _scrub_tool_result_for_raw(
                record,
                self.config.tool_result_content_len,
                self.config.tool_result_len,
                self.config.tool_result_intent_len,
                self.config.tool_result_mcp_len,
            )
            had_tool_result = False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    self._merge_tool_result(record, block, open_tool_calls)
                    events.append(self._make_tool_result(record, block, raw_record))
                    had_tool_result = True
            if had_tool_result:
                return
            text = "".join(
                block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
            )
            if text:
                events.append(self._make_user_message(record, text))
                return
        events.append(self._passthrough(record))

    def _handle_assistant(
        self,
        record: dict,
        events: list[EventIn],
        open_tool_calls: dict[str, dict],
        subagent_streams: dict[str, SubagentStream],
        pending_spawns: list[tuple[str, SubagentStream | None]],
        seen_message_ids: set[str],
    ) -> None:
        message = _as_dict(record.get("message"))
        content = message.get("content")
        if not isinstance(content, list):
            content = []

        # Claude Code splits one API response across several JSONL records -- a thinking-only
        # record, then a text-only one, then a tool_use-only one -- and repeats the *whole*
        # usage block on each. Summing token fields across events therefore over-counts by
        # ~4x (116,755,244 naive vs 30,022,598 deduped, across the reference corpus). The
        # values are not wrong, they are response-scoped and replicated onto block-scoped
        # events; the marker below is what lets a consumer tell.
        #
        # A record with no message.id can't be grouped, so it counts as its own response:
        # failing open preserves the total, where failing closed would silently drop tokens.
        message_id = message.get("id")
        first_block = message_id not in seen_message_ids if message_id else True
        if message_id:
            seen_message_ids.add(message_id)

        # The raw passthrough every event below shares -- scrubbed once per record so a
        # secret can't survive in one sibling event's raw while another's is clean.
        # The thinking scrub is applied unconditionally and the generated-content scrub only
        # when configured; they touch disjoint block types, so the order between them is
        # irrelevant.
        raw_record = _scrub_record_for_raw(record) if self.config.redact_generated_file_content else record
        raw_record = _scrub_thinking_for_raw(raw_record)
        raw_record, text_truncated = _cap_text_blocks(raw_record, self.config.assistant_text_len)

        events.append(
            self._make_assistant_message(record, message, raw_record, message_id, first_block, text_truncated)
        )

        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if name in _SPAWN_TOOL_NAMES:
                self._handle_spawn(
                    record, block, subagent_streams, events, pending_spawns, open_tool_calls, message_id, raw_record
                )
                continue
            tool_call_event = self._make_tool_call(record, block, raw_record, message_id)
            events.append(tool_call_event)
            refs = {"tool_call": tool_call_event.payload, "file_change": None}
            if name in _FILE_EDIT_TOOL_NAMES:
                file_change_event = self._make_file_change(
                    record, block, tool_call_event, raw_record, message_id
                )
                events.append(file_change_event)
                refs["file_change"] = file_change_event.payload
            open_tool_calls[block.get("id")] = refs

    def _handle_system(self, record: dict, events: list[EventIn]) -> None:
        subtype = record.get("subtype")
        if record.get("isApiErrorMessage") or subtype == "api_error":
            events.append(self._make_api_error(record))
        elif subtype == "compact_boundary":
            events.append(self._make_compaction(record))
        else:
            events.append(self._passthrough(record))

    def _handle_spawn(
        self,
        record: dict,
        block: dict,
        subagent_streams: dict[str, SubagentStream],
        events: list[EventIn],
        pending_spawns: list[tuple[str, SubagentStream | None]],
        open_tool_calls: dict[str, dict],
        message_id: str | None,
        raw_record: dict,
    ) -> None:
        tool_use_id = block.get("id")
        tool_input = _as_dict(block.get("input"))
        stream = subagent_streams.get(tool_use_id)
        agent_type = tool_input.get("subagent_type")
        child_session_id = None
        if stream is not None:
            agent_type = agent_type or stream.meta.get("agentType")
            child_session_id = stream.agent_id
        prompt = tool_input.get("prompt")
        payload = {
            "message_id": message_id,  # same sibling guard as tool_call
            "agent_type": agent_type,
            "child_session_id": child_session_id,
            # The directive: what this subagent was actually told to do. `description` is the
            # short label CC shows in the UI ("Implement Task 1: scaffold" -- median 27 chars)
            # and is the natural label for the child's work; `prompt` is the full instruction
            # (median 2,947, max 8,784) and stays uncapped in raw for the same reason typed
            # prompts do -- it is intent, not injected content. Promoted because without it,
            # answering "what was this subagent for" means reaching into raw.
            "description": tool_input.get("description"),
            "prompt_char_count": len(prompt) if isinstance(prompt, str) else None,
            "requested_model": tool_input.get("model"),
            "run_in_background": tool_input.get("run_in_background"),
            # Result-dependent, like tool_call's exit_status: null until the Task's
            # tool_result folds back, and left null forever if it never does.
            "status": None,
            "resolved_model": None,
            "duration_ms": None,
            # raw_record, not record: a spawn shares its enclosing assistant record with any
            # sibling blocks, so it must carry the same scrubbed passthrough every other event
            # derived from that record gets. Passing the original here let generated content
            # and over-cap assistant text bypass their scrubs on this one event type.
            "raw": raw_record,
        }
        spawn_event = EventIn(
            event_type="sub_agent_spawn",
            payload=payload,
            record_id=tool_use_id,
            parent_id=record.get("uuid"),
            event_ts=record.get("timestamp"),
        )
        events.append(spawn_event)
        # Registered like any other tool call so the Task's own tool_result enriches it --
        # that result is where the subagent's outcome lives, and without this it was merged
        # into nothing and dropped. Register spawn_event.payload, not the local `payload`:
        # pydantic copies the dict on validation, so mutating the local one would enrich
        # an object no event holds (same reason _handle_assistant registers
        # tool_call_event.payload).
        open_tool_calls[tool_use_id] = {"tool_call": None, "file_change": None, "spawn": spawn_event.payload}
        pending_spawns.append((tool_use_id, stream))

    # -- event builders ----------------------------------------------------------------------

    @staticmethod
    def _backfill_session_facts(record: dict, session_start_payload: dict) -> None:
        """Lifts session-level facts onto `session_start` as they appear, first wins.

        Runs on every record, not only `ai-title` ones: `attributionAgent` rides on
        assistant records, so a type-gated call would never see it.

        Claude Code names the session once it has enough context -- never at record 0, earliest
        observed at index 8 -- then re-emits the identical string on every subsequent write
        (3-42 times per session in the reference corpus; 10 of 10 sessions carried exactly one
        distinct title, and no subagent stream carried any). It is a session-level fact, so it
        belongs on the session-level event rather than scattered across N passthroughs.

        Backfill rather than read-ahead: `session_start` is synthesized from the first record,
        and the title does not exist yet at that point. This is the same enrich-in-place idiom
        `_merge_tool_result` uses on an already-appended `tool_call`.

        First-wins, not last-wins: no session has been observed changing its title, and if a
        future version does, the first is the one that labelled the work as it happened.

        The `ai-title`/`custom-title` records themselves still ship as `unknown` passthroughs
        -- has no silent ignore-list, and dropping them would be an explicit amendment, not a
        detail.

        Vendor rename, found live (2026-09-12): agent_version 2.1.258 and earlier emit
        `{"type": "ai-title", "aiTitle": ...}`; 2.1.260 emits `{"type": "custom-title",
        "customTitle": ...}` instead -- structurally identical, renamed key on a renamed
        record type. Confirmed against the stack DB: every 2.1.260 session (6/6) had no title
        before this fix, against a title rate above 60% on every prior version. Reading both
        keys costs nothing on either version -- at most one of them is ever present on a given
        record, since a vendor version emits one shape or the other, never both.
        """
        title = record.get("aiTitle") or record.get("customTitle")
        if isinstance(title, str) and title and session_start_payload.get("ai_title") is None:
            session_start_payload["ai_title"] = title
        # cwd / git_branch are session-level but not always on record 0 (Claude Code's first record
        # often lacks them). Same first-wins backfill: take them from the first record that carries
        # them, matching the github normalizer's "first record with cwd" behavior. Without this
        # session_start.cwd stays null and the whole repo_id evidence ladder (repo attribution)
        # collapses to null -- see the GraphFold A/B. The working directory is session-wide, so no
        # sidechain guard is needed (unlike attribution_agent below).
        cwd = record.get("cwd")
        if isinstance(cwd, str) and cwd and session_start_payload.get("cwd") is None:
            session_start_payload["cwd"] = cwd
        git_branch = record.get("gitBranch")
        if isinstance(git_branch, str) and git_branch and session_start_payload.get("git_branch") is None:
            session_start_payload["git_branch"] = git_branch
        # Same idiom, same reason: `attributionAgent` names the agent type a subagent stream is
        # running as, which is a session-level fact, but it is never on record 0 either -- read
        # straight off the first record it left `session_start.attribution_agent` null on all
        # 224 sessions. First-wins; a stream does not change what it is partway through.
        # `isSidechain` guard, and it is load-bearing: a master session's record stream still
        # contains its subagents' records, so an unguarded first-wins backfill labelled 134 of
        # 152 master sessions with the agent type of the first subagent they happened to spawn.
        # Every record carrying attributionAgent is on a sidechain, so on a master the field
        # stays null -- and that null is the discriminator between the two kinds of session.
        agent = record.get("attributionAgent")
        is_subagent_session = session_start_payload.get("parent_session_id") is not None
        if (
            isinstance(agent, str)
            and agent
            # A master's record stream still contains its subagents' records, so a master must
            # only take this from its OWN (non-sidechain) records -- and since every record
            # carrying attributionAgent is on a sidechain, a master ends up null, which is the
            # discriminator between the two kinds of session. Without this, 134 of 152 masters
            # were labelled with the agent type of the first subagent they happened to spawn.
            and (is_subagent_session or not record.get("isSidechain"))
            and session_start_payload.get("attribution_agent") is None
        ):
            session_start_payload["attribution_agent"] = agent

    def _make_session_start(self, record: dict, parent_session_id: str | None = None) -> EventIn:
        payload = {
            "cwd": record.get("cwd"),
            "git_branch": record.get("gitBranch"),
            # Backfilled by the collector engine (engine.py) from the upload manifest's
            # per-file repo_url/repo -- CC's own records never carry a remote, so this stays
            # null unless the manifest resolved one for this file.
            "git_remote": None,
            # Backfilled by _backfill_session_facts once the session names itself; stays null for
            # subagent streams, which never carry one.
            "ai_title": None,
            # TODO(normalizer): derive repo_id from a normalized git remote once CC records
            # expose one -- cwd/gitBranch alone don't give a stable cross-machine fingerprint.
            "repo_id": None,
            # None for a top-level session; set for a subagent -- the only place this
            # linkage reaches the wire, since EventIn/open_session()/EnvelopeHeader have no
            # parent_session_id field of their own and the wire header has no field of its own for it.
            "parent_session_id": parent_session_id,
            # Which agent type this stream is. Only ever set on a subagent -- every one of the
            # 16,659 records carrying it is on a sidechain -- so on a master session it is
            # null, and that null is the discriminator rather than a gap.
            "attribution_agent": record.get("attributionAgent"),
            "raw": record,
        }
        return EventIn(
            event_type="session_start",
            payload=payload,
            record_id=None,
            parent_id=None,
            event_ts=record.get("timestamp"),
        )

    @staticmethod
    def _is_injected_user_record(record: dict) -> bool:
        """True only when Claude Code positively marks the record as injected rather than
        typed. Four independent markers, because no single one covers the population:

            isMeta            skill bodies (largest observed: 151,597 chars)
            isCompactSummary  the post-compaction conversation summary (~30,000)
            origin.kind != human   task-notification, and whatever CC adds next
            promptSource == system

        **Fails open on purpose.** A record carrying none of these is treated as a prompt and
        ships whole. That population is not noise -- it is subagent task prompts ("You are
        fixing the five Important findings from..."), 170 of them, median 2,503 chars, max
        8,784. Those are the instruction given to a subagent: pure intent, and exactly what a
        cap must not eat. Inverting this to "cap unless origin.kind == human" would clip every
        one of them.
        """
        if record.get("isMeta") or record.get("isCompactSummary"):
            return True
        if record.get("promptSource") == "system":
            return True
        kind = (record.get("origin") or {}).get("kind")
        return bool(kind) and kind != "human"

    def _make_user_message(self, record: dict, text: str) -> EventIn:
        """A user record with real text content.

        Two populations share this event type and only one is a prompt. Prompts -- typed, or
        handed to a subagent -- are the session's primary intent signal and ship whole.
        Injected records are capped by `user_meta_text_len`; see _is_injected_user_record for
        how they are told apart. `char_count` is measured before the cap, so the size signal
        survives either way."""
        is_injected = self._is_injected_user_record(record)
        raw_record, text_truncated = (
            _cap_text_blocks(record, self.config.user_meta_text_len) if is_injected else (record, False)
        )
        emitted_text = text[: self.config.user_meta_text_len] if text_truncated else text
        slash_command = text.split(None, 1)[0] if text.startswith("/") else None
        payload = {
            "text": emitted_text,
            "char_count": len(text),
            "is_injected": is_injected,
            "text_truncated": text_truncated,
            "slash_command": slash_command,
            # The vendor now stamps `permissionMode` on the prompt record itself, so no
            # correlation with neighbouring `mode` records is needed -- the earlier TODO here
            # assumed one was. Measured over 1,277 records carrying it: auto 990, plan 165,
            # acceptEdits 96, default 26, and single-valued within all 424 turns that have
            # one, so it is a property of the turn and not of individual records.
            "permission_mode": record.get("permissionMode"),
            "plan_mode": (record["permissionMode"] == "plan") if "permissionMode" in record else None,
            # raw_record, not record: capping `text` alone would leave the full body in the
            # passthrough, which is where the 151,597-char skill body actually rides.
            "raw": raw_record,
        }
        return EventIn(
            event_type="user_message",
            payload=payload,
            record_id=record.get("uuid"),
            parent_id=record.get("parentUuid"),
            event_ts=record.get("timestamp"),
        )

    def _make_assistant_message(
        self,
        record: dict,
        message: dict,
        raw_record: dict,
        message_id: str | None,
        first_block: bool,
        text_truncated: bool,
    ) -> EventIn:
        usage = _as_dict(message.get("usage"))
        content = message.get("content")
        if not isinstance(content, list):
            content = []
        content_kind, block_types = _classify_content(content)
        text_chars = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        thinking_chars = "".join(
            b.get("thinking", "") for b in content if isinstance(b, dict) and b.get("type") == "thinking"
        )
        payload: dict = {
            # Groups the events Claude Code split out of one API response. Vendor id,
            # so it lives on the payload rather than the vendor-neutral header.
            "message_id": message_id,
            # The dedup selector: sum a token field only where this is true and you get the
            # response-level total. Additive by design -- the usage values below are left in
            # place on every event, so nothing consuming them today breaks.
            "first_block_of_message": first_block,
            # These numbers describe the whole response, not this event's blocks. Not called
            # "duplicate": they are correct, just scoped wider than the event carrying them.
            "usage_scope": "response",
            "content_kind": content_kind,
            "block_types": block_types,
            # Measured on `message`, which is the *unscrubbed* record -- so the size signal
            # survives the cap applied to raw_record above.
            "text_char_count": len(text_chars) if "text" in block_types else None,
            "text_truncated": text_truncated,
            # Explicit "the model reasoned here" flag. With the plaintext stripped by Claude
            # Code before it writes the transcript and the signature dropped on the way into
            # raw, a thinking block's remaining signal is exactly this boolean plus its
            # position -- so surface it rather than making every consumer inspect
            # block_types. Covers redacted_thinking too, which content_kind already buckets
            # as thinking. thinking_char_count stays alongside it and is not redundant: null
            # means no thinking block, 0 means one is present with no text (what every
            # current CC version emits), and it would light up again if that ever changes.
            "has_thinking": bool(_THINKING_BLOCK_TYPES.intersection(block_types)),
            "thinking_char_count": len(thinking_chars) if "thinking" in block_types else None,
            # The vendor's own per-call request id (`req_...`, distinct from `message_id`'s
            # `msg_...`) -- present on essentially every turn, not just `_make_api_error`'s
            # error records, which is the only place this was previously being read. This is
            # the exact join key `config/otel/claude_code.yaml` maps OTel's own `request_id`
            # attribute onto ("the join key to JSONL"), and what
            # `otel.jsonl_requests_for_session` requires to price a request from JSONL alone
            # (`worker/otel_trueup.py::_join_requests`'s JSONL-only branch is fully built and
            # already does this correctly -- it simply never received any rows before this
            # field existed here). Without it, no session could ever get a JSONL-only cost
            # estimate, OTel present or not -- this was a bug, not OTel taking deliberate
            # precedence over history.
            "provider_request_id": record.get("requestId"),
            "model": message.get("model"),
            "effort": record.get("effort"),
            "stop_reason": message.get("stop_reason"),
            # Who authored this record: the skill, plugin and agent whose instructions were in
            # force. Record-level, not turn-level -- measured across 43,034 events, none of
            # these ever appears on a record carrying a promptId, so a turn cannot hold them.
            # `attribution_agent` is 100% sidechain (16,659/16,659): it names the agent type of
            # a subagent stream, which is why it also lands on session_start below.
            "attribution_skill": record.get("attributionSkill"),
            "attribution_plugin": record.get("attributionPlugin"),
            "attribution_agent": record.get("attributionAgent"),
            "duration_ms": None,  # CC carries tool durations, not turn duration
            # Not exposed by CC: `usage` has no reasoning subtotal, and thinking is billed
            # inside output_tokens. It is recoverable by subtracting estimated_visible_tokens
            # below, but that spans the records of one message_id, so the graph layer derives
            # it -- this stays null rather than holding a per-record partial answer.
            "reasoning_tokens": None,
            "cache_miss_tokens": None,  # v1 gap -- not exposed by CC
        }
        # Counted on `message` (unscrubbed) for the reason given above: Write/Edit arguments
        # are generated output tokens and the generated-content scrub removes them, so
        # counting raw_record would undercount the largest responses by exactly their payload.
        if self.config.count_estimated_visible_tokens:
            payload.update(count_visible_output(content, self.config.tokenizer_encoding))
        else:
            payload.update(
                estimated_visible_tokens=None, output_tokenizer=None,
                tool_arg_chars=None, tool_call_count=None,
            )
        for target_field, usage_key in self.config.usage_field_map.items():
            payload[target_field] = usage.get(usage_key)

        cache_creation = usage.get("cache_creation")
        if isinstance(cache_creation, dict):
            detail = []
            if "ephemeral_5m_input_tokens" in cache_creation:
                detail.append({"ttl_s": 300, "tokens": cache_creation["ephemeral_5m_input_tokens"]})
            if "ephemeral_1h_input_tokens" in cache_creation:
                detail.append({"ttl_s": 3600, "tokens": cache_creation["ephemeral_1h_input_tokens"]})
            if detail:
                payload["cache_write_detail"] = detail

        server_tool_use = usage.get("server_tool_use")
        if isinstance(server_tool_use, dict) and any(server_tool_use.values()):
            payload["server_tool_use"] = server_tool_use

        payload["raw"] = raw_record
        return EventIn(
            event_type="assistant_message",
            payload=payload,
            record_id=record.get("uuid"),
            parent_id=record.get("parentUuid"),
            event_ts=record.get("timestamp"),
        )

    def _make_tool_call(self, record: dict, block: dict, raw_record: dict, message_id: str | None) -> EventIn:
        name = block.get("name", "")
        tool_input = _as_dict(block.get("input"))
        # input_chars reflects the true generated size even when the content itself is
        # scrubbed below -- code-volume signal survives without the code riding the wire.
        input_chars = len(json.dumps(tool_input))
        emitted_input = (
            _scrub_tool_input(name, tool_input) if self.config.redact_generated_file_content else tool_input
        )
        payload: dict = {
            # Not about tokens -- tool_call carries no usage. This is the sibling guard: calls
            # sharing a message_id were emitted together in one response, with no ordering and
            # no dependency between them. Real sequencing only exists *across* responses, so a
            # consumer drawing edges among same-message_id calls invents dependencies that
            # never existed.
            "message_id": message_id,
            "tool_name": name,
            "input": emitted_input,
            "input_chars": input_chars,
            "bytes_read": None,
            "exit_status": None,
            "duration_ms": None,
            "raw": raw_record,
        }
        mcp_match = self._mcp_pattern.match(name)
        if mcp_match:
            payload["mcp_server"] = mcp_match.group("server")
            payload["mcp_tool"] = mcp_match.group("tool")
        files = self._extract_files(name, tool_input)
        if files:
            payload["files"] = files
        return EventIn(
            event_type="tool_call",
            payload=payload,
            record_id=block.get("id"),
            parent_id=record.get("uuid"),
            event_ts=record.get("timestamp"),
        )

    def _extract_files(self, name: str, tool_input: dict) -> list[dict]:
        path = tool_input.get("file_path")
        if not path:
            return []
        if name == "Read":
            operation = "read"
        elif name == "Write":
            operation = "create"
        elif name in ("Edit", "MultiEdit"):
            operation = "edit"
        else:
            return []
        extension, artifact_class = self._classify_path(path)
        return [
            {
                "path": path,
                "operation": operation,
                "extension": extension,
                "artifact_class": artifact_class,
                "mime_type": None,  # local file -- set only for remote artifacts
            }
        ]

    def _make_file_change(
        self, record: dict, block: dict, tool_call_event: EventIn, raw_record: dict, message_id: str | None
    ) -> EventIn:
        tool_input = _as_dict(block.get("input"))
        path = tool_input.get("file_path", "")
        operation = "create" if block.get("name") == "Write" else "edit"
        extension, artifact_class = self._classify_path(path)
        payload = {
            "message_id": message_id,
            "path": path,
            "extension": extension,
            "artifact_class": artifact_class,
            "operation": operation,
            "derived_from_event_id": tool_call_event.record_id,
            "raw": raw_record,
        }
        return EventIn(
            event_type="file_change",
            payload=payload,
            record_id=None,  # derived, no natural vendor id -> Tier-2 fallback
            parent_id=record.get("uuid"),
            event_ts=record.get("timestamp"),
        )

    def _make_attachment(self, record: dict) -> EventIn:
        """Context injected outside the message flow -- an @-mentioned file, an IDE-open file,
        the skill listing, hook output, a directory listing.

        Promoted out of `unknown` because these carry the largest bodies in the transcript and
        because "which skill / which file was in context here" is a question worth answering
        without digging through raw. The body is capped per config; everything that makes the
        record queryable survives regardless of the cap -- the type, the path and its artifact
        class, and the pre-truncation size."""
        attachment = _as_dict(record.get("attachment"))
        atype = attachment.get("type")
        limit = self.config.attachment_len_by_type.get(atype, self.config.attachment_len)
        capped, content_chars, truncated = _cap_attachment(
            attachment, limit, self.config.attachment_len_unknown_type
        )

        payload: dict = {
            "attachment_type": atype,
            # Startup context effectively becomes the system prompt, and is not otherwise
            # distinguishable from a mid-session @-mention without reading record position.
            # CLAUDE.md, measured, arrives via the SessionStart hook -- never as a `file`.
            "is_startup_context": attachment.get("hookEvent") == "SessionStart"
            or bool(attachment.get("isInitial")),
            "content_chars": content_chars,
            "content_truncated": truncated,
            "extension": None,
            "artifact_class": None,
        }
        path = next((attachment[f] for f in _ATTACHMENT_PATH_FIELDS if isinstance(attachment.get(f), str)), None)
        if path:
            payload["path"] = path
            payload["extension"], payload["artifact_class"] = self._classify_path(path)
        for vendor_field, emitted in _ATTACHMENT_METADATA_FIELDS.get(atype, ()):
            if vendor_field in attachment:
                payload[emitted] = attachment[vendor_field]
        total_lines = _dig(attachment, _ATTACHMENT_TOTAL_LINES_PATH)
        if isinstance(total_lines, int):
            payload["total_lines"] = total_lines
        payload["raw"] = {**record, "attachment": capped} if capped is not attachment else record
        return EventIn(
            event_type="attachment",
            payload=payload,
            record_id=record.get("uuid"),
            parent_id=record.get("parentUuid"),
            event_ts=record.get("timestamp"),
        )

    def _make_tool_result(self, record: dict, block: dict, raw_record: dict) -> EventIn:
        """The tool_result record as its own event, alongside the enrich-in-place merge into
        its tool_call. Both are needed: the merge gives tool_call its result-dependent
        fields, this gives the causal graph the observation node -- with its own timestamp,
        which the merge has nowhere to put, and which is the only way to separate tool
        execution time from permission-wait.

        record_id is the record's own `uuid`, not the tool_use id, and that is load-bearing:
        the next assistant record's `parentUuid` points here, so anything else leaves a
        dangling parent and breaks the uuid/parentUuid spine. This departs from the tool_use
        fan-out rule in the module docstring, and safely -- a tool_result record carries
        exactly one tool_result block (4996/4996 across the reference corpus), so there is no
        fan-out to collide. `tool_use_id` still reaches consumers on the payload.

        `tool_denial_kind` is set when the call was blocked rather than run (permission-rule,
        user-rejected, automode-unavailable, interrupted). Those records carry no
        toolUseResult at all, so the merge leaves the tool_call's exit_status/duration_ms
        null -- this event is the only place a rejection is visible.
        """
        payload = {
            "tool_use_id": block.get("tool_use_id"),
            "source_tool_assistant_uuid": record.get("sourceToolAssistantUUID"),
            "is_error": bool(block.get("is_error")),
            "tool_denial_kind": record.get("toolDenialKind"),
            "raw": raw_record,
        }
        return EventIn(
            event_type="tool_result",
            payload=payload,
            record_id=record.get("uuid"),
            parent_id=record.get("parentUuid"),
            event_ts=record.get("timestamp"),
        )

    def _make_api_error(self, record: dict) -> EventIn:
        status = record.get("status")
        payload = {
            "error_class": _classify_error(status),
            "http_status": status,
            "retryable": bool(record.get("retryable", False)),
            "retry_count": record.get("retryCount"),
            "retry_after_ms": record.get("retryAfterMs"),
            "provider_request_id": record.get("requestId"),
            "message": record.get("message"),
            "raw": record,
        }
        return EventIn(
            event_type="api_error",
            payload=payload,
            record_id=record.get("uuid"),
            parent_id=record.get("parentUuid"),
            event_ts=record.get("timestamp"),
        )

    def _make_compaction(self, record: dict) -> EventIn:
        meta = _as_dict(record.get("compactMetadata"))
        before = meta.get("preTokens")
        after = meta.get("postTokens")
        reclaimed = before - after if before is not None and after is not None else None
        payload = {
            "trigger": meta.get("trigger"),
            "tokens_before": before,
            "tokens_after": after,
            "tokens_reclaimed": reclaimed,
            "duration_ms": meta.get("durationMs"),
            "raw": record,
        }
        return EventIn(
            event_type="compaction",
            payload=payload,
            record_id=record.get("uuid"),
            parent_id=record.get("parentUuid"),
            event_ts=record.get("timestamp"),
        )

    @staticmethod
    def _passthrough(record: dict) -> EventIn:
        return EventIn(
            event_type="unknown",
            payload={"raw": record},
            record_id=record.get("uuid"),  # None for the UI/meta types that lack one
            parent_id=record.get("parentUuid"),
            event_ts=record.get("timestamp"),
        )

    @staticmethod
    def _make_pr_link(record: dict) -> EventIn:
        """A ``pr-link`` record -- Claude Code's own note of a PR it opened
        (``{prNumber, prUrl, prRepository, timestamp}``). ``prRepository`` is a fully-qualified
        ``owner/name``, the highest-provenance repo identity in the transcript (the top rung of the
        repo_id evidence ladder). It carries no ``uuid``/``parentUuid`` -- not part of the
        conversation tree -- so record_id/parent_id are null. Ported from the github-acquisition
        normalizer; the causal-graph builder leaves it out-of-tree, GraphFold reads it for pr_list."""
        return EventIn(
            event_type="pr_link",
            payload={
                "pr_number": record.get("prNumber"),
                "pr_url": record.get("prUrl"),
                "pr_repository": record.get("prRepository"),
                "raw": record,
            },
            record_id=None,
            parent_id=None,
            event_ts=record.get("timestamp"),
        )

    # -- tool_use / tool_result pairing ------------------------------------------------

    def _remote_artifacts(self, tool_use_result: dict) -> list[dict]:
        """Types the artifacts an MCP tool touched, from mime types in its result.

        Needed because a remote artifact -- a Google Doc, Sheet, or Slide deck -- never
        produces a file_change: only Edit/Write/MultiEdit do. Without this, work delivered
        outside the repo is invisible to any "what did this session produce" question.

        Extracted here, before _scrub_tool_result_for_raw removes toolUseResult, so this is
        the only copy that survives -- the same read-before-scrub ordering that exit_status
        and hunks already rely on. Deliberately narrow: mime type, opaque id, and size. The
        sibling `title`, `owner` (an email address), and `viewUrl` are content and identity,
        and go with the rest of toolUseResult."""
        entries = tool_use_result.get("files")
        if not isinstance(entries, list):
            entries = [tool_use_result]
        artifacts = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            mime = entry.get("mimeType")
            if not isinstance(mime, str):
                continue
            size = entry.get("fileSize")
            artifacts.append(
                {
                    "path": None,  # remote: no filesystem path
                    "operation": "read",
                    "extension": None,
                    "artifact_class": self._classify_mime(mime),
                    "mime_type": mime,
                    "remote_id": entry.get("id"),
                    "bytes": int(size) if isinstance(size, str) and size.isdigit() else size,
                }
            )
        return artifacts

    def _merge_tool_result(self, record: dict, block: dict, open_tool_calls: dict[str, dict]) -> None:
        tool_use_id = block.get("tool_use_id")
        refs = open_tool_calls.pop(tool_use_id, None)
        if refs is None:
            return  # result for a tool_use we never saw open -- nothing to enrich

        tool_use_result = _as_dict(record.get("toolUseResult"))

        spawn_payload = refs.get("spawn")
        if spawn_payload is not None:
            # A Task result folds the subagent's outcome back to the parent. `prompt` is also
            # in there and is user content -- it stays out, dropped with the rest of
            # toolUseResult by _scrub_tool_result_for_raw.
            spawn_payload["status"] = tool_use_result.get("status")
            spawn_payload["resolved_model"] = tool_use_result.get("resolvedModel")
            spawn_payload["duration_ms"] = tool_use_result.get("durationMs")
            return

        exit_status = tool_use_result.get("exitCode")
        if exit_status is None:
            exit_status = 1 if block.get("is_error") else 0

        tool_call_payload = refs["tool_call"]
        tool_call_payload["exit_status"] = exit_status
        tool_call_payload["duration_ms"] = tool_use_result.get("durationMs")
        result_content = block.get("content")
        if isinstance(result_content, str):
            tool_call_payload["bytes_read"] = len(result_content.encode("utf-8"))

        # Remote artifacts (MCP): the call carried no file_path, so there is no files[] to
        # enrich -- the result is where they first appear.
        if not tool_call_payload.get("files"):
            remote = self._remote_artifacts(tool_use_result)
            if remote:
                tool_call_payload["files"] = remote

        # `linesAdded`/`linesRemoved` are NOT read: the vendor does not set them at this level
        # on any Edit/Write result (0 of 1,690 checked). They exist only inside `toolStats`,
        # which is Agent-only and per-run. The hunks are the per-call signal.
        structured_patch = tool_use_result.get("structuredPatch")
        # Exact +/- line counts, computed before the diff body is dropped (the graph's hunk sizes
        # include context and cannot reproduce them). Carried onto the file entry for session_info.files.
        lines_added, lines_removed = _lines_added_removed(tool_use_result)

        if tool_call_payload.get("files"):
            for f in tool_call_payload["files"]:
                if isinstance(structured_patch, list):
                    f["hunks"] = _hunks_from_structured_patch(structured_patch)
                if lines_added is not None:
                    f["lines_added"] = lines_added
                if lines_removed is not None:
                    f["lines_removed"] = lines_removed

        file_change_payload = refs["file_change"]
        if file_change_payload is not None:
            if structured_patch is not None:
                file_change_payload["hunks"] = _hunks_from_structured_patch(structured_patch)
            if lines_added is not None:
                file_change_payload["lines_added"] = lines_added
            if lines_removed is not None:
                file_change_payload["lines_removed"] = lines_removed
