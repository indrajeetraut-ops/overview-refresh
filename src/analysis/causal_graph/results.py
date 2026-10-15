"""``attrs.result`` -- a tool's (or spawn's) outcome, extracted from ``raw.toolUseResult`` and
``raw.mcpMeta`` into a vendor-neutral, facet-grouped block (design §4.1b, plan
docs/plan/2026-08-23-causal-graph-build-result-mode-attribution.md Step 1).

One entry point, called for both tool and spawn nodes:

    extract_result(tool_name, payload) -> dict | None

``payload`` is a ``tool_result`` event's payload (``{is_error, tool_denial_kind, raw, ...}``).
Returns ``None`` when there is nothing to extract at all (an empty/missing payload); a builder
that has no matching ``tool_result`` event for a call should simply not call this function, so
``attrs.result`` stays absent rather than an empty dict.

**Facets, not verbatim.** A named group inside the result block (``process``, ``git``,
``file``, ``delegation``, ...) is vendor-neutral -- several tools map into one facet, one tool
may fill several. Only applicable facets are present; a facet with nothing in it is omitted
entirely, never an empty dict.

**Extract, not derive.** Every value here is read straight out of ``raw``, never computed from
it -- see docs/plan/2026-08-22-normalizer-tool-result-facts.md §Terms for the extract/promote/
derive distinction this whole result-facts effort keeps. ``totalTokens``/``usage`` are dropped
rather than kept under a new name for exactly this reason (Decision 4 in the plan): the
delegation's real cost is a sum over the child graph, not a field that can be renamed into
correctness.

**Per-tool scoping is required, not stylistic.** The same vendor key means different things
under different tools -- ``type`` is Read's ``text|image|file_unchanged`` and Write's
``create|update``; ``message`` is SendMessage's body and ExitWorktree's status line; ``status``
is Task's only. So the lookup tables below are keyed by tool first; ``_ANY_TOOL`` exists for a
key that is genuinely tool-agnostic, and is empty in this corpus -- every mapped key needed
disambiguation by tool, so nothing qualified.

**The escape hatch.** A ``toolUseResult`` key that no extractor claims is never dropped: it
lands verbatim in ``result.unmapped``, and exactly one structlog WARN names the tool and key.
This is deliberate -- a fixed facet table is permanently behind whatever the vendor adds next,
and ``unmapped`` is how that gap stays visible instead of silently losing data.
"""
from __future__ import annotations

import json
import re

import structlog

__all__ = ["extract_result"]

_log = structlog.get_logger(module="analysis.causal_graph.results")

