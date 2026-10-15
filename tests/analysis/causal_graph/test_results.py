"""Behavioural tests for extract_result -- one assertion per facet, plus the contract-level
guarantees the plan calls out: absent-not-empty, the skip list, the unmapped hatch, outcome
derivation, and MCP resolving through mcpMeta rather than json.loads.
"""
from structlog.testing import capture_logs

from analysis.causal_graph.results import extract_result


def _payload(raw: dict, *, is_error: bool = False, tool_denial_kind: str | None = None) -> dict:
    return {"is_error": is_error, "tool_denial_kind": tool_denial_kind, "raw": {"toolUseResult": raw}}


# --- absence, not emptiness --------------------------------------------------------------

def test_none_payload_returns_none():
    assert extract_result("Bash", None) is None
    assert extract_result("Bash", {}) is None


# --- outcome derivation --------------------------------------------------------------------

def test_outcome_ok_on_success():
    result = extract_result("Bash", _payload({"stdout": "", "stderr": "", "interrupted": False,
                                               "isImage": False, "noOutputExpected": True}))
    assert result["outcome"] == "ok"
    assert result["error_text"] is None


def test_outcome_error_when_is_error_and_no_denial():
    payload = {"is_error": True, "tool_denial_kind": None,
               "raw": {"toolUseResult": "Error: EISDIR: illegal operation on a directory"}}
    result = extract_result("Read", payload)
    assert result["outcome"] == "error"
    assert result["error_text"] == "Error: EISDIR: illegal operation on a directory"


def test_outcome_denied_when_denial_kind_present_even_if_is_error_false():
    # outcome must come from tool_denial_kind, never inferred from is_error alone.
    payload = {"is_error": True, "tool_denial_kind": "permission-rule",
               "raw": {"toolUseResult": "Error: Permission to use Bash ... has been denied."}}
    result = extract_result("Bash", payload)
    assert result["outcome"] == "denied"
    assert result["error_text"] == "Error: Permission to use Bash ... has been denied."


def test_outcome_never_derived_from_dict_vs_string_mcp_success_is_a_string():
    # MCP returns a string toolUseResult on SUCCESS. is_error/denial say ok; a naive
    # isinstance(str) check would wrongly call this a failure.
    payload = {
        "is_error": False,
        "tool_denial_kind": None,
        "raw": {"toolUseResult": '{"fileContent": "hello"}',
                "mcpMeta": {"structuredContent": {"fileContent": "hello"}}},
    }
    result = extract_result("mcp__claude_ai_Google_Drive__read_file_content", payload)
    assert result["outcome"] == "ok"
    assert result["error_text"] is None


def test_bare_string_on_failure_no_facets():
    payload = {"is_error": True, "tool_denial_kind": None, "raw": {"toolUseResult": "boom"}}
    result = extract_result("Bash", payload)
    assert result == {"outcome": "error", "error_text": "boom"}


# --- process facet (Bash) -------------------------------------------------------------------

def test_process_facet_from_bash():
    result = extract_result("Bash", _payload({
        "stdout": "hi", "stderr": "", "interrupted": False, "isImage": False,
        "noOutputExpected": False, "returnCodeInterpretation": "non-zero judged benign",
        "backgroundTaskId": "bg1", "timedOutAfterMs": 120000, "backgroundCwdHint": "/repo",
        "persistedOutputPath": "/tmp/out", "persistedOutputSize": 48213,
    }))
    assert result["process"] == {
        "stdout": "hi", "stderr": "", "interrupted": False, "is_image": False,
        "no_output_expected": False, "return_code_interpretation": "non-zero judged benign",
        "background_task_id": "bg1", "timed_out_after_ms": 120000,
        "background_cwd_hint": "/repo", "persisted_output_path": "/tmp/out",
        "persisted_output_size": 48213,
    }
    assert "git" not in result


def test_no_output_expected_true_for_rm_style_silence():
    result = extract_result("Bash", _payload({
        "stdout": "", "stderr": "", "interrupted": False, "isImage": False,
        "noOutputExpected": True,
    }))
    assert result["process"]["no_output_expected"] is True


# --- git facet (gitOperation, special-cased) ------------------------------------------------

def test_git_commit_flattened():
    result = extract_result("Bash", _payload({
        "stdout": "x", "stderr": "", "interrupted": False, "isImage": False,
        "noOutputExpected": False,
        "gitOperation": {"commit": {"sha": "e01a4c6", "kind": "committed"}},
    }))
    assert result["git"] == {"op": "commit", "sha": "e01a4c6", "kind": "committed"}


