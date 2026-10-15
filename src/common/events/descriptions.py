"""What every published field means.

The schema's *types* are derived from real normalizer output and cannot drift into fiction.
Its *meaning* cannot be derived from anything -- no amount of walking a payload reveals that
`bytes_read` is UTF-8 bytes of the tool result rather than of the file on disk. So the prose
is authored here and merged into the derived shape at publish time: types stay machine-true,
descriptions stay human-true, and one file holds all of the latter.

**A field without an entry here cannot be published.** `cli_publish_payload` refuses to write
and names the offenders, and the drift guard fails the same way. That is deliberate: an
undocumented field in a published contract is worse than no contract, because it looks
authoritative. Adding a payload field therefore means adding a line here, in the same change.

Keys are the same paths `derive_shape` produces: `foo.bar` for nesting, `foo[]` for a list's
elements. Write what the field *is* and what a reader would otherwise get wrong -- not a
restatement of its name.
"""
from __future__ import annotations

__all__ = ["EVENT_DOCS", "FIELD_DOCS", "OUTER_DOCS", "missing_descriptions"]

# The vendor-neutral header every event carries.
OUTER_DOCS = {
    "event_type": "Which of the event types below this is; selects how `payload` reads.",
    "record_id": "This event's own id, from the vendor record (`uuid`). Null for derived events that had no record of their own.",
    "parent_id": "The id of the record this one answered or followed (`parentUuid`), forming the session DAG. Null at a session root.",
    "event_ts": "Vendor timestamp, ISO-8601 UTC. Null when the record carried none.",
}

EVENT_DOCS = {
    "api_error": "A provider-side failure returned instead of a response.",
    "assistant_message": "One assistant record. A single API response is split across several of these; see `message_id`.",
    "attachment": "Context injected outside the message flow: @-mentioned files, IDE state, skill listings, hook output.",
    "compaction": "The session transcript was summarized to reclaim context.",
    "file_change": "A file the session created, edited or deleted. Derived from a tool call, not a vendor record.",
    "pr_link": "Claude Code's own note of a pull request it opened, carrying the fully-qualified repository and PR number/url.",
    "session_start": "Opens a session. One per transcript, carrying session-wide identity.",
    "sub_agent_spawn": "An Agent/Task tool call that started a child session with its own transcript.",
    "tool_call": "A tool invocation the model requested, enriched in place with its result.",
    "tool_result": "The result record paired to a tool call; carries the outcome, never the content.",
    "unknown": "A record type the normalizer does not map. Kept whole in `raw` so nothing is lost.",
    "user_message": "A turn from the user side -- either typed by a person or injected by the harness.",
}

_RAW = "The complete original vendor record, after scrubbing. The losslessness backstop: anything not promoted to a field is still here."

