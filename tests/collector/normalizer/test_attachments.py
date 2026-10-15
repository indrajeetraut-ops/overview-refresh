"""Attachment bodies and artifact classification (spec: docs/modules/normalizer.md §6.6, §10).

An `attachment` record is context injected outside the message flow -- @-mentioned files, the
skill listing, hook output, IDE state. They carry the largest bodies in the transcript
(1,768,946 chars in the reference corpus, of which skill listings are 1.19M and the largest
single `file` is 66,739 chars), and until now shipped verbatim as `unknown`, which defeated the
Write/Edit scrubbing sitting two records away.

Body length is policy (`attachment_len`, per-type overrides); which fields hold the body is
structural (`_ATTACHMENT_CONTENT_FIELDS`). Metadata survives the cap unconditionally, because
"which skill was in context here" must stay answerable after the prose is gone.
"""
import json

import pytest

from collector.normalizer.claude_code import ClaudeCodeNormalizer
from collector.normalizer.config import NormalizerConfig
from fixtures.normalizer import sessions


def _run(config, build=sessions.attachment_session):
    (session,) = list(ClaudeCodeNormalizer(config).normalize(build()))
    return session


def _by_type(session, atype):
    return next(e for e in session.events if e.event_type == "attachment" and e.payload["attachment_type"] == atype)


def _serialized(session):
    return json.dumps([e.model_dump() for e in session.events])


# -- promoted out of `unknown` -------------------------------------------------------------


def test_attachment_is_its_own_event_type(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.attachment_session())
    kinds = [e.payload["attachment_type"] for e in session.events if e.event_type == "attachment"]
    assert kinds == [
        "file", "edited_text_file", "skill_listing", "hook_success",
        "some_future_attachment", "opened_file_in_ide",
    ]
    assert not [e for e in session.events if e.event_type == "unknown"]


def test_spine_intact(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.attachment_session())
    known = {e.record_id for e in session.events if e.record_id is not None}
    assert [e.event_type for e in session.events if e.parent_id and e.parent_id not in known] == []


# -- the cap ------------------------------------------------------------------------------


def test_default_config_drops_file_and_skill_bodies():
    """Defaults set these types to 0: they are user repo content, and a 1k head-truncation of a
    source file keeps its most identifying kilobyte."""
    session = _run(NormalizerConfig())
    blob = _serialized(session)
    assert sessions.SECRET_FILE_BODY[:80] not in blob
    assert sessions.SECRET_SNIPPET[:80] not in blob
    assert sessions.SECRET_SKILL_PROSE[:80] not in blob


def test_unmapped_type_is_dropped_by_default_not_merely_clipped():
    """attachment_len_unknown_type defaults to 0, and deliberately does not follow
    attachment_len: a 1000-char head of a field nobody has classified is still 1000 chars of
    something nobody has looked at."""
    session = _run(NormalizerConfig())
    event = _by_type(session, "some_future_attachment")
    assert event.payload["raw"]["attachment"]["payloadText"] == ""
    assert event.payload["content_truncated"] is True
    assert event.payload["content_chars"] > 0  # size still reported


def test_unmapped_type_cap_is_independently_configurable():
    """Raising it is an explicit choice, and does not follow attachment_len."""
    session = _run(NormalizerConfig(attachment_len=-1, attachment_len_unknown_type=100))
    event = _by_type(session, "some_future_attachment")
    assert len(event.payload["raw"]["attachment"]["payloadText"]) == 100


def test_short_fields_are_not_clipped():
    session = _run(NormalizerConfig(attachment_len_unknown_type=100))
    event = _by_type(session, "some_future_attachment")
    assert event.payload["raw"]["attachment"]["shortLabel"] == "fine"


def test_len_zero_empties_the_body_but_keeps_the_record():
    session = _run(NormalizerConfig(attachment_len=0, attachment_len_by_type={}))
    event = _by_type(session, "file")
    nested = event.payload["raw"]["attachment"]["content"]["file"]
    assert nested["content"] == ""
    assert nested["numLines"] == 120  # metadata survives regardless
    assert nested["filePath"] == "/Users/dev/repo/src/pricing.py"