def test_git_branch_merged_and_rebased_actions():
    merged = extract_result("Bash", _payload({
        "stdout": "", "stderr": "", "interrupted": False, "isImage": False,
        "noOutputExpected": False,
        "gitOperation": {"branch": {"ref": "origin/feature/x", "action": "merged"}},
    }))
    rebased = extract_result("Bash", _payload({
        "stdout": "", "stderr": "", "interrupted": False, "isImage": False,
        "noOutputExpected": False,
        "gitOperation": {"branch": {"ref": "origin/feature/x", "action": "rebased"}},
    }))
    assert merged["git"]["action"] == "merged"
    assert rebased["git"]["action"] == "rebased"


def test_git_pr_and_push_variants():
    pr = extract_result("Bash", _payload({
        "stdout": "", "stderr": "", "interrupted": False, "isImage": False,
        "noOutputExpected": False,
        "gitOperation": {"pr": {"number": 9, "url": "https://x/pull/9", "action": "created"}},
    }))
    push = extract_result("Bash", _payload({
        "stdout": "", "stderr": "", "interrupted": False, "isImage": False,
        "noOutputExpected": False,
        "gitOperation": {"push": {"branch": "feature/x"}},
    }))
    assert pr["git"] == {"op": "pr", "number": 9, "url": "https://x/pull/9", "action": "created"}
    assert push["git"] == {"op": "push", "branch": "feature/x"}


# --- file facet (Read/Write/Edit) -------------------------------------------------------------

def test_read_file_facet_lifts_nested_fields_and_drops_path_and_content():
    result = extract_result("Read", _payload({
        "type": "text",
        "file": {"filePath": "/repo/x.py", "content": "", "numLines": 62, "startLine": 160,
                 "totalLines": 793},
    }))
    assert result["file"] == {"kind": "text", "num_lines": 62, "start_line": 160,
                               "total_lines": 793}
    assert "path" not in result["file"]
    assert "content" not in result["file"]


def test_edit_file_facet_drops_patch_and_strings_keeps_flags():
    result = extract_result("Edit", _payload({
        "filePath": "/repo/x.py", "oldString": "", "newString": "", "originalFile": None,
        "structuredPatch": [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1,
                              "lines": []}],
        "userModified": False, "replaceAll": False, "staleRecovered": True,
        "memdirStamped": False,
    }))
    assert result["file"] == {"user_modified": False, "replace_all": False,
                               "stale_recovered": True, "memdir_stamped": False}
    assert "hunks" not in result["file"]
    assert "unmapped" not in result


def test_write_file_facet_kind_create_vs_update():
    created = extract_result("Write", _payload({
        "type": "create", "filePath": "/repo/x.py", "content": "", "structuredPatch": [],
        "originalFile": None, "userModified": False,
    }))
    updated = extract_result("Write", _payload({
        "type": "update", "filePath": "/repo/x.py", "content": "", "structuredPatch": [],
        "originalFile": None, "userModified": True,
    }))
    assert created["file"]["kind"] == "create"
    assert updated["file"]["kind"] == "update"
    assert updated["file"]["user_modified"] is True


# --- delegation facet (Task, called with the fixed "Task" lookup key) ------------------------

def test_delegation_facet_from_task_prompt_never_status_or_totals():
    result = extract_result("Task", _payload({
        "status": "completed", "prompt": "do the thing", "agentId": "a15f40f2871d0aa1b",
        "agentType": "general-purpose", "content": [{"type": "text", "text": ""}],
        "resolvedModel": "claude-fable-5", "totalDurationMs": 92794, "totalTokens": 16372,
        "totalToolUseCount": 2, "usage": {"input_tokens": 2},
        "toolStats": {"readCount": 0, "searchCount": 0, "bashCount": 1, "editFileCount": 1,
                      "linesAdded": 103, "linesRemoved": 0, "otherToolCount": 0},
    }))
    assert result["delegation"] == {
        "prompt": "do the thing", "agent_id": "a15f40f2871d0aa1b", "duration_ms": 92794,
        "tool_use_count": 2,
        "tool_stats": {"read_count": 0, "search_count": 0, "bash_count": 1,
                       "edit_file_count": 1, "lines_added": 103, "lines_removed": 0,
                       "other_tool_count": 0},
    }
    # status / agentType / resolvedModel / content / totalTokens / usage: never repeated.
    assert "status" not in result["delegation"]
    assert "agent_type" not in result["delegation"]
    assert "resolved_model" not in result["delegation"]
    assert "content" not in result["delegation"]
    assert "total_tokens" not in result["delegation"]
    assert "usage" not in result["delegation"]