FIELD_DOCS: dict[str, dict[str, str]] = {
    "api_error": {
        "error_class": "Bucketed failure kind derived from the HTTP status: `rate_limit` (429), `overloaded` (529), and so on.",
        "http_status": "HTTP status the provider returned.",
        "message": "Provider error text, verbatim.",
        "provider_request_id": "The provider's request id, for correlating with their logs.",
        "raw": _RAW,
        "retry_after_ms": "Backoff the provider asked for, in milliseconds.",
        "retry_count": "How many attempts had already been made when this error was recorded.",
        "retryable": "Whether the provider marked the failure as worth retrying.",
    },
    "assistant_message": {
        "block_types": "Ordered list of this record's content block types, exactly as emitted. Finer-grained than `content_kind` -- use it to tell `redacted_thinking` from `thinking`.",
        "block_types[]": "One block type: `text`, `thinking`, `redacted_thinking`, or `tool_use`.",
        "cache_miss_tokens": "Always null: Claude Code's usage object does not expose it.",
        "cache_read_tokens": "Tokens served from cache. Response-scoped -- see `usage_scope`.",
        "cache_write_detail": "Per-TTL breakdown of what was written to cache. Absent when the vendor reported no breakdown.",
        "cache_write_detail[]": "One TTL bucket.",
        "cache_write_detail[].tokens": "Tokens written to cache at this TTL.",
        "cache_write_detail[].ttl_s": "Cache lifetime in seconds: 300 (5m) or 3600 (1h).",
        "cache_write_tokens": "Total tokens written to cache, all TTLs. Response-scoped -- see `usage_scope`.",
        "content_kind": "Coarse shape of this record: `text`, `thinking`, `tool_use`, `mixed`, or `empty`. Lets a consumer tell a reasoning step from a final answer without parsing `raw`.",
        "duration_ms": "Always null on an assistant record: Claude Code times tool calls, not turns.",
        "estimated_visible_tokens": "Tokenizer count of this record's visible output -- reply text plus tool names and arguments. Thinking blocks are excluded, and a thinking-only record therefore reads 0. **An estimate, not Claude's own count**: produced by `output_tokenizer` (OpenAI's vocabulary), which runs about 1.13x under Claude on Sonnet 4.6 and 1.52x under on the Claude 5 family. Stored raw with no multiplier applied so the ratio can be re-applied later. Counted before redaction, so a scrubbed Write body still counts. See docs/design/notes/claude_token_usage.md.",
        "first_block_of_message": "True on exactly one event per API response. The dedup selector: sum a token field only where this is true and you get the response total, since usage is repeated on every record of the response.",
        "has_thinking": "Whether the model reasoned in this record. With the plaintext stripped by the vendor and the signature dropped on the way into `raw`, this flag plus the record's position is the remaining signal.",
        "input_tokens": "Tokens sent. Response-scoped -- see `usage_scope`.",
        "message_id": "Groups the several records the vendor split one API response into. The key for any response-level aggregation.",
        "model": "Model that produced this response.",
        "output_tokenizer": "Which tokenizer produced `estimated_visible_tokens`, e.g. `o200k_base`. Ships per event because a token count means nothing without the vocabulary behind it, and that vocabulary will eventually change.",
        "output_tokens": "Tokens produced -- **including thinking**, which is billed here and never broken out. Response-scoped; see `usage_scope`. Subtract the visible estimate to recover the thinking share.",
        "provider_request_id": "The vendor's own per-call request id (`req_...`, distinct from `message_id`'s `msg_...`), present on essentially every response. The join key to OTel's own `request_id` attribute (`config/otel/claude_code.yaml`) -- what lets a JSONL-only session be priced from tokens alone via the request-level join (`worker/otel_trueup.py`), independent of whether OTel telemetry is also running.",
        "raw": _RAW,
        "reasoning_tokens": "Always null here. Not unknowable -- it is recovered by summing `estimated_visible_tokens` across a `message_id` and subtracting from `output_tokens` -- but that spans records, so the graph layer derives it rather than this event holding a partial answer.",
        "attribution_agent": (
            "The agent type whose instructions produced this record — `general-purpose`, or a named subagent. Only ever set on a subagent sidechain, so null means the main session."
        ),
        "attribution_plugin": "The plugin supplying the skill in force, when one was.",
        "attribution_skill": (
            "The skill whose instructions were in force for this record, `plugin:skill` when it came from a plugin. This is what answers \"which skills did this work actually use\" — distinct from the `skill_listing` attachment, which only says which skills were offered in context."
        ),
        "effort": "The reasoning-effort setting for this response, when the vendor recorded one.",
        "stop_reason": "Why generation stopped: `tool_use`, `end_turn`, `stop_sequence`. **Null means this record is not the final one** of its response and its usage numbers are not yet settled.",
        "text_char_count": "Length of the concatenated text blocks, measured on the unscrubbed record so volume survives the cap. Null when there is no text block; 0 means a text block that is empty.",
        "text_truncated": "Whether `assistant_text_len` clipped the text stored in `raw`. `text_char_count` still reports the full original length.",
        "thinking_char_count": "Length of the thinking plaintext. Null means no thinking block; 0 means one is present but empty, which is what every observed Claude Code version writes. The distinction is the point.",
        "tool_arg_chars": "Serialized length of this record's tool names and arguments, unscrubbed. The character-level companion to `estimated_visible_tokens`.",
        "tool_call_count": "Tool-use blocks in **this record**, not the whole response. Needed because each call costs a fixed block of output tokens beyond its arguments.",
        "usage_scope": "Always `response`: the token fields on this event describe the whole API response, not this record's blocks. They are correct, just scoped wider than the event carrying them.",
    },
    "attachment": {
        "added_names": "Tool names newly made available to the model.",
        "added_names[]": "One tool name.",
        "added_types": "Agent types newly made available.",
        "added_types[]": "One agent type.",
        "artifact_class": "What kind of thing this is -- `code`, `documentation`, `config` -- from the path or MIME type. Null when unclassified.",
        "attachment_type": "The vendor's own label for this injection, e.g. `skill_listing`, `file`, `hook_success`.",
        "content_chars": "Length of the injected body before any cap, so a clipped body is never mistaken for a short one.",
        "content_truncated": "Whether the body in `raw` was clipped by the attachment cap.",
        "display_path": "Path as shown to the model, which may be relative or abbreviated.",
        "duration_ms": "How long the hook took.",
        "exit_code": "Hook process exit code.",
        "extension": "File extension, kept raw alongside `artifact_class` so a taxonomy change can be re-applied without re-ingesting.",
        "hook_event": "Which lifecycle point fired the hook, e.g. `SessionStart`.",
        "hook_name": "Name of the hook that produced this.",
        "is_startup_context": "Whether this arrived as part of session startup rather than mid-conversation. CLAUDE.md arrives this way, via the SessionStart hook.",
        "item_count": "Number of items in a reminder listing.",
        "line_end": "Last line of the selected range.",
        "line_start": "First line of the selected range.",
        "new_date": "The date the session rolled over to.",
        "path": "Filesystem path of the attached file.",
        "raw": _RAW,
        "removed_names": "Tool names withdrawn from the model.",
        "removed_types": "Agent types withdrawn.",
        "skill_count": "How many skills were listed.",
        "skill_names": "Which skills were in context. Kept even when the listing body is dropped, because this is the part worth querying.",
        "skill_names[]": "One skill name.",
        "total_lines": "Line count of the attached file.",
    },
    "compaction": {
        "duration_ms": "How long compaction took.",
        "raw": _RAW,
        "tokens_after": "Context size once summarized.",
        "tokens_before": "Context size that triggered it.",
        "tokens_reclaimed": "Difference between before and after.",
        "trigger": "What caused it: automatic threshold or an explicit request.",
    },
    "file_change": {
        "artifact_class": "What kind of file this is -- `code`, `documentation`, `config` -- from its path.",
        "derived_from_event_id": "The `tool_call` event this was derived from. This event has no vendor record of its own.",
        "extension": "File extension, kept raw alongside `artifact_class`.",
        "hunks": (
            "Edited regions. The only per-call signal of edit size -- the vendor sets no "
            "line-count field on an Edit/Write result (0 of 1,690 checked)."
        ),
        "hunks[]": "One contiguous edited region.",
        "hunks[].len": "Lines the region occupies AFTER the edit.",
        "hunks[].old_len": (
            "Lines it occupied before. `len - old_len` is the net change; without it a hunk "
            "says where an edit landed but not how much it moved."
        ),
        "hunks[].start": "First line of the region, after the edit.",
        "lines_added": "Exact count of added (`+`) lines, from the structuredPatch diff body before it is dropped. The gross code-churn signal the hunk sizes cannot give (they include unchanged context).",
        "lines_removed": "Exact count of removed (`-`) lines, from the structuredPatch diff body.",
        "message_id": "The API response whose tool call produced this change.",
        "operation": "What happened to the file: create, edit or delete.",
        "path": "Filesystem path of the changed file.",
        "raw": _RAW,
    },
    "pr_link": {
        "pr_number": "The pull request number.",
        "pr_repository": "The fully-qualified `owner/name` the PR was opened against -- the highest-provenance repository identity in the transcript.",
        "pr_url": "The pull request's web URL.",
        "raw": _RAW,
    },
    "session_start": {
        "ai_title": "The vendor's own one-line summary of what the session is about. Repeated on many records; captured once here.",
        "cwd": "Working directory the session ran in.",
        "git_branch": "Branch checked out at session start.",
        "git_remote": "The `cwd`'s real git remote, from the upload manifest's per-file `repo_url` (preferred) or `repo` -- captured by the uploader client via a real `git remote get-url origin` run at the session's actual working directory, and matched onto this session by the manifest entry the collector read it from (not a separate cwd-keyed lookup). Null when the manifest carried no repo for this file, `cwd` wasn't a git repo, or it had no `origin`. The evidence-ladder's `remote` tier prefers this over anything scraped from a transcript's own tool calls (`worker/graph_fold.py::repo_info`).",
        "attribution_agent": (
            "The agent type this stream is running as, for a subagent session. Null on a master session — every record carrying it is on a sidechain, so the null is the discriminator rather than a gap."
        ),
        "parent_session_id": "The session that spawned this one, for subagent transcripts. Null for a top-level session.",
        "raw": _RAW,
        "repo_id": "Always null: no stable repository identity is derivable yet.",
    },
    "sub_agent_spawn": {
        "agent_type": "Which agent was asked for, e.g. `Explore`, `general-purpose`.",
        "child_session_id": "The child's own transcript id, linking to its events.",
        "description": "Short label for the delegated task.",
        "duration_ms": "How long the child ran. Null while still running or if never paired.",
        "message_id": "The API response that requested the spawn.",
        "prompt_char_count": "Length of the instruction given to the child, measured unscrubbed.",
        "raw": _RAW,
        "requested_model": "Model asked for. May differ from what actually ran.",
        "resolved_model": "Model the child actually used. Children routinely differ from the parent, which is why their cost cannot be assumed.",
        "run_in_background": "Whether the parent continued without waiting.",
        "status": "Outcome once the child finished. Null while pending.",
    },
    "tool_call": {
        "bytes_read": "UTF-8 size of the tool's **result**, not of any file on disk. Present once the result is paired.",
        "duration_ms": "How long the tool took. Frequently null even on a matched result -- only present when the vendor reported it.",
        "exit_status": "Process exit status for tools that run commands.",
        "files": "Files this call touched, one entry each.",
        "files[]": "One file touched by the call.",
        "files[].artifact_class": "What kind of file, from its path or MIME type.",
        "files[].bytes": "Size of the file content involved.",
        "files[].extension": "File extension, kept raw alongside the class.",
        "files[].hunks": "Edited regions within this file. See `file_change.hunks`.",
        "files[].hunks[]": "One contiguous edited region.",
        "files[].hunks[].len": "Lines the region occupies AFTER the edit.",
        "files[].hunks[].old_len": "Lines it occupied before; `len - old_len` is the net change.",
        "files[].lines_added": "Exact count of added (`+`) lines, from the structuredPatch diff body. The gross code-churn signal the hunk sizes cannot give (they include context).",
        "files[].lines_removed": "Exact count of removed (`-`) lines, from the structuredPatch diff body.",
        "files[].hunks[].start": "First line of the region, after the edit.",
        "files[].mime_type": "MIME type, the only signal for remote artifacts that never produce a local file.",
        "files[].operation": "What happened to this file: read, create, edit or delete.",
        "files[].path": "Local path. Null for remote artifacts reached through MCP.",
        "files[].remote_id": "Identifier for a remote artifact, e.g. a Google Doc id.",
        "input": "The tool's arguments, verbatim and vendor-shaped. Deliberately opaque: descending into it would pin a vendor's structure as if it were ours. Generated file content is scrubbed when configured.",
        "input_chars": "Serialized length of `input` before any scrub, so redacted arguments still report their true size.",
        "mcp_server": "For an MCP tool, which server provided it.",
        "mcp_tool": "For an MCP tool, the tool name within that server.",
        "message_id": "The API response that requested this call.",
        "raw": _RAW,
        "tool_name": "Tool invoked, as the model named it.",
    },
    "tool_result": {
        "is_error": "Whether the tool reported failure.",
        "raw": _RAW,
        "source_tool_assistant_uuid": "The assistant record that issued the call, when the vendor recorded it.",
        "tool_denial_kind": "Why a call was refused, when it was: the user declined, a permission rule blocked it, and so on. Null when the call was not denied.",
        "tool_use_id": "Joins this result to its `tool_call`.",
    },
    "unknown": {
        "raw": _RAW,
    },
    "user_message": {
        "char_count": "Length of the message before any cap, measured unscrubbed.",
        "is_injected": "**True means the harness produced this, not a person** -- a skill body, a system reminder, command output, a task notification. Typed prompts are the primary intent signal; injected ones are context. Filter on this before treating a user message as intent.",
        "permission_mode": (
            "The permission mode in force when this prompt was sent: `auto`, `plan`, `acceptEdits` or `default`. Stamped on the record by the vendor, so no correlation with neighbouring mode records is needed. Single-valued across every turn measured, so it describes the turn rather than the individual record. Null on records from before the vendor emitted it."
        ),
        "plan_mode": (
            "True when the prompt was sent in plan mode — `permission_mode == 'plan'`, kept as its own boolean because \"was this planning or doing\" is the common filter. Null, not False, when the vendor recorded no mode at all: unknown and not-plan-mode are different claims."
        ),
        "raw": _RAW,
        "slash_command": "Always null: not currently extracted.",
        "text": "Message text, capped for injected messages and kept whole for typed ones.",
        "text_truncated": "Whether `text` was clipped. `char_count` still reports the full length.",
    },
}


def missing_descriptions(payload_by_type: dict) -> list[str]:
    """`event_type.field` for every published path with no entry above. Empty means complete."""
    gaps: list[str] = []
    for event_type, paths in sorted(payload_by_type.items()):
        if event_type not in EVENT_DOCS:
            gaps.append(f"{event_type} (event type itself)")
        known = FIELD_DOCS.get(event_type, {})
        for path in sorted(paths):
            if not known.get(path):
                gaps.append(f"{event_type}.{path}")
    return gaps
