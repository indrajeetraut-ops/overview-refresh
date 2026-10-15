"""Programmatic SessionSource/SubagentStream builders for normalizer tests (spec:
docs/modules/normalizer.md §11). Records are structurally-faithful synthetic Claude Code
JSONL rows -- no real recorded session is checked into the repo -- built in Python so the
fixture/assertion pair stays reviewable in one place, mirroring
tests/fixtures/uploader/archives.py's "build programmatically, no blobs" convention.
"""
from __future__ import annotations

import json

from collector.normalizer.models import SessionSource, SubagentStream


def _static(records: list[dict]):
    """Wraps an already-in-memory record list as the lazy callable SubagentStream.records
    expects (models.py) -- these fixtures have no real file to defer reading, so the
    laziness is a no-op here, but the interface stays consistent with the real loaders."""
    return lambda: records


def simple_session() -> SessionSource:
    """user -> assistant, no tools. Proves session_start synthesis + basic mapping."""
    records = [
        {
            "type": "user",
            "uuid": "evt-1",
            "parentUuid": None,
            "cwd": "/Users/dev/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-09T14:00:00.000Z",
            "message": {"role": "user", "content": "Please add a health check endpoint"},
        },
        {
            "type": "assistant",
            "uuid": "evt-2",
            "parentUuid": "evt-1",
            "timestamp": "2026-07-09T14:00:05.000Z",
            "requestId": "req_fixture_01",
            "message": {
                "role": "assistant",
                "id": "msg_fixture_01",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "Sure, I'll add that now."}],
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 40,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "stop_reason": "end_turn",
            },
        },
    ]
    return SessionSource(
        vendor="claude_code", session_id="session-simple", source="batch", agent_version="2.3.1", records=records
    )


def tool_pairing_session() -> SessionSource:
    """A Bash tool_use + result, then an Edit tool_use + result carrying a structuredPatch.

    Proves emit-at-call/enrich-in-place pairing and file_change derivation (hunks/lines
    copied from the vendor result, never computed).
    """
    records = [
        {
            "type": "user",
            "uuid": "evt-1",
            "parentUuid": None,
            "cwd": "/Users/dev/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-09T14:00:00.000Z",
            "permissionMode": "plan",
            "message": {"role": "user", "content": "Run the tests and fix the failure"},
        },
        {
            "type": "assistant",
            "uuid": "evt-2",
            "parentUuid": "evt-1",
            "timestamp": "2026-07-09T14:00:03.000Z",
            "effort": "high",
            "attributionSkill": "superpowers:brainstorming",
            "attributionPlugin": "superpowers",
            "message": {
                "role": "assistant",
                "id": "msg_fixture_02",
                "model": "claude-sonnet-5",
                "content": [
                    {"type": "text", "text": "Let me run the tests first."},
                    {"type": "tool_use", "id": "toolu_bash1", "name": "Bash", "input": {"command": "pytest -q"}},
                ],
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 30,
                    "cache_read_input_tokens": 10,
                    "cache_creation_input_tokens": 5,
                },
                "stop_reason": "tool_use",
            },
        },
        {
            "type": "user",
            "uuid": "evt-3",
            "parentUuid": "evt-2",
            "timestamp": "2026-07-09T14:00:07.000Z",
            "sourceToolAssistantUUID": "evt-2",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_bash1", "content": "1 failed, 4 passed", "is_error": True}
                ],
            },
            "toolUseResult": {"exitCode": 1, "durationMs": 850, "stdout": "1 failed, 4 passed", "stderr": ""},
        },
        {
            "type": "assistant",
            "uuid": "evt-4",
            "parentUuid": "evt-3",
            "timestamp": "2026-07-09T14:00:10.000Z",
            "message": {
                "role": "assistant",
                "id": "msg_fixture_03",
                "model": "claude-sonnet-5",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_edit1",
                        "name": "Edit",
                        "input": {"file_path": "src/app.py", "old_string": "return 1", "new_string": "return 2"},
                    }
                ],
                "usage": {
                    "input_tokens": 250,
                    "output_tokens": 60,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "stop_reason": "tool_use",
            },
        },
        {
            "type": "user",
            "uuid": "evt-5",
            "parentUuid": "evt-4",
            "timestamp": "2026-07-09T14:00:12.000Z",
            "sourceToolAssistantUUID": "evt-4",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_edit1", "content": "", "is_error": False}],
            },
            "toolUseResult": {
                "exitCode": 0,
                "durationMs": 40,
                "originalFile": "def f():\n    return 1",
                "structuredPatch": [
                    {"oldStart": 10, "oldLines": 1, "newStart": 10, "newLines": 1, "lines": ["-return 1", "+return 2"]}
                ],
            },
        },
    ]
    return SessionSource(
        vendor="claude_code", session_id="session-tools", source="batch", agent_version="2.3.1", records=records
    )