def test_len_minus_one_keeps_everything():
    session = _run(NormalizerConfig(attachment_len=-1, attachment_len_by_type={}))
    event = _by_type(session, "file")
    assert event.payload["raw"]["attachment"]["content"]["file"]["content"] == sessions.SECRET_FILE_BODY
    assert event.payload["content_truncated"] is False


def test_per_type_override_beats_the_global_default():
    session = _run(NormalizerConfig(attachment_len=-1, attachment_len_by_type={"skill_listing": 0}))
    assert _by_type(session, "skill_listing").payload["raw"]["attachment"]["content"] == ""
    # the global -1 still applies to everything else
    assert _by_type(session, "file").payload["raw"]["attachment"]["content"]["file"]["content"]


def test_truncation_is_recorded_with_original_size():
    """A clipped body must never look like a short one."""
    session = _run(NormalizerConfig(attachment_len=50, attachment_len_by_type={}))
    event = _by_type(session, "file")
    assert event.payload["content_chars"] == len(sessions.SECRET_FILE_BODY)
    assert event.payload["content_truncated"] is True
    assert len(event.payload["raw"]["attachment"]["content"]["file"]["content"]) == 50


def test_untruncated_body_reports_not_truncated():
    session = _run(NormalizerConfig(attachment_len=-1, attachment_len_by_type={}))
    assert _by_type(session, "hook_success").payload["content_truncated"] is False


def test_metadata_survives_a_zero_cap():
    """The point of capping rather than dropping: "which skills were in context" stays
    answerable from `names`/`skillCount` after the prose is gone."""
    session = _run(NormalizerConfig())
    att = _by_type(session, "skill_listing").payload["raw"]["attachment"]
    assert att["skillCount"] == 3
    assert att["names"] == ["my-arch-doc", "brainstorming", "writing-plans"]


def test_multi_field_type_caps_every_body_field():
    session = _run(NormalizerConfig(attachment_len=30, attachment_len_by_type={}))
    event = _by_type(session, "hook_success")
    att = event.payload["raw"]["attachment"]
    assert len(att["stdout"]) == 30
    assert att["exitCode"] == 0 and att["hookName"] == "SessionStart:startup"


# -- artifact classification --------------------------------------------------------------


def test_attachment_path_is_classified(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.attachment_session())
    assert _by_type(session, "file").payload["artifact_class"] == "code"
    assert _by_type(session, "file").payload["extension"] == ".py"
    assert _by_type(session, "edited_text_file").payload["artifact_class"] == "documentation"


def test_dotfile_classified_by_basename(normalizer, normalize_one):
    """Path('.gitignore').suffix is '' -- no suffix map can reach it."""
    session = normalize_one(normalizer, sessions.attachment_session())
    event = _by_type(session, "opened_file_in_ide")
    assert event.payload["extension"] == ""
    assert event.payload["artifact_class"] == "config"


def test_pathless_attachment_has_null_class(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.attachment_session())
    assert _by_type(session, "skill_listing").payload["artifact_class"] is None


