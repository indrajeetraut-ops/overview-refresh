"""The tool_result record ships as its own event (spec: docs/modules/normalizer.md §3.3, §8, §9).

Before this, _handle_user merged the result into its tool_call and returned without emitting,
so the record was dropped whole. Two things broke: the uuid/parentUuid spine (the next
assistant record's parentUuid pointed at a record no event carried, leaving ~16% of assistant
events with a dangling parent) and every rejection (a denied call has no toolUseResult at all,
so the merge had nothing to write and the denial was invisible).

The tests here fix both in place, and pin the redaction boundary that makes emitting the record
safe. That boundary is content-vs-fact, not result-vs-not-result: what a call *produced* --
exit codes, git operations, file paths, hunk geometry -- ships; copies of the user's files do
not. Both halves are config (`tool_result_content_len`, `tool_result_len`), and the tests below
pin the shipped defaults as well as the fact that the knobs move.
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
    sessions.file_read_session,
    sessions.image_and_plan_session,
    sessions.mcp_json_string_session,
]


def _tool_results(session):
    return [e for e in session.events if e.event_type == "tool_result"]


# -- emission and identity ----------------------------------------------------------------


def test_one_event_per_tool_result_block(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    assert [e.payload["tool_use_id"] for e in _tool_results(session)] == ["toolu_bash1", "toolu_edit1"]


def test_record_id_is_the_record_uuid_not_the_tool_use_id(normalizer, normalize_one):
    """Load-bearing: the next assistant record's parentUuid points at the *record* uuid, so
    keying on the tool_use id instead would leave that parent dangling."""
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    bash_result = _tool_results(session)[0]
    assert bash_result.record_id == "evt-3"
    assert bash_result.parent_id == "evt-2"
    assert bash_result.payload["tool_use_id"] == "toolu_bash1"


def test_carries_its_own_timestamp(normalizer, normalize_one):
    """The result's timestamp, not the call's -- the merge into tool_call has nowhere to put
    it, and it's the only way to separate execution time from permission-wait."""
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    call = next(e for e in session.events if e.event_type == "tool_call")
    result = _tool_results(session)[0]
    assert call.event_ts == "2026-07-09T14:00:03.000Z"
    assert result.event_ts == "2026-07-09T14:00:07.000Z"


def test_source_tool_assistant_uuid_survives(normalizer, normalize_one):
    """The direct result->call edge, independent of parentUuid and tool_use_id."""
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    assert [e.payload["source_tool_assistant_uuid"] for e in _tool_results(session)] == ["evt-2", "evt-4"]


# -- the spine ----------------------------------------------------------------------------


@pytest.mark.parametrize("build", ALL_BUILDERS, ids=lambda b: b.__name__)
def test_every_parent_id_resolves(normalizer, build):
    """Regression guard for the dangling-parent rate. Holds across subagent streams too --
    each yielded session is checked against its own record_ids."""
    for session in normalizer.normalize(build()):
        known = {e.record_id for e in session.events if e.record_id is not None}
        dangling = [
            (e.event_type, e.parent_id) for e in session.events if e.parent_id is not None and e.parent_id not in known
        ]
        assert dangling == [], f"{session.session_id}: unresolved parent_id -> {dangling}"


# -- rejections ---------------------------------------------------------------------------


def test_denied_call_emits_result_with_denial_kind(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.denied_tool_session())
    (denied,) = _tool_results(session)
    assert denied.payload["tool_denial_kind"] == "user-rejected"
    assert denied.payload["is_error"] is True
    assert denied.payload["tool_use_id"] == "toolu_rm1"


def test_denied_call_has_no_tool_use_result_and_does_not_crash(normalizer, normalize_one):
    """A denial carries no toolUseResult, so the merge falls back to is_error and leaves
    duration null -- the tool never ran."""
    session = normalize_one(normalizer, sessions.denied_tool_session())
    call = next(e for e in session.events if e.event_type == "tool_call")
    assert call.payload["exit_status"] == 1
    assert call.payload["duration_ms"] is None