SCREENSHOT_B64 = "iVBORw0KGgoAAAANSUhEUgAAAxAAAAGP" * 200  # 6,400 chars, PNG-shaped
PLAN_TEXT = "# Plan\n\n" + ("Step: rewrite the scrub so bodies are cut and facts are not.\n" * 40)


def image_and_plan_session() -> SessionSource:
    """Two results the key-name walk has to treat differently.

    A screenshot arrives as `{content: [{type: image, source: {data: <base64>}}]}` -- nested
    two levels inside the tool_result block's own content list, so only a recursive walk
    reaches it, and capping rather than dropping ships a truncated PNG that is content and is
    not even a usable image.

    An ExitPlanMode result carries `plan`: the thing that was being attempted. It is not
    anyone's file, and clipping it loses the part that says what was decided, so it takes
    tool_result_intent_len and is kept whole.
    """
    records = [
        {
            "type": "user",
            "uuid": "im-1",
            "parentUuid": None,
            "cwd": "/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-12T10:00:00.000Z",
            "permissionMode": "plan",
            "message": {"role": "user", "content": "Screenshot the page, then plan the fix"},
        },
        {
            "type": "assistant",
            "uuid": "im-2",
            "parentUuid": "im-1",
            "timestamp": "2026-07-12T10:00:02.000Z",
            "attributionAgent": "general-purpose",
            "message": {
                "role": "assistant",
                "id": "msg_img_01",
                "model": "claude-sonnet-5",
                "content": [
                    {"type": "tool_use", "id": "toolu_shot", "name": "Read", "input": {"file_path": "/tmp/shot.png"}}
                ],
                "usage": {"input_tokens": 90, "output_tokens": 12},
            },
        },
        {
            "type": "user",
            "uuid": "im-3",
            "parentUuid": "im-2",
            "timestamp": "2026-07-12T10:00:04.000Z",
            "sourceToolAssistantUUID": "im-2",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_shot",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": SCREENSHOT_B64}}
                        ],
                        "is_error": False,
                    }
                ],
            },
            "toolUseResult": {
                "type": "image",
                "file": {"base64": SCREENSHOT_B64, "type": "image/png", "originalSize": 48213},
            },
        },
        {
            "type": "assistant",
            "uuid": "im-4",
            "parentUuid": "im-3",
            "timestamp": "2026-07-12T10:00:06.000Z",
            "message": {
                "role": "assistant",
                "id": "msg_img_02",
                "model": "claude-sonnet-5",
                "content": [
                    {"type": "tool_use", "id": "toolu_plan", "name": "ExitPlanMode", "input": {"plan": PLAN_TEXT}}
                ],
                "usage": {"input_tokens": 300, "output_tokens": 80},
                "stop_reason": "tool_use",
            },
        },
        {
            "type": "user",
            "uuid": "im-5",
            "parentUuid": "im-4",
            "timestamp": "2026-07-12T10:00:09.000Z",
            "sourceToolAssistantUUID": "im-4",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_plan", "content": "ok", "is_error": False}],
            },
            "toolUseResult": {"plan": PLAN_TEXT, "isAgent": False},
        },
    ]
    return SessionSource(
        vendor="claude_code", session_id="session-image", source="batch", agent_version="2.3.1", records=records
    )