def test_file_change_and_tool_call_files_are_classified(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    change = next(e for e in session.events if e.event_type == "file_change")
    assert (change.payload["extension"], change.payload["artifact_class"]) == (".py", "code")
    edit = next(e for e in session.events if e.payload.get("tool_name") == "Edit")
    assert edit.payload["files"][0]["artifact_class"] == "code"


def test_unknown_extension_classifies_as_unknown_not_dropped(normalizer, normalize_one):
    src = sessions.tool_pairing_session()
    src.records[3]["message"]["content"][0]["input"]["file_path"] = "src/thing.wat"
    session = normalize_one(normalizer, src)
    change = next(e for e in session.events if e.event_type == "file_change")
    assert change.payload["artifact_class"] == "unknown"
    assert change.payload["extension"] == ".wat"  # raw suffix always survives the label


def test_taxonomy_is_config_driven(normalize_one):
    config = NormalizerConfig(artifact_class_by_extension={"infrastructure": [".py"]})
    (session,) = list(ClaudeCodeNormalizer(config).normalize(sessions.tool_pairing_session()))
    change = next(e for e in session.events if e.event_type == "file_change")
    assert change.payload["artifact_class"] == "infrastructure"


# -- remote artifacts (MCP) ----------------------------------------------------------------


def test_mime_types_extracted_from_mcp_result(normalizer, normalize_one):
    """These calls produce no file_change, so this is the only record that a Google Doc was
    touched. Extracted before the scrub removes toolUseResult -- there is no second copy."""
    session = normalize_one(normalizer, sessions.mcp_remote_artifact_session())
    call = next(e for e in session.events if e.event_type == "tool_call")
    classes = [(f["artifact_class"], f["mime_type"]) for f in call.payload["files"]]
    assert classes == [
        ("document", "application/vnd.google-apps.document"),
        ("spreadsheet", "application/vnd.google-apps.spreadsheet"),
    ]


def test_remote_artifact_keeps_id_and_size(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.mcp_remote_artifact_session())
    call = next(e for e in session.events if e.event_type == "tool_call")
    first = call.payload["files"][0]
    assert first["remote_id"] == "1dnZAgqNL4-uVvBTE6EenoNCgtSwlccDG"
    assert first["bytes"] == 5860  # numeric string coerced
    assert first["path"] is None


def test_remote_title_owner_and_url_are_not_promoted_to_the_payload(normalizer, normalize_one):
    """The `files[]` entry stays identity-only: id, size, mime. Title and owner are not
    promoted beside them -- the payload is what gets indexed and queried, and a doc title and
    an owner's email address are not things to build an index on.

    They do survive in `raw` (see the test below), which is the deliberate half of the
    content-vs-fact split: `raw` is the record as it happened."""
    session = normalize_one(normalizer, sessions.mcp_remote_artifact_session())
    call = next(e for e in session.events if e.event_type == "tool_call")
    promoted = json.dumps(call.payload["files"])
    assert sessions.DRIVE_DOC_TITLE not in promoted
    assert sessions.DRIVE_OWNER_EMAIL not in promoted
    assert "docs.google.com" not in promoted


def test_remote_file_metadata_survives_in_raw(normalizer, normalize_one):
    """An MCP listing is metadata end to end -- no file body anywhere in it -- and under the
    scrub this replaced the whole 2,459-byte result was reduced to the number `2459` in
    `bytes_read`. Which Drive files a session touched was unanswerable.

    Nothing here is a copy of a file, so nothing here is cut. An org that considers an owner's
    email address too much can drop it with a `redaction_patterns` entry; that is a policy
    decision with a switch, not a default baked into the scrub."""
    session = normalize_one(normalizer, sessions.mcp_remote_artifact_session())
    result = next(e for e in session.events if e.event_type == "tool_result")
    files = result.payload["raw"]["toolUseResult"]["files"]
    assert files[0]["title"] == sessions.DRIVE_DOC_TITLE
    assert files[0]["owner"] == sessions.DRIVE_OWNER_EMAIL
    assert files[0]["fileSize"] == "5860"
    assert files[0]["mimeType"] == "application/vnd.google-apps.document"


def test_local_files_keep_null_mime(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    edit = next(e for e in session.events if e.payload.get("tool_name") == "Edit")
    assert edit.payload["files"][0]["mime_type"] is None


# -- the whole corpus of fixtures ----------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        sessions.SECRET_FILE_BODY[:60],
        sessions.SECRET_SNIPPET[:60],
        sessions.SECRET_SKILL_PROSE[:60],
        "leaked-future-content leaked-future-content",
    ],
)
def test_no_attachment_body_leaks_under_defaults(secret):
    session = _run(NormalizerConfig())
    assert secret not in _serialized(session)


# -- identity promotion (§6.12) ------------------------------------------------------------


def test_is_startup_context_from_session_start_hook(normalizer, normalize_one):
    """Measured: CLAUDE.md reaches the model through the SessionStart hook (8 occurrences),
    never as a `file` attachment. Without this flag, startup context is only distinguishable
    from a mid-session @-mention by record position."""
    session = normalize_one(normalizer, sessions.attachment_session())
    assert _by_type(session, "hook_success").payload["is_startup_context"] is True
    assert _by_type(session, "file").payload["is_startup_context"] is False


def test_is_startup_context_from_is_initial(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.attachment_session())
    assert _by_type(session, "skill_listing").payload["is_startup_context"] is True