def test_ran_call_has_null_denial_kind(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    assert all(e.payload["tool_denial_kind"] is None for e in _tool_results(session))


# -- redaction: the seam ------------------------------------------------------------------


def test_facts_beside_a_body_survive(normalizer):
    """A container under a content name keeps its facts. Read's result is `{file: {content,
    filePath, numLines, totalLines}}` -- the body goes, the three facts stay.

    Regression guard for a real bug: propagating the content policy into the subtree emptied
    `filePath` on 442 of 456 Read results in the corpus, which is precisely the fact this
    walk exists to keep. Every key decides by its own name, and none inherits."""
    read = ClaudeCodeNormalizer()
    (session,) = list(read.normalize(sessions.file_read_session()))
    (result,) = _tool_results(session)
    f = result.payload["raw"]["toolUseResult"]["file"]
    assert f["content"] == ""
    assert f["filePath"] == "/repo/src/auth.py"
    assert f["numLines"] == 100
    assert f["totalLines"] == 415


def test_result_facts_survive_into_raw(normalizer):
    """The seam is content-vs-fact, not result-vs-not-result.

    The scrub this replaced removed `toolUseResult` whole, on the theory that everything under
    it was output. It is not: exitCode, durationMs and a hunk's geometry are facts about what
    happened, and they were going out with the file bodies."""
    (session,) = list(normalizer.normalize(sessions.tool_pairing_session()))
    results = [e.payload["raw"]["toolUseResult"] for e in _tool_results(session)]
    bash, edit = results
    assert bash["exitCode"] == 1
    assert bash["durationMs"] == 850
    assert edit["structuredPatch"][0] == {
        "oldStart": 10,
        "oldLines": 1,
        "newStart": 10,
        "newLines": 1,
        "lines": [],  # the geometry stays, the diff body goes
    }


@pytest.mark.parametrize("build", ALL_BUILDERS, ids=lambda b: b.__name__)
def test_file_bodies_never_reach_raw_at_default_config(normalizer, build):
    """Every _TOOL_RESULT_CONTENT_KEYS name, wherever it appears in the result tree, is empty
    at the shipped `tool_result_content_len: 0`. Walking by name is what reaches a nested body
    -- `file.content` goes while `file.filePath` stays."""
    from collector.normalizer.claude_code import _TOOL_RESULT_CONTENT_KEYS

    def walk(node, path=()):
        if isinstance(node, dict):
            for k, v in node.items():
                # Containers under a content name are kept as containers -- `file` stays a
                # dict so filePath/numLines survive. Only the string bodies must be gone, so
                # asserting on the container would pass vacuously.
                if k in _TOOL_RESULT_CONTENT_KEYS and not isinstance(v, dict):
                    assert not v, f"{'.'.join(path + (k,))} kept a body: {v!r}"
                walk(v, path + (k,))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + (str(i),))

    for session in normalizer.normalize(build()):
        for event in session.events:
            walk(event.payload["raw"].get("toolUseResult"), ("toolUseResult",))
            walk(event.payload["raw"].get("mcpMeta"), ("mcpMeta",))


@pytest.mark.parametrize("build", ALL_BUILDERS, ids=lambda b: b.__name__)
def test_tool_result_block_content_is_dropped(normalizer, build):
    """The block body is the same text the model saw, under a `content` key -- so the same
    policy reaches it, with no separate rule."""
    for session in normalizer.normalize(build()):
        for event in session.events:
            message = event.payload["raw"].get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    body = block["content"]
                    if isinstance(body, list):
                        # A structured body (a list of blocks, e.g. an image) keeps its shape
                        # so the block types stay readable; the leaves are what must be empty.
                        assert all(not v for b in body for k, v in b.items()
                                   if k in ("text", "data") or isinstance(v, str) and k == "content")
                        assert all(not (b.get("source") or {}).get("data") for b in body)
                    else:
                        assert not body