def file_read_session() -> SessionSource:
    """A Read, whose result nests the body inside a dict of facts:
    `{file: {content, filePath, numLines, startLine, totalLines}}`.

    The shape that makes the scrub's key-name walk load-bearing -- a rule that dropped `file`
    whole would take the path and the line counts with it, and a rule that only looked at
    top-level keys would never reach the body at all. 1,016 Read results in the corpus.
    """
    records = [
        {
            "type": "user",
            "uuid": "rd-1",
            "parentUuid": None,
            "cwd": "/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-11T09:00:00.000Z",
            "message": {"role": "user", "content": "Show me the auth module"},
        },
        {
            "type": "assistant",
            "uuid": "rd-2",
            "parentUuid": "rd-1",
            "timestamp": "2026-07-11T09:00:02.000Z",
            "message": {
                "role": "assistant",
                "id": "msg_read_01",
                "model": "claude-sonnet-5",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_read1",
                        "name": "Read",
                        "input": {"file_path": "/repo/src/auth.py", "offset": 192, "limit": 100},
                    }
                ],
                "usage": {"input_tokens": 120, "output_tokens": 15},
            },
        },
        {
            "type": "user",
            "uuid": "rd-3",
            "parentUuid": "rd-2",
            "timestamp": "2026-07-11T09:00:03.000Z",
            "sourceToolAssistantUUID": "rd-2",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_read1",
                        "content": "   192\tdef authenticate(user, password):\n   193\t    return check(user)",
                        "is_error": False,
                    }
                ],
            },
            "toolUseResult": {
                "type": "text",
                "file": {
                    "content": "def authenticate(user, password):\n    return check(user)",
                    "filePath": "/repo/src/auth.py",
                    "numLines": 100,
                    "startLine": 192,
                    "totalLines": 415,
                },
            },
        },
    ]
    return SessionSource(
        vendor="claude_code", session_id="session-read", source="batch", agent_version="2.3.1", records=records
    )


def denied_tool_session() -> SessionSource:
    """A tool call the user rejected at the permission prompt.

    The distinguishing shape: `toolDenialKind` is set and there is **no** `toolUseResult` at
    all -- the tool never ran, so there is no exit code or duration to report. Observed in
    the reference corpus as permission-rule / user-rejected / automode-unavailable /
    interrupted. The tool_result event is the only place a rejection is visible.
    """
    records = [
        {
            "type": "user",
            "uuid": "evt-1",
            "parentUuid": None,
            "cwd": "/Users/dev/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-09T15:00:00.000Z",
            "message": {"role": "user", "content": "Clean up the build directory"},
        },
        {
            "type": "assistant",
            "uuid": "evt-2",
            "parentUuid": "evt-1",
            "timestamp": "2026-07-09T15:00:02.000Z",
            "message": {
                "role": "assistant",
                "id": "msg_fixture_04",
                "model": "claude-sonnet-5",
                "content": [
                    {"type": "tool_use", "id": "toolu_rm1", "name": "Bash", "input": {"command": "rm -rf build/"}}
                ],
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "stop_reason": "tool_use",
            },
        },
        {
            "type": "user",
            "uuid": "evt-3",
            "parentUuid": "evt-2",
            "timestamp": "2026-07-09T15:00:09.000Z",
            "sourceToolAssistantUUID": "evt-2",
            "toolDenialKind": "user-rejected",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_rm1",
                        "content": "The user doesn't want to proceed with this tool use.",
                        "is_error": True,
                    }
                ],
            },
        },
    ]
    return SessionSource(
        vendor="claude_code", session_id="session-denied", source="batch", agent_version="2.3.1", records=records
    )