# --- vendor key -> (facet, field) tables ------------------------------------------------
#
# Lookup order: _BY_TOOL[tool_name], then _ANY_TOOL, else the key lands in `unmapped`.
# "Task" doubles for "Agent": both vendor tool names collapse to one `sub_agent_spawn` event
# upstream (normalizer's `_SPAWN_TOOL_NAMES`) and the result shape is identical either way --
# see descriptions.py's spawn doc ("Exactly two tool names become one -- Agent and Task").
#
# A few real vendor keys have no destination field in the plan's facet vocabulary at all
# (SendMessage's own `message` body, WebFetch's `result` body, WebSearch's `results` list) --
# those are not typos here, they fall through to `unmapped` by design; see the plan's Step 1
# facet vocabulary block for the exact field set this table implements.
#
# One deliberate deviation from the plan's literal field name: ToolSearch's vendor key is
# `matches`, a LIST of matched tool names, not a count. The plan's vocabulary names the
# destination `match_count`, which is misleading for a list -- naming a list field "_count"
# invites a reader to sum or compare it as a scalar. Carried through verbatim as `matches`
# instead so the field name matches its content; flagged for the design doc to reconcile.
_BY_TOOL: dict[str, dict[str, tuple[str, str]]] = {
    "Bash": {
        "stdout": ("process", "stdout"),
        "stderr": ("process", "stderr"),
        "interrupted": ("process", "interrupted"),
        "isImage": ("process", "is_image"),
        "noOutputExpected": ("process", "no_output_expected"),
        "returnCodeInterpretation": ("process", "return_code_interpretation"),
        "backgroundTaskId": ("process", "background_task_id"),
        "timedOutAfterMs": ("process", "timed_out_after_ms"),
        "backgroundCwdHint": ("process", "background_cwd_hint"),
        "persistedOutputPath": ("process", "persisted_output_path"),
        "persistedOutputSize": ("process", "persisted_output_size"),
        # gitOperation: special-cased by _flatten_git_operation, not this table.
    },
    "Read": {
        "type": ("file", "kind"),
        # "file": Read's nested dict, special-cased by _lift_read_file, not this table.
    },
    "Write": {
        "type": ("file", "kind"),
        "userModified": ("file", "user_modified"),
        "memdirStamped": ("file", "memdir_stamped"),
    },
    "Edit": {
        "userModified": ("file", "user_modified"),
        "replaceAll": ("file", "replace_all"),
        "staleRecovered": ("file", "stale_recovered"),
        "memdirStamped": ("file", "memdir_stamped"),
    },
    "Task": {
        "prompt": ("delegation", "prompt"),
        "agentId": ("delegation", "agent_id"),
        "totalDurationMs": ("delegation", "duration_ms"),
        "totalToolUseCount": ("delegation", "tool_use_count"),
        "worktreeBranch": ("delegation", "worktree_branch"),
        "worktreePath": ("delegation", "worktree_path"),
        "isAsync": ("delegation", "is_async"),
        "outputFile": ("delegation", "output_file"),
        "canReadOutputFile": ("delegation", "can_read_output_file"),
        # toolStats: special-cased by _snake_case_dict, not this table.
        # status / agentType / resolvedModel: NOT mapped -- already on the spawn node from
        # the call side (builder._spawn_node), not repeated here (plan Step 2).
        # description: NOT mapped -- no destination in the facet vocabulary; falls to
        # `unmapped` (7 of 75 spawn results, all the async ones).
    },
    "ExitPlanMode": {
        "plan": ("exit_plan_mode", "text"),
        "isAgent": ("exit_plan_mode", "is_agent"),
        "filePath": ("exit_plan_mode", "file_path"),  # the plan file -- not a tool's own
        # file, so unlike Edit/Write's filePath this is NOT already on attrs.files[].
    },
    "AskUserQuestion": {
        "questions": ("interaction", "questions"),
        "answers": ("interaction", "answers"),
        "annotations": ("interaction", "annotations"),
    },
    "WebFetch": {
        "url": ("web", "url"),
        "code": ("web", "http_status"),
        "codeText": ("web", "status_text"),
        "bytes": ("web", "bytes"),
        "durationMs": ("web", "duration_ms"),
        # result: NOT mapped -- the fetched body; no destination in the facet vocabulary.
    },
    "WebSearch": {
        "query": ("web", "query"),
        "searchCount": ("web", "result_count"),
        "durationSeconds": ("web", "duration_s"),
        # results: NOT mapped -- the full result list; no destination in the facet vocabulary.
    },
    "TaskUpdate": {
        "taskId": ("todo", "task_id"),
        "statusChange": ("todo", "status_change"),
        "updatedFields": ("todo", "updated_fields"),
        "success": ("todo", "success"),
    },
    "TaskCreate": {
        "task": ("todo", "task"),
    },
    "TaskList": {
        "tasks": ("todo", "tasks"),
    },
    "Skill": {
        "commandName": ("skill", "command_name"),
        "success": ("skill", "success"),
        "allowedTools": ("skill", "allowed_tools"),
    },
    "ToolSearch": {
        "matches": ("search", "matches"),  # see module docstring's deviation note
        "query": ("search", "query"),
        "total_deferred_tools": ("search", "total_deferred_tools"),
    },
    "EnterWorktree": {
        "worktreeBranch": ("worktree", "branch"),
        "worktreePath": ("worktree", "path"),
        "message": ("worktree", "message"),
    },
    "ExitWorktree": {
        "worktreeBranch": ("worktree", "branch"),
        "worktreePath": ("worktree", "path"),
        "action": ("worktree", "action"),
        "originalCwd": ("worktree", "original_cwd"),
        "message": ("worktree", "message"),
    },
    "SendMessage": {
        "pin": ("message", "pin"),
        "resumedAgentId": ("message", "resumed_agent_id"),
        "success": ("message", "success"),
        # message: NOT mapped -- SendMessage's own body text; no destination in the facet
        # vocabulary (unlike EnterWorktree/ExitWorktree's `message`, a different vendor key
        # sharing the same name -- see the per-tool-scoping note above).
    },
    "ReportFindings": {
        "count": ("findings", "count"),
        "findings": ("findings", "items"),
    },
    "Monitor": {
        "taskId": ("monitor", "task_id"),
        "timeoutMs": ("monitor", "timeout_ms"),
        "persistent": ("monitor", "persistent"),
    },
}
_BY_TOOL["Agent"] = _BY_TOOL["Task"]