@pytest.mark.parametrize(
    "secret",
    [
        "def f():\n    return 1",  # Edit originalFile -- a copy of the user's source
        "Explored the auth module; found 3 entry points.",  # subagent report, under `content`
        "The user doesn't want to proceed with this tool use.",  # denial message, block content
    ],
)
def test_known_bodies_appear_nowhere(normalizer, secret):
    for build in ALL_BUILDERS:
        for session in normalizer.normalize(build()):
            serialized = json.dumps([e.model_dump() for e in session.events])
            assert secret not in serialized, f"{build.__name__}/{session.session_id} leaked: {secret!r}"


def test_non_content_strings_are_capped_not_kept_whole(normalizer):
    """The default-deny half of the policy. A key nobody has classified -- a future Claude
    Code version's -- is not a body key, so it takes `tool_result_len`. Capped, not unbounded:
    that is what stops a new key holding a whole file from arriving silently."""
    tight = ClaudeCodeNormalizer(NormalizerConfig(tool_result_len=10))
    src = sessions.tool_pairing_session()
    (session,) = list(tight.normalize(src))
    stdout = session.events and _tool_results(session)[0].payload["raw"]["toolUseResult"]["stdout"]
    assert stdout == "1 failed, " + "…[TRUNCATED]"


def test_bodies_are_not_gated_on_redact_generated_file_content():
    """That flag governs generated *source* in a Write/Edit input. Result bodies are a
    separate decision on a separate knob, so turning it off must not expose them."""
    permissive = ClaudeCodeNormalizer(NormalizerConfig(redact_generated_file_content=False))
    (session,) = list(permissive.normalize(sessions.tool_pairing_session()))
    serialized = json.dumps([e.model_dump() for e in session.events])
    assert "def f():\n    return 1" not in serialized


def test_content_len_is_a_switch_in_both_directions():
    """Every decision in this scrub is config. -1 keeps a body whole -- an operator who wants
    result content can have it, and that is a choice they make explicitly rather than a
    default they inherit."""
    keep = ClaudeCodeNormalizer(NormalizerConfig(tool_result_content_len=-1))
    (session,) = list(keep.normalize(sessions.tool_pairing_session()))
    edit = _tool_results(session)[1].payload["raw"]["toolUseResult"]
    assert edit["originalFile"] == "def f():\n    return 1"


# -- derivation still happens (toolUseResult is read before it is scrubbed) ----------------