SECRET_FILE_BODY = "def compute_pricing(seats):\n    return seats * 42  # proprietary\n" * 40
SECRET_SNIPPET = "1\timport internal_billing\n2\tRATE = 0.0731  # do not disclose\n" * 30
SECRET_SKILL_PROSE = "- my-arch-doc: Use ONLY when asked to write a Talium architecture document.\n" * 40
SECRET_HOOK_STDOUT = '{"hookSpecificOutput": {"additionalContext": "internal deployment topology"}}\n' * 20


def attachment_session() -> SessionSource:
    """The attachment record family -- context injected outside the message flow.

    Covers the three cases the capping logic distinguishes: a mapped type whose body is nested
    (`file` -> content.file.content), a mapped type whose body is flat (`edited_text_file` ->
    snippet), and a type absent from the map entirely, which must be capped by the default-deny
    sweep rather than shipped whole. Also carries a metadata-only type to prove short fields
    aren't needlessly clipped.
    """
    records = [
        {
            "type": "user",
            "uuid": "evt-1",
            "parentUuid": None,
            "cwd": "/Users/dev/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-09T16:00:00.000Z",
            "message": {"role": "user", "content": "Review the pricing module"},
        },
        {
            "type": "attachment",
            "uuid": "att-file",
            "parentUuid": "evt-1",
            "timestamp": "2026-07-09T16:00:01.000Z",
            "attachment": {
                "type": "file",
                "filename": "/Users/dev/repo/src/pricing.py",
                "displayPath": "src/pricing.py",
                "content": {
                    "type": "text",
                    "file": {
                        "filePath": "/Users/dev/repo/src/pricing.py",
                        "content": SECRET_FILE_BODY,
                        "numLines": 120,
                        "startLine": 1,
                        "totalLines": 120,
                    },
                },
            },
        },
        {
            "type": "attachment",
            "uuid": "att-edited",
            "parentUuid": "att-file",
            "timestamp": "2026-07-09T16:00:02.000Z",
            "attachment": {
                "type": "edited_text_file",
                "filename": "/Users/dev/repo/docs/pricing.md",
                "snippet": SECRET_SNIPPET,
            },
        },
        {
            "type": "attachment",
            "uuid": "att-skill",
            "parentUuid": "att-edited",
            "timestamp": "2026-07-09T16:00:03.000Z",
            "attachment": {
                "type": "skill_listing",
                "content": SECRET_SKILL_PROSE,
                "skillCount": 3,
                "isInitial": True,
                "names": ["my-arch-doc", "brainstorming", "writing-plans"],
            },
        },
        {
            "type": "attachment",
            "uuid": "att-hook",
            "parentUuid": "att-skill",
            "timestamp": "2026-07-09T16:00:04.000Z",
            "attachment": {
                "type": "hook_success",
                "hookName": "SessionStart:startup",
                "hookEvent": "SessionStart",
                "exitCode": 0,
                "durationMs": 283,
                "command": "run-hook.cmd session-start",
                "stdout": SECRET_HOOK_STDOUT,
                "stderr": "",
                "content": "",
            },
        },
        {
            "type": "attachment",
            "uuid": "att-unknown",
            "parentUuid": "att-hook",
            "timestamp": "2026-07-09T16:00:05.000Z",
            # Not in _ATTACHMENT_CONTENT_FIELDS -- stands in for a type a future Claude Code
            # version introduces. Must be capped by default-deny, not shipped whole.
            "attachment": {
                "type": "some_future_attachment",
                "payloadText": "leaked-future-content " * 200,
                "shortLabel": "fine",
            },
        },
        {
            "type": "attachment",
            "uuid": "att-meta",
            "parentUuid": "att-unknown",
            "timestamp": "2026-07-09T16:00:06.000Z",
            "attachment": {
                "type": "opened_file_in_ide",
                "filename": "/Users/dev/repo/.gitignore",
            },
        },
    ]
    return SessionSource(
        vendor="claude_code", session_id="session-attachments", source="batch", agent_version="2.3.1", records=records
    )