# No key in this corpus was safe to share across tools with one meaning -- `success` alone
# means Skill/SendMessage/TaskUpdate outcomes, `query` means WebSearch/ToolSearch queries, each
# landing in a different facet. Kept as a real (empty) table, not removed, because the
# structure is part of the contract (plan Step 1): a future vendor key that genuinely means the
# same thing everywhere belongs here, not duplicated across every _BY_TOOL entry.
_ANY_TOOL: dict[str, tuple[str, str]] = {}

# Keys skipped everywhere, regardless of tool -- never reach a facet or `unmapped`. Each carries
# its reason (plan Step 1's explicit skip list).
_SKIP_KEYS_GLOBAL = frozenset({
    "structuredPatch",  # already attrs.files[].hunks (Edit, Write)
    "oldString", "newString", "originalFile",  # scrubbed to "" upstream (Edit, Write)
    "content",  # scrubbed to "" upstream (Write); Task's subagent report, not carried (plan
    # Step 2's "Not doing: carrying Task.content" -- recoverable from the child session)
    "totalTokens", "usage",  # NOT a delegation total -- plan Decision 4
})

# Keys skipped only for specific tools -- the same key name means something worth keeping
# elsewhere (e.g. ExitPlanMode's `filePath` is the plan's own destination, not a tool's file,
# so it is mapped in _BY_TOOL instead of skipped here).
_SKIP_KEYS_BY_TOOL: dict[str, frozenset[str]] = {
    "Write": frozenset({"filePath"}),  # already attrs.files[].path
    "Edit": frozenset({"filePath"}),  # already attrs.files[].path
    "Task": frozenset({"status", "agentType", "resolvedModel"}),  # already on the spawn
    "Agent": frozenset({"status", "agentType", "resolvedModel"}),  # node, call side
}

# Read's nested `file` dict -> result.file.{num_lines,start_line,total_lines}. filePath/content
# inside it are dropped for the same reasons as the top-level skip list (attrs.files[].path;
# scrubbed to "" upstream) and are not repeated in _SKIP_KEYS_GLOBAL since they never reach the
# top-level key loop -- "file" itself is special-cased before that loop runs.
_READ_FILE_LIFT = {
    "numLines": "num_lines",
    "startLine": "start_line",
    "totalLines": "total_lines",
}

_CAMEL_TO_SNAKE = re.compile(r"(?<!^)(?=[A-Z])")


def _snake(key: str) -> str:
    return _CAMEL_TO_SNAKE.sub("_", key).lower()


def _set_facet(result: dict, facet: str, field: str, value: object) -> None:
    result.setdefault(facet, {})[field] = value