def test_delegation_async_spawn_has_no_duration():
    result = extract_result("Task", _payload({
        "isAsync": True, "status": "async_launched", "agentId": "a1", "description": "step",
        "resolvedModel": "claude-opus-4-8", "prompt": "…", "outputFile": "/tmp/out",
        "canReadOutputFile": True,
    }))
    assert result["delegation"]["is_async"] is True
    assert result["delegation"]["output_file"] == "/tmp/out"
    assert result["delegation"]["can_read_output_file"] is True
    assert "duration_ms" not in result["delegation"]
    # description has no destination in the facet vocabulary -> unmapped, not silently lost.
    assert result["unmapped"] == {"description": "step"}


# --- exit_plan_mode facet --------------------------------------------------------------------

def test_exit_plan_mode_facet_keeps_its_own_file_path():
    result = extract_result("ExitPlanMode", _payload({
        "plan": "# Plan\n…", "isAgent": False, "filePath": "/Users/x/.claude/plans/x.md",
    }))
    assert result["exit_plan_mode"] == {
        "text": "# Plan\n…", "is_agent": False, "file_path": "/Users/x/.claude/plans/x.md",
    }


# --- interaction facet (AskUserQuestion) ------------------------------------------------------

def test_interaction_facet():
    result = extract_result("AskUserQuestion", _payload({
        "questions": [{"question": "Which?", "header": "Scope", "options": [], "multiSelect": False}],
        "answers": {"Which?": "A"},
        "annotations": {},
    }))
    assert result["interaction"]["questions"][0]["question"] == "Which?"
    assert result["interaction"]["answers"] == {"Which?": "A"}
    assert result["interaction"]["annotations"] == {}


# --- web facet (WebFetch / WebSearch) ---------------------------------------------------------

def test_webfetch_facet():
    result = extract_result("WebFetch", _payload({
        "url": "https://x", "code": 404, "codeText": "Not Found", "bytes": 0,
        "durationMs": 1221, "result": "the body",
    }))
    assert result["web"] == {"url": "https://x", "http_status": 404, "status_text": "Not Found",
                              "bytes": 0, "duration_ms": 1221}
    assert result["unmapped"] == {"result": "the body"}


def test_websearch_facet():
    result = extract_result("WebSearch", _payload({
        "query": "skip locked postgres", "results": [{"title": "t", "url": "u"}],
        "searchCount": 1, "durationSeconds": 2.5,
    }))
    assert result["web"] == {"query": "skip locked postgres", "result_count": 1,
                              "duration_s": 2.5}
    assert "results" in result["unmapped"]


# --- todo facet (TaskCreate/TaskUpdate/TaskList) -----------------------------------------------

def test_todo_facet_task_update():
    result = extract_result("TaskUpdate", _payload({
        "success": True, "taskId": "1", "updatedFields": ["status"],
        "statusChange": {"from": "pending", "to": "in_progress"},
    }))
    assert result["todo"] == {"success": True, "task_id": "1", "updated_fields": ["status"],
                               "status_change": {"from": "pending", "to": "in_progress"}}


def test_todo_facet_task_create_and_list():
    created = extract_result("TaskCreate", _payload({"task": {"id": "1", "subject": "x"}}))
    listed = extract_result("TaskList", _payload({"tasks": []}))
    assert created["todo"] == {"task": {"id": "1", "subject": "x"}}
    assert listed["todo"] == {"tasks": []}


# --- skill / search / worktree / message / findings / monitor facets ---------------------------

def test_skill_facet():
    result = extract_result("Skill", _payload({
        "success": True, "commandName": "superpowers:brainstorming", "allowedTools": ["Read"],
    }))
    assert result["skill"] == {"success": True, "command_name": "superpowers:brainstorming",
                                "allowed_tools": ["Read"]}


def test_search_facet_matches_kept_as_a_list_not_a_count():
    result = extract_result("ToolSearch", _payload({
        "matches": ["WebSearch", "WebFetch"], "query": "select:WebSearch,WebFetch",
        "total_deferred_tools": 35,
    }))
    assert result["search"]["matches"] == ["WebSearch", "WebFetch"]
    assert result["search"]["query"] == "select:WebSearch,WebFetch"
    assert result["search"]["total_deferred_tools"] == 35


def test_worktree_facet_enter_and_exit():
    entered = extract_result("EnterWorktree", _payload({
        "worktreePath": "/repo/.claude/worktrees/x", "worktreeBranch": "worktree-x",
        "message": "Created worktree…",
    }))
    exited = extract_result("ExitWorktree", _payload({
        "action": "keep", "originalCwd": "/repo", "worktreePath": "/repo/.claude/worktrees/x",
        "worktreeBranch": "worktree-x", "message": "Exited worktree…",
    }))
    assert entered["worktree"] == {"path": "/repo/.claude/worktrees/x", "branch": "worktree-x",
                                    "message": "Created worktree…"}
    assert exited["worktree"] == {"path": "/repo/.claude/worktrees/x", "branch": "worktree-x",
                                   "action": "keep", "original_cwd": "/repo",
                                   "message": "Exited worktree…"}