# A subagent directive, sized like the real ones (corpus median 2,947 chars, max 8,784).
SUBAGENT_DIRECTIVE = (
    "You are exploring the auth module for the Talium code-econ storage work.\n\n"
    "## Scope\nFind every entry point and report the call chain.\n" * 30
)

DRIVE_DOC_TITLE = "Architectural Design Proposal: Hybrid Data Access Layer"
DRIVE_OWNER_EMAIL = "someone@example.com"
DRIVE_DOC_BODY = "# Architectural Design Proposal\n\n" + ("The ingest service accepts... " * 60)


def mcp_json_string_session() -> SessionSource:
    """MCP results, which arrive as a JSON **string** rather than a dict.

    Three results covering the branch: a listing (metadata end to end -- kept whole), a
    document read (`fileContent` is the body -- cut), and a plain error string (not JSON at
    all -- falls through to the ordinary cap). 383 string-shaped results in the corpus, of
    which 378 are errors or rejections.
    """
    listing = (
        '{"files":[{"id":"1dnZAgqNL4","mimeType":"application/vnd.google-apps.document",'
        f'"fileSize":"5860","title":"{DRIVE_DOC_TITLE}","owner":"{DRIVE_OWNER_EMAIL}",'
        '"viewUrl":"https://docs.google.com/document/d/1dnZAgqNL4"},'
        '{"id":"2xyQ","mimeType":"application/vnd.google-apps.spreadsheet","title":"Q3 Costs"}]}'
    )
    doc = json.dumps({"fileContent": DRIVE_DOC_BODY,
                      "mimeType": "application/vnd.google-apps.document",
                      "id": "1dnZAgqNL4"})
    err = "Error: Exit code 1 the command could not be completed as written " * 4

    def _call(uuid, parent, ts, tid, name):
        return {
            "type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
            "message": {"role": "assistant", "id": f"msg_{tid}", "model": "claude-sonnet-5",
                        "content": [{"type": "tool_use", "id": tid, "name": name, "input": {}}],
                        "usage": {"input_tokens": 50, "output_tokens": 10}},
        }

    def _result(uuid, parent, ts, tid, payload):
        return {
            "type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": ts,
            "sourceToolAssistantUUID": parent,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": "", "is_error": False}]},
            "toolUseResult": payload,
        }

    records = [
        {
            "type": "user", "uuid": "mc-1", "parentUuid": None, "cwd": "/repo",
            "gitBranch": "main", "timestamp": "2026-07-13T08:00:00.000Z",
            "message": {"role": "user", "content": "What is in my Drive?"},
        },
        _call("mc-2", "mc-1", "2026-07-13T08:00:01.000Z", "toolu_list",
              "mcp__claude_ai_Google_Drive__list_recent_files"),
        _result("mc-3", "mc-2", "2026-07-13T08:00:02.000Z", "toolu_list", listing),
        _call("mc-4", "mc-3", "2026-07-13T08:00:03.000Z", "toolu_read",
              "mcp__claude_ai_Google_Drive__read_file_content"),
        _result("mc-5", "mc-4", "2026-07-13T08:00:04.000Z", "toolu_read", doc),
        _call("mc-6", "mc-5", "2026-07-13T08:00:05.000Z", "toolu_bash", "Bash"),
        _result("mc-7", "mc-6", "2026-07-13T08:00:06.000Z", "toolu_bash", err),
    ]
    return SessionSource(
        vendor="claude_code", session_id="session-mcp-json", source="batch",
        agent_version="2.3.1", records=records,
    )