def test_merge_into_tool_call_unaffected(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    bash_call = next(e for e in session.events if e.payload.get("tool_name") == "Bash")
    assert bash_call.payload["exit_status"] == 1
    assert bash_call.payload["duration_ms"] == 850
    assert bash_call.payload["bytes_read"] == len("1 failed, 4 passed".encode("utf-8"))

    edit_call = next(e for e in session.events if e.payload.get("tool_name") == "Edit")
    assert edit_call.payload["files"][0]["lines_added"] == 1  # exact +/- counts, restored 2026-08-30
    assert edit_call.payload["files"][0]["hunks"] == [{"start": 10, "len": 1, "old_len": 1}]


# -- subagent outcome ---------------------------------------------------------------------


def test_task_result_folds_outcome_back_onto_spawn(normalizer):
    parent = next(iter(normalizer.normalize(sessions.mixed_model_subagent_session())))
    spawn = next(e for e in parent.events if e.record_id == "toolu_spawn_sonnet")
    assert spawn.payload["status"] == "completed"
    assert spawn.payload["resolved_model"] == "claude-sonnet-5"
    assert spawn.payload["duration_ms"] == 75000
    assert spawn.payload["child_session_id"] == "agent-sonnet-1"


def test_spawn_with_no_result_keeps_null_placeholders(normalizer):
    """toolu_spawn_haiku's result never folds back -- the subagent ran but the parent never
    learned the outcome. Null, not absent, and never a crash (§8)."""
    parent = next(iter(normalizer.normalize(sessions.mixed_model_subagent_session())))
    spawn = next(e for e in parent.events if e.record_id == "toolu_spawn_haiku")
    assert spawn.payload["status"] is None
    assert spawn.payload["resolved_model"] is None
    assert spawn.payload["duration_ms"] is None
    assert spawn.payload["child_session_id"] == "agent-haiku-1"


def test_task_result_still_emits_its_own_tool_result_event(normalizer):
    parent = next(iter(normalizer.normalize(sessions.mixed_model_subagent_session())))
    (result,) = [e for e in parent.events if e.event_type == "tool_result"]
    assert result.payload["tool_use_id"] == "toolu_spawn_sonnet"
    assert result.payload["is_error"] is False


# -- images, intent, and the signals that were being dropped --------------------------------


def test_screenshot_base64_is_dropped_not_capped(normalizer, normalize_one):
    """A truncated PNG is content and is not even a usable image. Both places a screenshot
    lands are governed: `toolUseResult.file.base64` and the image block nested inside the
    tool_result block's own content list, which only a recursive walk reaches.

    Capping instead shipped 31 x 1,012 chars of base64 across the reference corpus."""
    session = normalize_one(normalizer, sessions.image_and_plan_session())
    result = _tool_results(session)[0]
    raw = result.payload["raw"]
    assert raw["toolUseResult"]["file"]["base64"] == ""
    assert raw["toolUseResult"]["file"]["type"] == "image/png"  # the fact beside it survives
    assert raw["toolUseResult"]["file"]["originalSize"] == 48213
    block = raw["message"]["content"][0]
    assert block["content"][0]["source"]["data"] == ""
    assert block["content"][0]["source"]["media_type"] == "image/png"
    assert sessions.SCREENSHOT_B64[:64] not in json.dumps(result.model_dump())


def test_plan_text_survives_whole(normalizer, normalize_one):
    """A plan records what was being *attempted* -- not a tool's output, and nobody's file.
    Clipping it at tool_result_len would lose the part that says what it decided, so it takes
    tool_result_intent_len, which is -1."""
    session = normalize_one(normalizer, sessions.image_and_plan_session())
    plan_result = _tool_results(session)[1]
    plan = plan_result.payload["raw"]["toolUseResult"]["plan"]
    assert plan == sessions.PLAN_TEXT
    assert len(plan) > 2000  # comfortably past the 1000-char tool_result_len
    assert not plan.endswith("…[TRUNCATED]")


def test_intent_length_is_a_switch():
    """Kept whole by default, but still config -- an operator who wants plans clipped can."""
    tight = ClaudeCodeNormalizer(NormalizerConfig(tool_result_intent_len=50))
    (session,) = list(tight.normalize(sessions.image_and_plan_session()))
    plan = _tool_results(session)[1].payload["raw"]["toolUseResult"]["plan"]
    assert plan == sessions.PLAN_TEXT[:50] + "…[TRUNCATED]"


def test_permission_mode_and_plan_mode_are_derived(normalizer, normalize_one):
    """`plan_mode` was permanently null behind a TODO that assumed a correlation with
    neighbouring `mode` records was needed. The vendor stamps `permissionMode` on the prompt
    record itself, so no correlation is required."""
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    (user,) = [e for e in session.events if e.event_type == "user_message"]
    assert user.payload["permission_mode"] == "plan"
    assert user.payload["plan_mode"] is True


def test_plan_mode_is_null_not_false_when_the_vendor_said_nothing(normalizer, normalize_one):
    """Unknown and not-plan-mode are different claims. A record from before the vendor emitted
    permissionMode must not read as 'this was not planning'."""
    session = normalize_one(normalizer, sessions.simple_session())
    (user,) = [e for e in session.events if e.event_type == "user_message"]
    assert user.payload["permission_mode"] is None
    assert user.payload["plan_mode"] is None


def test_attribution_is_captured(normalizer, normalize_one):
    """Which skill/plugin produced a record. Distinct from the `skill_listing` attachment,
    which only says which skills were *offered* in context -- this says which one was in
    force. Record-level: measured over 43,034 events, none of these keys ever appears on a
    record carrying a promptId, so a turn cannot hold them."""
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    first = next(e for e in session.events if e.event_type == "assistant_message")
    assert first.payload["attribution_skill"] == "superpowers:brainstorming"
    assert first.payload["attribution_plugin"] == "superpowers"
    assert first.payload["effort"] == "high"


def test_subagent_agent_type_lands_on_the_session(normalizer, normalize_one):
    """attributionAgent is 100% sidechain in the corpus (16,659/16,659), so it names the agent
    type of a stream rather than a property of one record."""
    session = normalize_one(normalizer, sessions.image_and_plan_session())
    assistant = next(e for e in session.events if e.event_type == "assistant_message")
    assert assistant.payload["attribution_agent"] == "general-purpose"


def test_mcp_json_string_is_parsed_scrubbed_and_recoded(normalizer, normalize_one):
    """An MCP result arrives as a JSON string, so key-name walking cannot see inside it until
    it is parsed. Two outcomes, deliberately different:

      * `fileContent` -- a Drive document's whole body -- is a content key and is cut;
      * the metadata beside it is kept WHOLE, not clipped at tool_result_len. Clipping
        produced a mid-object truncation that was neither parseable nor useful.

    The string shape is preserved on the way out: reading it back is a consumer's decision.
    """
    session = normalize_one(normalizer, sessions.mcp_json_string_session())
    listing, doc, _err = _tool_results(session)

    files = json.loads(listing.payload["raw"]["toolUseResult"])["files"]
    assert files[0]["title"] == sessions.DRIVE_DOC_TITLE  # whole, not clipped
    assert files[0]["owner"] == sessions.DRIVE_OWNER_EMAIL
    assert len(files) == 2

    body = json.loads(doc.payload["raw"]["toolUseResult"])
    assert body["fileContent"] == ""  # the document body is gone
    assert body["mimeType"] == "application/vnd.google-apps.document"  # the fact stays


def test_non_json_string_results_stay_capped(normalizer, normalize_one):
    """Error text and rejection messages are bare strings, not JSON -- 378 of the 383 string
    results in the corpus. They are output like `stdout`, so they keep tool_result_len."""
    tight = ClaudeCodeNormalizer(NormalizerConfig(tool_result_len=20))
    (session,) = list(tight.normalize(sessions.mcp_json_string_session()))
    err = _tool_results(session)[2].payload["raw"]["toolUseResult"]
    assert err == "Error: Exit code 1 t" + "…[TRUNCATED]"


def test_mcp_len_is_a_switch(normalizer):
    """Whole by default, still config."""
    tight = ClaudeCodeNormalizer(NormalizerConfig(tool_result_mcp_len=30))
    (session,) = list(tight.normalize(sessions.mcp_json_string_session()))
    files = json.loads(_tool_results(session)[0].payload["raw"]["toolUseResult"])["files"]
    assert files[0]["title"].endswith("…[TRUNCATED]")


def test_master_session_never_takes_a_subagents_agent_type(normalizer):
    """A master's record stream still contains its subagents' records, so a first-wins backfill
    with no guard labelled 134 of 152 master sessions with the agent type of the first subagent
    they happened to spawn. A master must stay null -- that null is the discriminator."""
    sessions_out = list(normalizer.normalize(sessions.mixed_model_subagent_session()))
    master = next(s for s in sessions_out if s.session_id == "session-parent")
    start = next(e for e in master.events if e.event_type == "session_start")
    assert start.payload["parent_session_id"] is None
    assert start.payload["attribution_agent"] is None