def test_message_facet_sendmessage_body_is_unmapped_not_dropped():
    result = extract_result("SendMessage", _payload({
        "success": True, "message": "Agent had no active task…", "resumedAgentId": "a1",
        "pin": {"id": "a1", "name": "a1", "ref": "abc"},
    }))
    assert result["message"] == {"success": True, "resumed_agent_id": "a1",
                                  "pin": {"id": "a1", "name": "a1", "ref": "abc"}}
    assert result["unmapped"] == {"message": "Agent had no active task…"}


def test_findings_facet():
    result = extract_result("ReportFindings", _payload({"count": 2, "findings": [{"a": 1}]}))
    assert result["findings"] == {"count": 2, "items": [{"a": 1}]}


def test_monitor_facet():
    result = extract_result("Monitor", _payload({
        "taskId": "t1", "timeoutMs": 1200000, "persistent": False,
    }))
    assert result["monitor"] == {"task_id": "t1", "timeout_ms": 1200000, "persistent": False}


# --- MCP -----------------------------------------------------------------------------------

def test_mcp_resolves_via_mcpmeta_structured_content_not_json_loads():
    payload = {
        "is_error": False, "tool_denial_kind": None,
        "raw": {
            "toolUseResult": '{"fileContent": "WRONG -- should not be parsed"}',
            "mcpMeta": {"structuredContent": {"fileContent": "right"}},
        },
    }
    result = extract_result("mcp__claude_ai_Google_Drive__read_file_content", payload,
                             mcp_server="claude_ai_Google_Drive", mcp_tool="read_file_content")
    assert result["mcp"] == {"server": "claude_ai_Google_Drive", "tool": "read_file_content",
                              "structured_content": {"fileContent": "right"}}
    assert "unmapped" not in result


def test_mcp_falls_back_to_json_loads_when_no_mcpmeta():
    payload = {
        "is_error": False, "tool_denial_kind": None,
        "raw": {"toolUseResult": '{"fileContent": "from json"}'},
    }
    result = extract_result("mcp__claude_ai_Google_Drive__read_file_content", payload)
    assert result["mcp"]["structured_content"] == {"fileContent": "from json"}


def test_mcpmeta_string_never_appears_in_output():
    payload = {
        "is_error": False, "tool_denial_kind": None,
        "raw": {"toolUseResult": '{"fileContent": "x"}',
                "mcpMeta": {"structuredContent": {"fileContent": "x"}}},
    }
    result = extract_result("mcp__claude_ai_Google_Drive__read_file_content", payload)
    assert "mcpMeta" not in str(result)


# --- unmapped hatch --------------------------------------------------------------------------

def test_unknown_vendor_key_lands_in_unmapped_with_exactly_one_warn():
    payload = _payload({
        "stdout": "", "stderr": "", "interrupted": False, "isImage": False,
        "noOutputExpected": False, "someNewVendorKey": "surprise",
    })
    with capture_logs() as logs:
        result = extract_result("Bash", payload)
    assert result["unmapped"] == {"someNewVendorKey": "surprise"}
    warnings = [e for e in logs if e.get("log_level") == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["event"] == "result_unmapped_key"
    assert warnings[0]["tool"] == "Bash"
    assert warnings[0]["key"] == "someNewVendorKey"


# --- skip list: reaches neither a facet nor unmapped -------------------------------------------

def test_skip_list_keys_reach_neither_facet_nor_unmapped():
    with capture_logs() as logs:
        result = extract_result("Edit", _payload({
            "filePath": "/repo/x.py", "oldString": "old", "newString": "new",
            "originalFile": "orig", "structuredPatch": [{"lines": []}],
            "userModified": False, "replaceAll": False,
        }))
    assert "unmapped" not in result
    assert result["file"] == {"user_modified": False, "replace_all": False}
    assert not [e for e in logs if e.get("log_level") == "warning"]


def test_skip_list_task_totals_and_content_no_unmapped_no_warn():
    with capture_logs() as logs:
        result = extract_result("Task", _payload({
            "status": "completed", "agentType": "general-purpose",
            "content": [{"type": "text", "text": ""}], "resolvedModel": "claude-fable-5",
            "totalTokens": 16372, "usage": {"input_tokens": 2},
        }))
    assert "unmapped" not in result
    assert "delegation" not in result  # every key here is skipped -- nothing to facet
    assert not [e for e in logs if e.get("log_level") == "warning"]