def mcp_remote_artifact_session() -> SessionSource:
    """A Google Drive MCP call. The artifact type lives only in the tool result's mimeType,
    and these calls never produce a file_change -- so without extraction before the scrub,
    work delivered outside the repo is invisible. The sibling title/owner/viewUrl must not
    survive."""
    records = [
        {
            "type": "user",
            "uuid": "evt-1",
            "parentUuid": None,
            "cwd": "/Users/dev/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-09T17:00:00.000Z",
            "message": {"role": "user", "content": "What did I write in Drive recently?"},
        },
        {
            "type": "assistant",
            "uuid": "evt-2",
            "parentUuid": "evt-1",
            "timestamp": "2026-07-09T17:00:02.000Z",
            "message": {
                "role": "assistant",
                "id": "msg_fixture_05",
                "model": "claude-sonnet-5",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_drive1",
                        "name": "mcp__claude_ai_Google_Drive__list_recent_files",
                        "input": {"page_size": 5},
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "stop_reason": "tool_use",
            },
        },
        {
            "type": "user",
            "uuid": "evt-3",
            "parentUuid": "evt-2",
            "timestamp": "2026-07-09T17:00:05.000Z",
            "sourceToolAssistantUUID": "evt-2",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_drive1", "content": "2 files", "is_error": False}
                ],
            },
            "toolUseResult": {
                "files": [
                    {
                        "id": "1dnZAgqNL4-uVvBTE6EenoNCgtSwlccDG",
                        "mimeType": "application/vnd.google-apps.document",
                        "fileSize": "5860",
                        "title": DRIVE_DOC_TITLE,
                        "owner": DRIVE_OWNER_EMAIL,
                        "viewUrl": "https://docs.google.com/document/d/1dnZAgqNL4",
                    },
                    {
                        "id": "2xyQBrpML5-wWcUF7FfpoODhuTxmddEH",
                        "mimeType": "application/vnd.google-apps.spreadsheet",
                        "fileSize": "9120",
                        "title": "Q3 Cost Model",
                        "owner": DRIVE_OWNER_EMAIL,
                        "viewUrl": "https://docs.google.com/spreadsheets/d/2xyQBrpML5",
                    },
                ]
            },
        },
    ]
    return SessionSource(
        vendor="claude_code", session_id="session-mcp", source="batch", agent_version="2.3.1", records=records
    )