def test_skill_identity_survives_a_zero_body(normalizer, normalize_one):
    """The whole point of capping rather than dropping: "which skills were in context" stays
    answerable on the payload, without reaching into raw."""
    session = normalize_one(normalizer, sessions.attachment_session())
    event = _by_type(session, "skill_listing")
    assert event.payload["skill_names"] == ["my-arch-doc", "brainstorming", "writing-plans"]
    assert event.payload["skill_count"] == 3
    assert event.payload["raw"]["attachment"]["content"] == ""


def test_hook_identity_promoted(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.attachment_session())
    event = _by_type(session, "hook_success")
    assert event.payload["hook_name"] == "SessionStart:startup"
    assert event.payload["hook_event"] == "SessionStart"
    assert event.payload["exit_code"] == 0
    assert event.payload["duration_ms"] == 283


def test_file_identity_promoted(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.attachment_session())
    event = _by_type(session, "file")
    assert event.payload["display_path"] == "src/pricing.py"
    assert event.payload["total_lines"] == 120
    assert event.payload["content_chars"] == len(sessions.SECRET_FILE_BODY)


def test_unmapped_type_gets_no_invented_metadata(normalizer, normalize_one):
    """A type absent from the metadata map still emits the universal fields and nothing else."""
    session = normalize_one(normalizer, sessions.attachment_session())
    payload = _by_type(session, "some_future_attachment").payload
    assert payload["attachment_type"] == "some_future_attachment"
    assert payload["is_startup_context"] is False
    assert "skill_names" not in payload and "hook_name" not in payload


# -- subagent directive --------------------------------------------------------------------


def test_subagent_directive_promoted(normalizer):
    """The Task prompt is what a subagent was told to do -- the label for its work and the
    intent behind it. Corpus: 124 spawns, prompt median 2,947 chars, description median 27."""
    parent = next(iter(normalizer.normalize(sessions.mixed_model_subagent_session())))
    spawn = next(e for e in parent.events if e.record_id == "toolu_spawn_sonnet")
    assert spawn.payload["description"] == "Explore the auth module"
    assert spawn.payload["prompt_char_count"] == len(sessions.SUBAGENT_DIRECTIVE)
    assert spawn.payload["requested_model"] == "sonnet"
    assert spawn.payload["run_in_background"] is False


def test_subagent_prompt_is_not_capped(normalizer):
    """Uncapped in raw for the same reason typed prompts are: it is intent, not injected
    content. The assistant text cap targets `text` blocks, not tool_use inputs."""
    parent = next(iter(normalizer.normalize(sessions.mixed_model_subagent_session())))
    spawn = next(e for e in parent.events if e.record_id == "toolu_spawn_sonnet")
    block = next(
        b for b in spawn.payload["raw"]["message"]["content"]
        if isinstance(b, dict) and b.get("id") == "toolu_spawn_sonnet"
    )
    assert block["input"]["prompt"] == sessions.SUBAGENT_DIRECTIVE


def test_spawn_without_directive_fields_is_null_not_missing(normalizer):
    parent = next(iter(normalizer.normalize(sessions.mixed_model_subagent_session())))
    spawn = next(e for e in parent.events if e.record_id == "toolu_spawn_haiku")
    assert spawn.payload["description"] == "Draft the migration plan"
    assert spawn.payload["prompt_char_count"] is None
    assert spawn.payload["requested_model"] is None


def test_spawn_raw_uses_the_scrubbed_passthrough(normalizer, normalize_one):
    """Regression: _handle_spawn took the original record, so a spawn sharing its assistant
    record with a long text block or an Edit shipped content every sibling event had scrubbed."""
    src = sessions.simple_session()
    src.records[1]["message"]["content"] = [
        {"type": "text", "text": "LEAKMARKER " * 400},
        {"type": "tool_use", "id": "toolu_t", "name": "Task", "input": {"subagent_type": "Explore"}},
    ]
    session = normalize_one(normalizer, src)
    spawn = next(e for e in session.events if e.event_type == "sub_agent_spawn")
    text_block = next(b for b in spawn.payload["raw"]["message"]["content"] if b.get("type") == "text")
    assert len(text_block["text"]) == 1000