def _flatten_git_operation(git_operation: object) -> dict:
    """``{commit: {sha, kind}}`` -> ``{op: "commit", sha, kind}``.

    Exactly one of commit/push/pr/branch is ever present (123 samples, this corpus) -- the
    tagged union collapses to one flat dict naming which branch fired.
    """
    if not isinstance(git_operation, dict) or not git_operation:
        return {}
    op, fields = next(iter(git_operation.items()))
    out: dict = {"op": op}
    if isinstance(fields, dict):
        out.update(fields)
    return out


def _lift_read_file(nested: object, result: dict) -> None:
    if not isinstance(nested, dict):
        return
    for vendor_key, field in _READ_FILE_LIFT.items():
        if vendor_key in nested:
            _set_facet(result, "file", field, nested[vendor_key])


def extract_result(
    tool_name: str | None,
    payload: dict | None,
    *,
    mcp_server: str | None = None,
    mcp_tool: str | None = None,
) -> dict | None:
    """A tool_result payload -> the facet-grouped ``attrs.result`` block, or None.

    ``payload`` is a ``tool_result`` event's payload: ``{is_error, tool_denial_kind, raw}``.
    ``mcp_server``/``mcp_tool`` are the originating ``tool_call``'s ``mcp_server``/``mcp_tool``
    fields, needed only for MCP calls to name the facet's server/tool (the carrier itself,
    ``raw.mcpMeta``, names neither).

    Returns ``None`` when ``payload`` is empty/missing -- a caller with no matching
    ``tool_result`` event at all should simply not call this, so ``attrs.result`` stays absent.
    """
    if not payload:
        return None

    is_error = bool(payload.get("is_error"))
    denial = payload.get("tool_denial_kind")
    if denial is not None:
        outcome = "denied"
    elif is_error:
        outcome = "error"
    else:
        outcome = "ok"

    raw = payload.get("raw")
    raw = raw if isinstance(raw, dict) else {}
    tur = raw.get("toolUseResult")

    result: dict = {"outcome": outcome, "error_text": None}

    if outcome != "ok":
        # Not doing: parsing anything out of a failure string. The bare string IS the fact.
        if isinstance(tur, str):
            result["error_text"] = tur
        return result

    # --- success -------------------------------------------------------------------------
    mcp_meta = raw.get("mcpMeta")
    is_mcp = isinstance(mcp_meta, dict) or (isinstance(tool_name, str) and tool_name.startswith("mcp__"))
    if is_mcp:
        structured = mcp_meta.get("structuredContent") if isinstance(mcp_meta, dict) else None
        if structured is None and isinstance(tur, str):
            try:
                structured = json.loads(tur)
            except (TypeError, ValueError):
                structured = None
        if structured is not None:
            result["mcp"] = {
                "server": mcp_server,
                "tool": mcp_tool,
                "structured_content": structured,
            }
        return result

    if not isinstance(tur, dict):
        return result  # bare string on success outside MCP: not observed, nothing to extract

    by_tool = _BY_TOOL.get(tool_name or "", {})
    tool_skips = _SKIP_KEYS_BY_TOOL.get(tool_name or "", frozenset())
    unmapped: dict = {}

    for key, value in tur.items():
        if key in _SKIP_KEYS_GLOBAL or key in tool_skips:
            continue
        if key == "gitOperation":
            git = _flatten_git_operation(value)
            if git:
                result["git"] = git
            continue
        if key == "file" and tool_name == "Read":
            _lift_read_file(value, result)
            continue
        if key == "toolStats" and isinstance(value, dict):
            _set_facet(result, "delegation", "tool_stats", {_snake(k): v for k, v in value.items()})
            continue

        mapping = by_tool.get(key) or _ANY_TOOL.get(key)
        if mapping is None:
            unmapped[key] = value
            _log.warning("result_unmapped_key", tool=tool_name, key=key)
            continue
        facet, field = mapping
        _set_facet(result, facet, field, value)

    if unmapped:
        result["unmapped"] = unmapped

    return result