def mixed_model_subagent_session() -> SessionSource:
    """Parent model fable spawns two Task subagents: a complete sonnet child (itself
    spawning a nested haiku grandchild) and an incomplete haiku child (its own transcript
    ends on a non-assistant turn, and the parent never receives its fold-back result).
    The doc's required fixture (§11) -- exercises §6 end to end, including one level of
    nesting.
    """
    grandchild_records = [
        {
            "type": "user",
            "uuid": "gc-evt-1",
            "parentUuid": None,
            "cwd": "/Users/dev/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-09T14:01:10.000Z",
            "message": {"role": "user", "content": "Find auth entry points"},
        },
        {
            "type": "assistant",
            "uuid": "gc-evt-2",
            "parentUuid": "gc-evt-1",
            "timestamp": "2026-07-09T14:01:15.000Z",
            "message": {
                "role": "assistant",
                "id": "msg_fixture_06",
                "model": "claude-haiku-5",
                "content": [{"type": "text", "text": "Found login() and refresh_token()."}],
                "usage": {
                    "input_tokens": 30,
                    "output_tokens": 15,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "stop_reason": "end_turn",
            },
        },
    ]

    sonnet_records = [
        {
            "type": "user",
            "uuid": "sub-evt-1",
            "parentUuid": None,
            "cwd": "/Users/dev/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-09T14:01:00.000Z",
            "message": {"role": "user", "content": "Explore the auth module"},
        },
        {
            "type": "assistant",
            "uuid": "sub-evt-2",
            "parentUuid": "sub-evt-1",
            "timestamp": "2026-07-09T14:01:05.000Z",
            "message": {
                "role": "assistant",
                "id": "msg_fixture_07",
                "model": "claude-sonnet-5",
                "content": [
                    {"type": "text", "text": "Spawning a deeper explorer."},
                    {"type": "tool_use", "id": "toolu_grandchild", "name": "Task", "input": {"subagent_type": "Explore"}},
                ],
                "usage": {
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "stop_reason": "tool_use",
            },
        },
    ]

    haiku_records = [
        {
            "type": "user",
            "uuid": "sub-evt-h1",
            "parentUuid": None,
            "cwd": "/Users/dev/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-09T14:01:00.000Z",
            "message": {"role": "user", "content": "Draft a plan for the migration"},
        },
        {
            "type": "assistant",
            "uuid": "sub-evt-h2",
            "parentUuid": "sub-evt-h1",
            "timestamp": "2026-07-09T14:01:04.000Z",
            "message": {
                "role": "assistant",
                "id": "msg_fixture_08",
                "model": "claude-haiku-5",
                "content": [
                    {"type": "tool_use", "id": "toolu_h_read", "name": "Read", "input": {"file_path": "src/migrate.py"}}
                ],
                "usage": {
                    "input_tokens": 40,
                    "output_tokens": 10,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "stop_reason": "tool_use",
            },
        },
        {
            "type": "user",
            "uuid": "sub-evt-h3",
            "parentUuid": "sub-evt-h2",
            "timestamp": "2026-07-09T14:01:06.000Z",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_h_read", "content": "...file contents...", "is_error": False}
                ],
            },
            "toolUseResult": {"exitCode": 0, "durationMs": 5},
        },
        # transcript ends here on a non-assistant turn: the child ran but never returned.
    ]

    parent_records = [
        {
            "type": "user",
            "uuid": "evt-1",
            "parentUuid": None,
            "cwd": "/Users/dev/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-09T14:00:00.000Z",
            "message": {"role": "user", "content": "Plan and explore the auth refactor"},
        },
        {
            "type": "assistant",
            "uuid": "evt-2",
            "parentUuid": "evt-1",
            "timestamp": "2026-07-09T14:00:05.000Z",
            "message": {
                "role": "assistant",
                "id": "msg_fixture_09",
                "model": "claude-fable-5",
                "content": [
                    {"type": "text", "text": "I'll spawn two subagents."},
                    {
                        "type": "tool_use",
                        "id": "toolu_spawn_sonnet",
                        "name": "Task",
                        "input": {
                            "subagent_type": "Explore",
                            # The directive: `description` is the short UI label and the
                            # natural name for the child's work; `prompt` is the instruction.
                            "description": "Explore the auth module",
                            "prompt": SUBAGENT_DIRECTIVE,
                            "model": "sonnet",
                            "run_in_background": False,
                        },
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_spawn_haiku",
                        "name": "Task",
                        "input": {"subagent_type": "Plan", "description": "Draft the migration plan"},
                    },
                ],
                "usage": {
                    "input_tokens": 300,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "stop_reason": "tool_use",
            },
        },
        {
            "type": "user",
            "uuid": "evt-3",
            "parentUuid": "evt-2",
            "timestamp": "2026-07-09T14:01:20.000Z",
            "sourceToolAssistantUUID": "evt-2",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_spawn_sonnet",
                        "content": "Explored the auth module; found 3 entry points.",
                        "is_error": False,
                    }
                ],
            },
            "toolUseResult": {
                "status": "completed",
                "resolvedModel": "claude-sonnet-5",
                "durationMs": 75000,
                "totalTokens": 999,
                # The subagent's task text -- user content, must not survive into raw.
                "prompt": "Explore the auth module and report the entry points",
            },
        },
        # no tool_result ever arrives for toolu_spawn_haiku.
    ]

    grandchild = SubagentStream(
        agent_id="agent-grandchild-1",
        tool_use_id="toolu_grandchild",
        meta={
            "agentId": "agent-grandchild-1",
            "toolUseId": "toolu_grandchild",
            "agentType": "Explore",
            "spawnDepth": 2,
            "resolvedModel": "claude-haiku-5",
        },
        records=_static(grandchild_records),
    )
    sonnet_child = SubagentStream(
        agent_id="agent-sonnet-1",
        tool_use_id="toolu_spawn_sonnet",
        meta={
            "agentId": "agent-sonnet-1",
            "toolUseId": "toolu_spawn_sonnet",
            "agentType": "Explore",
            "spawnDepth": 1,
            "resolvedModel": "claude-sonnet-5",
        },
        records=_static(sonnet_records),
        subagent_streams={"toolu_grandchild": grandchild},
    )
    haiku_child = SubagentStream(
        agent_id="agent-haiku-1",
        tool_use_id="toolu_spawn_haiku",
        meta={
            "agentId": "agent-haiku-1",
            "toolUseId": "toolu_spawn_haiku",
            "agentType": "Plan",
            "spawnDepth": 1,
            "resolvedModel": "claude-haiku-5",
        },
        records=_static(haiku_records),
    )

    return SessionSource(
        vendor="claude_code",
        session_id="session-parent",
        source="batch",
        agent_version="2.3.1",
        records=parent_records,
        subagent_streams={"toolu_spawn_sonnet": sonnet_child, "toolu_spawn_haiku": haiku_child},
    )


