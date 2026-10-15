"""Config models for the normalizer. Layering: defaults here → YAML → env vars.

Only the flat, non-derivational vendor mappings live in YAML: the usage-field copy
map, the MCP tool-name pattern, and redaction patterns. Record-type dispatch and derivations
(file_change, subagent correlation, cache_write_detail/server_tool_use construction) are code
— they have real branching, not a straight copy.

TODO(normalizer): switch load_config to the shared config loader in src/common when that
loader lands.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_ENV_REF = re.compile(r"\$\{(\w+)\}")

# The straight usage.<path> -> assistant_message.<field> copies.
_DEFAULT_USAGE_FIELD_MAP = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cache_read_input_tokens",
    "cache_write_tokens": "cache_creation_input_tokens",
}

_DEFAULT_MCP_NAME_PATTERN = r"^mcp__(?P<server>[^_].*?)__(?P<tool>.+)$"

# Artifact class -> the extensions/basenames/mime types that map to it. Starting
# taxonomy, deliberately small; an unmapped value classifies as "unknown" and is never
# dropped, so the raw extension is what surfaces gaps here.
_DEFAULT_ARTIFACT_CLASS_BY_EXTENSION = {
    "code": [
        ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".rb", ".php",
        ".c", ".h", ".cpp", ".hpp", ".cs", ".swift", ".scala", ".sh", ".bash", ".zsh", ".sql",
    ],
    "documentation": [".md", ".markdown", ".rst", ".adoc", ".txt"],
    "config": [".yaml", ".yml", ".toml", ".ini", ".cfg", ".json", ".env", ".properties"],
    "markup": [".html", ".htm", ".css", ".scss", ".xml"],
    "data": [".csv", ".tsv", ".parquet", ".jsonl", ".ndjson"],
    "notebook": [".ipynb"],
    "template": [".mako", ".jinja", ".jinja2", ".j2", ".hbs", ".mustache"],
}

# Dotfiles and extensionless files: Path(".gitignore").suffix is "", so suffix lookup can't
# reach them. Matched on the exact basename instead.
_DEFAULT_ARTIFACT_CLASS_BY_BASENAME = {
    "config": [
        ".gitignore", ".dockerignore", ".gitattributes", ".editorconfig", ".npmrc",
        "Dockerfile", "Makefile", "Procfile", "requirements.txt", "go.mod", "Gemfile",
    ],
    "documentation": ["README", "LICENSE", "CHANGELOG", "NOTICE"],
}

# Remote artifacts reached through MCP tools -- these never produce a file_change, so the
# mime type from the tool result is the only signal that a Google Doc was touched at all.
_DEFAULT_ARTIFACT_CLASS_BY_MIME = {
    "document": [
        "application/vnd.google-apps.document",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
    "spreadsheet": [
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ],
    "presentation": [
        "application/vnd.google-apps.presentation",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ],
    "documentation": ["text/markdown", "text/plain"],
    "code": ["text/x-python", "application/x-sh"],
    "data": ["text/csv", "application/json"],
    "pdf": ["application/pdf"],
}


class NormalizerConfig(BaseModel):
    # "which normalizer built this event" . A config
    # field with a default, matching server/uploader/config.py's uploader_version.
    collector_version: str = "0.1.0"
    usage_field_map: dict[str, str] = Field(default_factory=lambda: dict(_DEFAULT_USAGE_FIELD_MAP))
    # -- what survives of a tool result -------------------------------------------
    # A tool result holds two unlike things under one key: copies of files (up to 208 KB) and
    # facts about what happened (gitOperation, filePath, type, status, agentId, resolvedModel
    # -- tens of bytes). The normalizer keeps the facts and cuts the bodies. It promotes
    # nothing to the payload: which facts a consumer needs is the consumer's decision, and the
    # graph builder makes it at build time from `raw`, where it can be changed without
    # re-normalizing anything.
    #
    # Both use the -1 / 0 / N semantics of attachment_len: -1 keeps the value whole, 0 empties
    # it, N clips to N chars with a visible truncation mark.
    #
    # Bodies. 0 by default: a written file, a read file, a diff body and an Edit's
    # before/after are copies of the user's source, and the normalizer is not a place to store
    # them. WHICH keys these are is structural and lives in claude_code.py's
    # _TOOL_RESULT_CONTENT_KEYS.
    tool_result_content_len: int = 0
    # Everything else under a result. 1000 covers the facts whole -- a gitOperation is 30-100
    # bytes, a commit summary on stdout ~250 -- while keeping a `cat` of a 25 KB file from
    # riding in on Bash's stdout, which is the one key that carries both.
    tool_result_len: int = 1000
    # Intent: a plan submitted through ExitPlanMode, the prompt handed to a subagent. -1 --
    # kept whole -- because these say what was being *attempted*, and a plan clipped at 1000
    # chars loses the part that says what it decided. Not content: nobody's file is in here.
    # Separate from tool_result_len so keeping them whole does not also un-cap `stdout`.
    tool_result_intent_len: int = -1
    # A result that arrives as a JSON string -- MCP's shape. Content keys still apply, so
    # `fileContent` (a Drive document's whole body) is cut; everything else takes this,
    # whole by default because an MCP listing is metadata end to end and clipping it at
    # 1000 produced a mid-object truncation that was neither parseable nor useful. Only
    # reaches values that PARSE as JSON: error text and rejection messages arrive as bare
    # strings, take tool_result_len, and stay capped.
    tool_result_mcp_len: int = -1
    mcp_name_pattern: str = _DEFAULT_MCP_NAME_PATTERN
    redaction_patterns: list[str] = Field(default_factory=list)  # empty by default
    # Unconditional -- unlike redaction_patterns, this doesn't depend on a pattern match:
    # content a Write/Edit/MultiEdit call generates and writes to disk is categorically
    # sensitive (proprietary source, not merely secret-shaped), so it's on by default.
    redact_generated_file_content: bool = True

    # -- message text -----------------------------------------------------------
    # Assistant text is the surviving intent signal -- whether the agent is about to review,
    # implement, or fix. Claude Code strips the thinking plaintext before writing the
    # transcript (empty in all 2,382 blocks observed), so this is what is left to classify on.
    # 1000 is a guess, not a measurement: median assistant text is 93 chars and 88% of blocks
    # are already under it, so the cap bounds the tail without touching the common case.
    # Same -1 / 0 / N semantics as attachment_len.
    assistant_text_len: int = 1000
    # Injected user records only -- skill bodies, task notifications, command output, system
    # reminders. A typed prompt is left whole regardless: it *is* the primary intent signal and
    # is short, whereas the largest injection observed was a 151,597-char skill body. The
    # discriminator is the vendor's own `isMeta`, the same field turn detection uses.
    user_meta_text_len: int = 1000

    # -- visible output token counts --------------------------------------------
    # Counts the reply text and tool arguments an assistant record emitted, so a consumer can
    # subtract them from output_tokens and recover what thinking cost. Off means the field is
    # null and tiktoken is never imported.
    count_estimated_visible_tokens: bool = True
    # tiktoken encoding name. o200k_base is the current-generation OpenAI vocabulary and the
    # one the published constants were measured against; cl100k_base scored the same on prose
    # and marginally worse overall. This is *not* Anthropic's tokenizer -- no such thing is
    # published -- so the count is raw and a per-model-family multiplier is applied downstream.
    # Changing this invalidates stored counts, which is why the name ships on every event.
    tokenizer_encoding: str = "o200k_base"

    # -- attachment bodies ------------------------------------------------------
    # An `attachment` record is context injected outside the message flow: @-mentioned files,
    # IDE-open files, skill listings, hook output, directory listings. Several types embed
    # whole files -- the largest observed is a 66,739-char source file, and skill listings
    # account for 1.19M of the 1.77M chars of attachment content in the reference corpus.
    #
    # Length is policy, so it lives here; *which* fields hold the body is structural knowledge
    # of the vendor format and lives in claude_code.py's _ATTACHMENT_CONTENT_FIELDS, the same
    # split as _GENERATED_CONTENT_FIELDS.
    #
    # -1 keeps the body whole, 0 drops it entirely, N truncates to N chars. Truncation is
    # always recorded (content_chars + content_truncated) so a clipped body is never mistaken
    # for a short one.
    attachment_len: int = 1000
    # Per-type overrides. The defaults reflect what the corpus showed: a skill listing is the
    # same ~9KB of prose re-sent by every session and its `names`/`skillCount` metadata (kept
    # regardless) already answers "which skills were in context"; the file-bearing types are
    # user repo content.
    attachment_len_by_type: dict[str, int] = Field(
        default_factory=lambda: {
            "skill_listing": 0,
            "file": 0,
            "already_read_file": 0,
            "edited_text_file": 0,
            "selected_lines_in_ide": 0,
        }
    )
    # An attachment type we have never seen -- a future Claude Code version's -- gets this
    # instead of attachment_len. It defaults to 0 because the whole point of the unknown case
    # is that we cannot say what is in the body: a 1000-char head of an unclassified field is
    # still 1000 chars of something nobody has looked at. Known-but-mapped types are covered by
    # attachment_len; this is only the never-seen case, and raising it is an explicit choice.
    attachment_len_unknown_type: int = 0

    # -- artifact classification -----------------------------------------------
    # Labels the goods a session produced -- code vs documentation vs a Google Doc. Derived
    # from the path/mime only, never from content, so it is unaffected by any redaction above.
    # The taxonomy is an org decision (is a .mako a template or config?), hence config not
    # code. The raw `extension`/`mime_type` always ship alongside the label, so a taxonomy
    # change can be re-applied to stored data without re-ingesting.
    artifact_class_by_extension: dict[str, list[str]] = Field(
        default_factory=lambda: dict(_DEFAULT_ARTIFACT_CLASS_BY_EXTENSION)
    )
    artifact_class_by_basename: dict[str, list[str]] = Field(
        default_factory=lambda: dict(_DEFAULT_ARTIFACT_CLASS_BY_BASENAME)
    )
    artifact_class_by_mime: dict[str, list[str]] = Field(
        default_factory=lambda: dict(_DEFAULT_ARTIFACT_CLASS_BY_MIME)
    )


def load_config(path: str | Path) -> NormalizerConfig:
    text = Path(path).read_text(encoding="utf-8")
    text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), text)
    return NormalizerConfig.model_validate(yaml.safe_load(text) or {})