def api_error_and_compaction_session() -> SessionSource:
    """A system api_error record and a compact_boundary record."""
    records = [
        {
            "type": "user",
            "uuid": "evt-1",
            "parentUuid": None,
            "cwd": "/Users/dev/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-09T15:00:00.000Z",
            "message": {"role": "user", "content": "Keep going"},
        },
        {
            "type": "system",
            "uuid": "evt-2",
            "parentUuid": "evt-1",
            "timestamp": "2026-07-09T15:00:02.000Z",
            "isApiErrorMessage": True,
            "subtype": "api_error",
            "status": 529,
            "retryable": True,
            "retryCount": 1,
            "retryAfterMs": 2000,
            "requestId": "req-abc123",
            "message": "Overloaded",
            "headers": {"x-should-retry": "true"},
        },
        {
            "type": "system",
            "uuid": "evt-3",
            "parentUuid": "evt-2",
            "timestamp": "2026-07-09T15:00:30.000Z",
            "subtype": "compact_boundary",
            "compactMetadata": {"trigger": "auto", "preTokens": 180000, "postTokens": 20000, "durationMs": 1500},
        },
    ]
    return SessionSource(
        vendor="claude_code", session_id="session-errors", source="batch", agent_version="2.3.1", records=records
    )


def passthrough_session() -> SessionSource:
    """mode/permission-mode records (no uuid) and one wholly unrecognized type -- both
    must ship as `unknown`, never silently dropped."""
    records = [
        {
            "type": "user",
            "uuid": "evt-1",
            "parentUuid": None,
            "cwd": "/Users/dev/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-09T16:00:00.000Z",
            "message": {"role": "user", "content": "Switch to plan mode"},
        },
        {"type": "mode", "mode": "plan", "timestamp": "2026-07-09T16:00:01.000Z"},
        {"type": "permission-mode", "mode": "acceptEdits", "timestamp": "2026-07-09T16:00:02.000Z"},
        {"type": "some-future-record-type", "uuid": "evt-4", "timestamp": "2026-07-09T16:00:03.000Z", "detail": "forward-compat"},
    ]
    return SessionSource(
        vendor="claude_code", session_id="session-passthrough", source="batch", agent_version="2.3.1", records=records
    )


def pr_link_session() -> SessionSource:
    """A `pr-link` record -- Claude Code's own note of a PR it opened -- must ship as a typed
    `pr_link` event carrying prNumber/prUrl/prRepository. No uuid/parentUuid on the record, so
    record_id/parent_id are null (it isn't part of the conversation tree)."""
    records = [
        {
            "type": "user",
            "uuid": "evt-1",
            "parentUuid": None,
            "cwd": "/Users/dev/repo",
            "gitBranch": "main",
            "timestamp": "2026-07-09T17:00:00.000Z",
            "message": {"role": "user", "content": "Open a PR for the fix"},
        },
        {
            "type": "pr-link",
            "prNumber": 42,
            "prUrl": "https://github.com/acme/repo/pull/42",
            "prRepository": "acme/repo",
            "timestamp": "2026-07-09T17:00:05.000Z",
        },
    ]
    return SessionSource(
        vendor="claude_code", session_id="session-pr-link", source="batch", agent_version="2.3.1", records=records
    )
