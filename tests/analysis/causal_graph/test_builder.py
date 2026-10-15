"""Behavioural tests for build_graph -- each asserts a transform that would fail if reverted.

Loads the hand-authored tiny_session fixture (one normalized session) and checks: assistant
collapse by message_id, tokens taken from the fb=true record (not summed), tool_call+result
merge, spawn ref carriage, turn segmentation + statuses, and zero dangling parent_id within
the session.
"""
import json
from pathlib import Path

import pytest

from analysis.causal_graph.builder import _build_graph_model as build_graph
from analysis.causal_graph.config import ReasoningConfig, VendorReasoningConfig
from analysis.causal_graph.models import Family, NodeType
from analysis.causal_graph.query import query
from analysis.causal_graph.query_config import MemoryClassifierConfig, load_query_config

FIXTURE = Path(__file__).parents[2] / "fixtures" / "causal_graph" / "tiny_session.json"


@pytest.fixture
def session() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def graph(session):
    return build_graph(session)


def _nodes_of(graph, node_type):
    return [n for n in graph.nodes.values() if n.type == node_type]


# --- graph identity --------------------------------------------------------

def test_graph_identity_from_session(graph):
    assert graph.session_id == "sess-tiny-0001"
    assert graph.vendor == "claude_code"
    assert graph.source == "jsonl"
    assert graph.built == "complete"
    assert graph.parent_session_id is None


def test_meta_title_from_session_start_objective_none(graph):
    assert graph.meta["title"] == "Add the read feature"
    assert graph.meta["objective"] is None


# --- assistant collapse ----------------------------------------------------

def test_assistant_collapse_to_unique_message_id_count(session, graph):
    records = [e for e in session["events"] if e["event_type"] == "assistant_message"]
    unique_ids = {e["payload"]["message_id"] for e in records}
    assistant_nodes = _nodes_of(graph, NodeType.ASSISTANT)
    # 3 assistant_message records, 2 distinct message_ids -> 2 nodes.
    assert len(records) == 3
    assert len(unique_ids) == 2
    assert len(assistant_nodes) == len(unique_ids)


def test_assistant_tokens_from_fb_record_not_summed(graph):
    m1 = graph.nodes["M1"]
    # fb=true record a1a carries 100/50/10/5; the fb=false a1b carries 999s. Take fb, never sum.
    assert m1.attrs["input_tokens"] == 100
    assert m1.attrs["output_tokens"] == 50
    assert m1.attrs["cache_read_tokens"] == 10
    assert m1.attrs["cache_write_tokens"] == 5
    assert m1.attrs["input_tokens"] != 100 + 999
    # thinking dropped from content, but the flag survives (true on any record in the group).
    assert m1.attrs["has_thinking"] is True


# --- reasoning-token estimate -----------------------------------------------

def test_reasoning_ingredients_summed_over_the_message_id_group(graph):
    m1 = graph.nodes["M1"]
    # a1a estimated_visible_tokens=12/tool_call_count=1, a1b 6/1 -> summed, never fb-only.
    assert m1.attrs["estimated_visible_tokens"] == 18
    assert m1.attrs["tool_call_count"] == 2


def test_reasoning_estimated_and_floored_on_the_tiny_fixture(graph):
    # M1: output_tokens=50 is smaller than the tool-call framing alone (66) at this ratio --
    # a real negative, floored at 0, not an error.
    m1 = graph.nodes["M1"]
    assert m1.attrs["reasoning_source"] == "estimated"
    assert m1.attrs["reasoning_ratio"] == 1.52  # claude-opus-4-8, default ratio_by_model
    assert m1.attrs["estimated_reasoning_tokens"] == 0

    m3 = graph.nodes["M3"]
    assert m3.attrs["reasoning_source"] == "estimated"
    assert m3.attrs["estimated_reasoning_tokens"] == 0
    assert m3.attrs["has_thinking"] is False


def _reasoning_session(*, vendor="claude_code", reasoning_tokens=None, model="claude-opus-5",
                       output_tokens=1000, estimated_visible_tokens=10, tool_call_count=0,
                       content_kind="text"):
    """A minimal one-message, one-turn session, for exercising estimate_reasoning end to end
    through build_graph rather than calling the module directly."""
    events = [
        {"event_type": "session_start", "record_id": "ss1", "parent_id": None,
         "event_ts": None, "payload": {"ai_title": "t", "raw": {}}},
        {"event_type": "user_message", "record_id": "u1", "parent_id": None,
         "event_ts": "2026-08-10T00:00:00Z",
         "payload": {"text": "go", "is_injected": False, "raw": {"promptId": "P1"}}},
        {"event_type": "assistant_message", "record_id": "a1", "parent_id": "u1",
         "event_ts": "2026-08-10T00:00:01Z",
         "payload": {
             "message_id": "m1", "first_block_of_message": True, "model": model,
             "has_thinking": False, "input_tokens": 1, "output_tokens": output_tokens,
             "cache_read_tokens": 0, "cache_write_tokens": 0,
             "estimated_visible_tokens": estimated_visible_tokens,
             "tool_call_count": tool_call_count, "content_kind": content_kind,
             "reasoning_tokens": reasoning_tokens,
         }},
    ]
    return {"vendor": vendor, "session_id": "s-reasoning", "source": "batch",
            "parent_session_id": None, "events": events}


def test_reported_reasoning_beats_estimation_end_to_end():
    session = _reasoning_session(reasoning_tokens=777, output_tokens=100000,
                                 estimated_visible_tokens=1)
    graph = build_graph(session)
    a1 = graph.nodes["m1"]
    assert a1.attrs["reasoning_source"] == "reported"
    assert a1.attrs["estimated_reasoning_tokens"] == 777
    assert a1.attrs["reasoning_ratio"] is None


def test_vendor_isolation_codex_does_not_get_claude_constants():
    session = _reasoning_session(vendor="codex", model="claude-opus-5", output_tokens=1000,
                                 estimated_visible_tokens=100, tool_call_count=3,
                                 content_kind="mixed")
    graph = build_graph(session)
    a1 = graph.nodes["m1"]
    assert a1.attrs["reasoning_source"] == "estimated"
    assert a1.attrs["reasoning_ratio"] == 1.0  # codex default, not claude_code's 1.52
    assert a1.attrs["estimated_reasoning_tokens"] == 900  # 1000 - 1.0*100 - 0 framing


def test_vendor_isolation_unknown_vendor_does_not_get_claude_constants():
    session = _reasoning_session(vendor="some_future_vendor", model="claude-opus-5",
                                 output_tokens=1000, estimated_visible_tokens=100,
                                 tool_call_count=3, content_kind="mixed")
    graph = build_graph(session)
    a1 = graph.nodes["m1"]
    assert a1.attrs["reasoning_ratio"] == 1.0
    assert a1.attrs["estimated_reasoning_tokens"] == 900


def test_custom_config_is_honoured_when_passed_explicitly():
    session = _reasoning_session(model="whatever", output_tokens=1000,
                                 estimated_visible_tokens=100, tool_call_count=0)
    custom = ReasoningConfig(vendors={
        "claude_code": VendorReasoningConfig(ratio_default=2.0),
    })
    graph = build_graph(session, custom)
    a1 = graph.nodes["m1"]
    assert a1.attrs["reasoning_ratio"] == 2.0
    assert a1.attrs["estimated_reasoning_tokens"] == 800  # 1000 - 2.0*100


# --- tool merge ------------------------------------------------------------

def test_tool_call_and_result_merge_to_one_node(graph):
    tools = _nodes_of(graph, NodeType.TOOL)
    assert len(tools) == 1
    tool = tools[0]
    assert tool.id == "tc1"
    assert tool.attrs["tool_name"] == "Read"
    assert tool.attrs["is_error"] is False
    assert tool.attrs["duration_ms"] == 42
    assert tool.attrs["cmd"] == []
    assert graph.meta["query_config_revision"] == "2026-08-27"


def test_tool_is_child_of_calling_assistant(graph):
    # tc1 and sp1 share message_id M1, so both hang under the M1 assistant node.
    assert "tc1" in graph.nodes["M1"].children
    assert "sp1" in graph.nodes["M1"].children


# --- spawn -----------------------------------------------------------------

def test_spawn_carries_child_session_id_in_refs(graph):
    spawns = _nodes_of(graph, NodeType.SPAWN)
    assert len(spawns) == 1
    spawn = spawns[0]
    assert spawn.type == NodeType.SPAWN
    assert spawn.refs["child_session_id"] == "agent-child-001"
    # late-bound: the child ref lives in refs, never in attrs.
    assert "child_session_id" not in spawn.attrs
    assert spawn.attrs["agent_type"] == "Explore"
    assert spawn.attrs["description"] == "explore the codebase"
    assert spawn.attrs["prompt_char_count"] == 120
    assert spawn.attrs["requested_model"] == "claude-sonnet-4"
    assert spawn.attrs["resolved_model"] == "claude-sonnet-4"
    assert spawn.attrs["run_in_background"] is False
    assert spawn.attrs["status"] == "completed"
    assert spawn.attrs["duration_ms"] == 5000


# --- turn segmentation -----------------------------------------------------

def test_turn_count_and_statuses(graph):
    turns = [graph.nodes[r] for r in graph.roots]
    assert [t.attrs["prompt_id"] for t in turns] == ["P1", "P2", "P3"]
    statuses = [t.attrs["status"] for t in turns]
    # P1 closed by turn_duration -> completed; P2 has no assistant -> empty;
    # P3 has an assistant but no close marker (last turn) -> interrupted.
    assert statuses == ["completed", "empty", "interrupted"]


def test_turn_duration_prefers_marker_then_ts_delta(graph):
    turns = {graph.nodes[r].attrs["prompt_id"]: graph.nodes[r] for r in graph.roots}
    assert turns["P1"].attrs["duration_ms"] == 12345  # from the turn_duration record
    # P3: no marker -> last_ts - start_ts = 09.500 - 07.000 = 2500 ms.
    assert turns["P3"].attrs["duration_ms"] == 2500


def test_user_prompt_carries_attachment_resources(graph):
    turn_p1 = graph.nodes["turn:1"]
    user_prompt_id = next(
        c for c in turn_p1.children if graph.nodes[c].type == NodeType.USER_PROMPT
    )
    up = graph.nodes[user_prompt_id]
    assert up.attrs["resources"] == ["att1"]
    assert up.attrs["text"].startswith("Add a feature")


def test_empty_turn_has_no_assistant_child(graph):
    turn_p2 = graph.nodes["turn:2"]
    child_types = {graph.nodes[c].type for c in turn_p2.children}
    assert NodeType.ASSISTANT not in child_types


def test_turn_id_format_and_run_ordinal(graph):
    """The local id is vendor-neutral and the 1-based turn number is explicit."""
    assert graph.roots == ["turn:1", "turn:2", "turn:3"]
    for turn_num, turn_id in enumerate(graph.roots, 1):
        turn = graph.nodes[turn_id]
        assert turn.attrs["turn_num"] == turn_num
        assert turn.attrs["prompt_id"] == f"P{turn_num}"  # provenance survives unchanged
        assert turn.id == f"turn:{turn_num}"


# --- turn boundary rule (plan Step 1) ---------------------------------------

def test_mid_run_turn_duration_does_not_split_a_turn():
    """The bash-mode fixture: a `!command` emits its own `turn_duration` without rotating
    `promptId` -- under the OLD rule this closed the turn early and the SAME promptId then
    opened a second turn whose id already existed (a silent `Graph.add` collision). Under the
    new rule the marker is consumed inside the open run and the SAME promptId keeps
    accumulating into ONE turn."""
    g = build_graph(_session([
        _user("u1", "p1"),
        _assistant("a1", "M1"),
        {"event_type": "unknown", "record_id": "td1", "event_ts": "2026-08-23T10:00:02Z",
         "payload": {"raw": {"type": "system", "subtype": "turn_duration", "durationMs": 3622}}},
        _user("u2", "p1"),  # SAME promptId as u1 -- a bash-mode second command, not a new turn
        _assistant("a2", "M2"),
    ]))
    assert len(_turns(g)) == 1
    turn = _turns(g)[0]
    assert turn.id == "turn:1"
    assert turn.attrs["turn_num"] == 1
    assert {"M1", "M2"} <= set(turn.children)  # both assistants landed in the ONE turn


def test_different_prompt_id_does_split():
    g = build_graph(_session([
        _user("u1", "p1"),
        _assistant("a1", "M1"),
        _user("u2", "p2"),
        _assistant("a2", "M2"),
    ]))
    turns = _turns(g)
    assert [t.attrs["prompt_id"] for t in turns] == ["p1", "p2"]
    assert [t.id for t in turns] == ["turn:1", "turn:2"]
    assert [t.attrs["turn_num"] for t in turns] == [1, 2]


def test_two_turn_duration_markers_yield_one_turn_last_duration_kept():
    """A run with two markers (3 of 594 turns in the reference corpus) still yields one turn.
    Both are "accounted": each overwrites `duration_ms` as it's seen, so the LAST one is what
    survives -- see `_segment_turns`'s docstring for why last, not sum."""
    g = build_graph(_session([
        _user("u1", "p1"),
        _assistant("a1", "M1"),
        {"event_type": "unknown", "record_id": "td1", "event_ts": "2026-08-23T10:00:02Z",
         "payload": {"raw": {"type": "system", "subtype": "turn_duration", "durationMs": 100}}},
        {"event_type": "unknown", "record_id": "td2", "event_ts": "2026-08-23T10:00:03Z",
         "payload": {"raw": {"type": "system", "subtype": "turn_duration", "durationMs": 200}}},
    ]))
    turns = _turns(g)
    assert len(turns) == 1
    assert turns[0].attrs["duration_ms"] == 200  # the LAST marker, not the first and not a sum
    assert turns[0].attrs["status"] == "completed"


def test_run_with_zero_markers_still_builds_status_interrupted():
    """182 of 593 runs in the reference corpus carry no `turn_duration` at all -- the build
    must not require one."""
    g = build_graph(_session([_user("u1", "p1"), _assistant("a1", "M1")]))
    turns = _turns(g)
    assert len(turns) == 1
    assert turns[0].attrs["status"] == "interrupted"
    assert turns[0].attrs["duration_ms"] is not None  # ts-delta fallback, never null


# --- session-scoped result lookup (plan Step 2) -----------------------------

def test_result_in_a_later_turn_merges_onto_its_call_node_stays_in_calls_turn():
    """A `tool_result` written after the promptId already rotated still resolves against its
    call (6 of 7,927 in the reference corpus) -- the result attaches to the call's node; the
    node itself stays in the CALL's turn, not the result's."""
    g = build_graph(_session([
        _user("u1", "p1"),
        _assistant("a1", "M1"),
        {"event_type": "tool_call", "record_id": "tc1", "event_ts": "2026-08-23T10:00:02Z",
         "payload": {"tool_name": "Bash", "message_id": "M1", "input": {}}},
        _user("u2", "p2"),
        {"event_type": "tool_result", "record_id": "tr1", "event_ts": "2026-08-23T10:00:03Z",
         "payload": {"tool_use_id": "tc1", "is_error": True, "tool_denial_kind": None,
                     "raw": {"promptId": "p2"}}},
    ]))
    turns = _turns(g)
    assert len(turns) == 2
    tool = g.nodes["tc1"]
    # merged from p2's tool_result -- proves the lookup crossed the turn boundary (Step 2's
    # fix); reverting to a per-turn results map leaves this False (default, no result found).
    assert tool.attrs["is_error"] is True
    # the node itself never moved: it is still a child of M1, which is still in turn p1.
    assert tool.parent_id == "M1"
    assert tool.id in g.nodes["M1"].children
    assert g.nodes["M1"].parent_id == turns[0].id
    assert "tc1" not in turns[1].children


def test_result_whose_call_is_absent_does_not_crash():
    """A `tool_result` with no matching `tool_call`/`sub_agent_spawn` anywhere in the session
    (0 observed in the reference corpus) must not raise -- it simply has no node to merge onto
    and the build completes normally."""
    g = build_graph(_session([
        _user("u1", "p1"),
        {"event_type": "tool_result", "record_id": "tr1", "event_ts": "2026-08-23T10:00:01Z",
         "payload": {"tool_use_id": "no-such-call", "is_error": False,
                     "tool_denial_kind": None, "raw": {"promptId": "p1"}}},
    ]))
    assert len(_turns(g)) == 1
    assert "no-such-call" not in g.nodes  # no orphan node was fabricated for it


# --- replay: first-wins, no double parent (plan Step 3) ---------------------

def test_replayed_assistant_across_turns_does_not_replace_the_existing_node():
    """A raw record for `message_id` "dup1" appearing in TWO turns (a replay spanning a turn
    boundary) must not let the second occurrence overwrite the first: `Graph.add` is
    first-wins, so the FIRST turn's field values survive."""
    g = build_graph(_session([
        _user("u1", "p1"),
        _assistant("a1", "dup1"),  # first: input_tokens=1 (the _assistant() helper's default)
        _user("u2", "p2"),
        {"event_type": "assistant_message", "record_id": "a2",
         "event_ts": "2026-08-23T10:00:05Z",
         "payload": {"message_id": "dup1", "first_block_of_message": True, "model": "m",
                     "input_tokens": 999, "output_tokens": 999,
                     "cache_read_tokens": 999, "cache_write_tokens": 999,
                     "estimated_visible_tokens": 999, "tool_call_count": 0}},
    ]))
    node = g.nodes["dup1"]
    assert node.attrs["input_tokens"] == 1  # the replay's 999 did NOT overwrite it


def test_replayed_assistant_across_turns_does_not_add_a_second_parent():
    """The SAME replay must not leave "dup1" listed as a child of both turns -- that would let
    a walk of `children` (the token rollup) count it twice even though `Graph.add` kept only
    one node object. The child-append guard is what clears this, not `Graph.add` alone."""
    g = build_graph(_session([
        _user("u1", "p1"),
        _assistant("a1", "dup1"),
        _user("u2", "p2"),
        {"event_type": "assistant_message", "record_id": "a2",
         "event_ts": "2026-08-23T10:00:05Z",
         "payload": {"message_id": "dup1", "first_block_of_message": True, "model": "m",
                     "input_tokens": 999, "output_tokens": 999,
                     "cache_read_tokens": 999, "cache_write_tokens": 999,
                     "estimated_visible_tokens": 999, "tool_call_count": 0}},
    ]))
    turns = _turns(g)
    assert "dup1" in turns[0].children
    assert "dup1" not in turns[1].children  # refused: already claimed by turn p1
    # token rollup: summed once (from the surviving node), not twice.
    assert turns[0].attrs["tokens"]["input_tokens"] == 1
    assert turns[1].attrs["tokens"]["input_tokens"] == 0


# --- data integrity --------------------------------------------------------

def test_zero_dangling_parent_id_within_session(session):
    record_ids = {e["record_id"] for e in session["events"] if e["record_id"] is not None}
    dangling = [
        e["record_id"]
        for e in session["events"]
        if e["parent_id"] is not None and e["parent_id"] not in record_ids
    ]
    assert dangling == []


# --- files attached to tool nodes (Step 1.1) -------------------------------

def _file_session(tool_files=None, file_change_events=None) -> dict:
    """A minimal one-turn session whose single Edit tool (tc1) may carry tool_call.files[]
    and/or a derived file_change event."""
    events = [
        {"event_type": "session_start", "record_id": None, "parent_id": None, "event_ts": None,
         "payload": {"ai_title": "edit", "raw": {}}},
        {"event_type": "user_message", "record_id": "u1", "parent_id": None,
         "event_ts": "2026-08-10T00:00:00Z",
         "payload": {"text": "edit a file", "is_injected": False, "raw": {"promptId": "P1"}}},
        {"event_type": "assistant_message", "record_id": "a1", "parent_id": "u1",
         "event_ts": "2026-08-10T00:00:01Z",
         "payload": {"message_id": "m1", "first_block_of_message": True, "model": "opus",
                     "input_tokens": 1, "output_tokens": 1, "has_thinking": False}},
        {"event_type": "tool_call", "record_id": "tc1", "parent_id": "a1",
         "event_ts": "2026-08-10T00:00:02Z",
         "payload": {"tool_name": "Edit", "message_id": "m1", "input": {},
                     "files": tool_files or []}},
        {"event_type": "tool_result", "record_id": "tr1", "parent_id": "tc1",
         "event_ts": "2026-08-10T00:00:03Z",
         "payload": {"tool_use_id": "tc1", "is_error": False, "raw": {"promptId": "P1"}}},
    ]
    events.extend(file_change_events or [])
    return {"vendor": "claude_code", "session_id": "s1", "source": "batch",
            "parent_session_id": None, "events": events}


def _file_change(path, ext, cls, op="edit"):
    return {"event_type": "file_change", "record_id": None, "parent_id": "a1",
            "event_ts": "2026-08-10T00:00:02Z",
            "payload": {"path": path, "extension": ext, "artifact_class": cls, "operation": op,
                        "derived_from_event_id": "tc1", "hunks": []}}


def test_tool_call_files_attached():
    g = build_graph(_file_session(tool_files=[
        {"path": "/x/a.py", "extension": ".py", "artifact_class": "code", "operation": "edit"},
    ]))
    tool = g.nodes["tc1"]
    assert tool.attrs["file_change"] is True
    assert [f["path"] for f in tool.attrs["files"]] == ["/x/a.py"]
    assert tool.attrs["files"][0]["artifact_class"] == "code"


def test_file_change_event_attached():
    g = build_graph(_file_session(file_change_events=[
        _file_change("/x/b.md", ".md", "documentation", op="create"),
    ]))
    tool = g.nodes["tc1"]
    assert tool.attrs["file_change"] is True
    assert [f["path"] for f in tool.attrs["files"]] == ["/x/b.md"]
    assert tool.attrs["files"][0]["artifact_class"] == "documentation"


def test_files_deduped_across_both_sources():
    g = build_graph(_file_session(
        tool_files=[{"path": "/x/a.py", "extension": ".py", "artifact_class": "code",
                     "operation": "edit"}],
        file_change_events=[_file_change("/x/a.py", ".py", "code")],
    ))
    files = g.nodes["tc1"].attrs["files"]
    assert [f["path"] for f in files] == ["/x/a.py"]  # same path -> one entry


def test_tool_without_files_has_no_marker():
    tool = build_graph(_file_session()).nodes["tc1"]
    assert "file_change" not in tool.attrs
    assert "files" not in tool.attrs


@pytest.mark.parametrize(
    ("path", "expected_kind"),
    [
        ("/Users/me/.claude/projects/-repo/memory/MEMORY.md", "index"),
        ("/Users/me/.claude/projects/-repo/memory/notes.md", "entry"),
        (r"C:\Users\me\.claude\projects\-repo\memory\notes.md", "entry"),
    ],
)
def test_memory_file_classification_is_vendor_configured(path, expected_kind):
    graph = build_graph(_file_session(tool_files=[{
        "path": path, "extension": ".md", "artifact_class": "documentation",
        "operation": "edit",
    }]))

    tool = graph.nodes["tc1"]
    assert tool.attrs["memory_access"] is True
    assert tool.attrs["files"][0]["is_memory"] is True
    assert tool.attrs["files"][0]["memory_kind"] == expected_kind


@pytest.mark.parametrize(
    "path",
    [
        "/repo/memory/MEMORY.md",
        "/Users/me/.claude/projects/-repo/not-memory/MEMORY.md",
        "/Users/me/.claude/projects/-repo/memoryish/notes.md",
    ],
)
def test_memory_file_classification_rejects_near_misses(path):
    graph = build_graph(_file_session(tool_files=[{
        "path": path, "extension": ".md", "artifact_class": "documentation",
        "operation": "edit",
    }]))

    tool = graph.nodes["tc1"]
    assert "memory_access" not in tool.attrs
    assert "is_memory" not in tool.attrs["files"][0]
    assert "memory_kind" not in tool.attrs["files"][0]


def test_unknown_vendor_has_no_memory_classification():
    session = _file_session(tool_files=[{
        "path": "/Users/me/.claude/projects/-repo/memory/MEMORY.md",
        "extension": ".md", "artifact_class": "documentation", "operation": "edit",
    }])
    session["vendor"] = "future_harness"

    tool = build_graph(session).nodes["tc1"]
    assert "memory_access" not in tool.attrs
    assert "is_memory" not in tool.attrs["files"][0]


def test_memory_classifier_uses_first_matching_rule():
    config = load_query_config().model_copy(update={
        "memory_classifiers": {
            "claude_code": [
                MemoryClassifierConfig(path_glob="**/memory/**", kind="unknown"),
                MemoryClassifierConfig(path_glob="**/memory/MEMORY.md", kind="index"),
            ],
        },
    })
    graph = build_graph(_file_session(tool_files=[{
        "path": "/home/.claude/projects/-repo/memory/MEMORY.md",
        "extension": ".md", "artifact_class": "documentation", "operation": "create",
    }]), query_config=config)
    assert graph.nodes["tc1"].attrs["files"][0]["memory_kind"] == "unknown"


def test_write_result_refines_create_and_update_operations():
    created = _file_session(tool_files=[{
        "path": "/home/.claude/projects/-repo/memory/MEMORY.md",
        "extension": ".md", "artifact_class": "documentation", "operation": "create",
    }])
    updated = json.loads(json.dumps(created))
    for session, kind in ((created, "create"), (updated, "update")):
        next(e for e in session["events"] if e["event_type"] == "tool_call")["payload"]["tool_name"] = "Write"
        result = next(e for e in session["events"] if e["event_type"] == "tool_result")
        result["payload"]["raw"]["toolUseResult"] = {"type": kind}

    assert build_graph(created).nodes["tc1"].attrs["files"][0]["operation"] == "create"
    assert build_graph(updated).nodes["tc1"].attrs["files"][0]["operation"] == "edit"


def test_inline_query_selects_only_memory_tools():
    graph = build_graph(_file_session(tool_files=[{
        "path": "/home/.claude/projects/-repo/memory/notes.md",
        "extension": ".md", "artifact_class": "documentation", "operation": "edit",
    }]))
    result = query(Family(master=graph), {
        "version": "1",
        "scope": {"kind": "tree"},
        "select": {"type": "Tool", "where": "Tool.memory_access == true"},
        "return": {"kind": "nodes", "verbosity": "ref"},
    })
    assert [(item.identity.session_id, item.identity.node_id) for item in result.items] == [
        ("s1", "tc1"),
    ]


# --- attrs.result merge (Step 1 tool nodes, Step 2 spawn nodes) -------------

def _result_session(*, tool_result=None, spawn_result=None, mcp=False, no_tool_result=False):
    """One turn with a Bash tool_call and a sub_agent_spawn, each optionally paired with a
    tool_result carrying the given raw.toolUseResult (or omitted entirely to test absence)."""
    events = [
        {"event_type": "session_start", "record_id": None, "parent_id": None, "event_ts": None,
         "payload": {"ai_title": "result merge", "raw": {}}},
        {"event_type": "user_message", "record_id": "u1", "parent_id": None,
         "event_ts": "2026-08-20T00:00:00Z",
         "payload": {"text": "do stuff", "is_injected": False, "raw": {"promptId": "P1"}}},
        {"event_type": "assistant_message", "record_id": "a1", "parent_id": "u1",
         "event_ts": "2026-08-20T00:00:01Z",
         "payload": {"message_id": "m1", "first_block_of_message": True, "model": "opus",
                     "input_tokens": 1, "output_tokens": 1, "has_thinking": False}},
        {"event_type": "tool_call", "record_id": "tc1", "parent_id": "a1",
         "event_ts": "2026-08-20T00:00:02Z",
         "payload": {
             "tool_name": ("mcp__claude_ai_Google_Drive__read_file_content" if mcp else "Bash"),
             "message_id": "m1", "input": {},
             **({"mcp_server": "claude_ai_Google_Drive", "mcp_tool": "read_file_content"}
                if mcp else {}),
         }},
        {"event_type": "sub_agent_spawn", "record_id": "sp1", "parent_id": "a1",
         "event_ts": "2026-08-20T00:00:02Z",
         "payload": {"agent_type": "general-purpose", "status": "completed",
                     "resolved_model": "claude-fable-5", "message_id": "m1",
                     "child_session_id": "agent-child-002"}},
    ]
    if not no_tool_result:
        events.append({
            "event_type": "tool_result", "record_id": "tr1", "parent_id": "tc1",
            "event_ts": "2026-08-20T00:00:03Z",
            "payload": {"tool_use_id": "tc1", "is_error": False, "tool_denial_kind": None,
                        "raw": {"promptId": "P1", "toolUseResult": tool_result}},
        })
    events.append({
        "event_type": "tool_result", "record_id": "tr2", "parent_id": "sp1",
        "event_ts": "2026-08-20T00:00:04Z",
        "payload": {"tool_use_id": "sp1", "is_error": False, "tool_denial_kind": None,
                    "raw": {"promptId": "P1", "toolUseResult": spawn_result}},
    })
    return {"vendor": "claude_code", "session_id": "s1", "source": "batch",
            "parent_session_id": None, "events": events}


def test_tool_node_result_merged_git_facet():
    g = build_graph(_result_session(tool_result={
        "stdout": "…", "stderr": "", "interrupted": False, "isImage": False,
        "noOutputExpected": False,
        "gitOperation": {"commit": {"sha": "e01a4c6", "kind": "committed"}},
    }, spawn_result={"status": "completed", "agentId": "a1", "prompt": "x"}))
    tool = g.nodes["tc1"]
    assert tool.attrs["result"]["outcome"] == "ok"
    assert tool.attrs["result"]["git"] == {"op": "commit", "sha": "e01a4c6", "kind": "committed"}


def test_tool_node_result_absent_when_no_tool_result_event():
    g = build_graph(_result_session(no_tool_result=True,
                                     spawn_result={"status": "completed"}))
    assert "result" not in g.nodes["tc1"].attrs


def test_tool_node_mcp_result_uses_call_side_server_and_tool():
    g = build_graph(_result_session(mcp=True,
                                     tool_result='{"fileContent": "hi"}',
                                     spawn_result={"status": "completed"}))
    tool = g.nodes["tc1"]
    assert tool.attrs["result"]["mcp"] == {
        "server": "claude_ai_Google_Drive", "tool": "read_file_content",
        "structured_content": {"fileContent": "hi"},
    }


def test_spawn_node_result_merged_delegation_facet():
    g = build_graph(_result_session(
        tool_result={"stdout": "", "stderr": "", "interrupted": False, "isImage": False,
                     "noOutputExpected": True},
        spawn_result={
            "status": "completed", "prompt": "You are helping…", "agentId": "a15f40f2871d0aa1b",
            "agentType": "general-purpose", "resolvedModel": "claude-fable-5",
            "totalDurationMs": 92794, "totalToolUseCount": 2,
            "toolStats": {"readCount": 0, "searchCount": 0, "bashCount": 1, "editFileCount": 1,
                          "linesAdded": 103, "linesRemoved": 0, "otherToolCount": 0},
        },
    ))
    spawn = g.nodes["sp1"]
    assert spawn.attrs["result"]["outcome"] == "ok"
    delegation = spawn.attrs["result"]["delegation"]
    assert delegation["prompt"] == "You are helping…"
    assert delegation["agent_id"] == "a15f40f2871d0aa1b"
    assert delegation["duration_ms"] == 92794
    assert delegation["tool_stats"]["lines_added"] == 103
    # call-side attrs (agent_type/status/resolved_model) are unaffected and not duplicated.
    assert spawn.attrs["status"] == "completed"
    assert "status" not in delegation
    assert "agent_type" not in delegation


def test_spawn_node_result_absent_when_no_matching_tool_result():
    session = _result_session(tool_result={"stdout": "", "stderr": "", "interrupted": False,
                                            "isImage": False, "noOutputExpected": True},
                               spawn_result=None)
    session["events"] = [e for e in session["events"] if e.get("record_id") != "tr2"]
    g = build_graph(session)
    assert "result" not in g.nodes["sp1"].attrs


# --- permission mode / plan mode (plan Step 3) -----------------------------

def _session(events: list[dict]) -> dict:
    """A minimal one-session envelope around hand-built events."""
    return {"vendor": "claude_code", "session_id": "sess-mode-0001", "events": events}


def _user(rid, prompt_id, *, mode=..., plan=..., injected=False):
    payload = {"text": "go", "is_injected": injected, "raw": {"promptId": prompt_id}}
    if mode is not ...:
        payload["permission_mode"] = mode
    if plan is not ...:
        payload["plan_mode"] = plan
    return {"event_type": "user_message", "record_id": rid,
            "event_ts": "2026-08-23T10:00:00Z", "payload": payload}


def _assistant(rid, mid, **attribution):
    payload = {"message_id": mid, "first_block_of_message": True, "model": "m",
               "input_tokens": 1, "output_tokens": 1,
               "cache_read_tokens": 0, "cache_write_tokens": 0,
               "estimated_visible_tokens": 1, "tool_call_count": 0}
    payload.update(attribution)
    return {"event_type": "assistant_message", "record_id": rid,
            "event_ts": "2026-08-23T10:00:01Z", "payload": payload}


def _turns(graph):
    return [graph.nodes[t] for t in graph.roots]


def test_turn_mode_null_not_false_when_vendor_recorded_none(graph):
    """245 of 584 corpus turns carry no mode. Unknown and not-plan-mode are different
    claims, so plan_mode must be None rather than False."""
    for turn in _turns(graph):
        assert turn.attrs["permission_mode"] is None
        assert turn.attrs["plan_mode"] is None


def test_turn_mode_read_from_initiating_prompt():
    g = build_graph(_session([_user("u1", "p1", mode="plan", plan=True),
                              _assistant("a1", "M1")]))
    turn = _turns(g)[0]
    assert turn.attrs["permission_mode"] == "plan"
    assert turn.attrs["plan_mode"] is True


def test_turn_mode_ignores_injected_prompt():
    """The initiating prompt is the first NON-injected user_message; an injected record
    carrying a different mode must not win."""
    g = build_graph(_session([_user("u1", "p1", mode="auto", plan=False),
                              _user("u2", "p1", mode="plan", plan=True, injected=True),
                              _assistant("a1", "M1")]))
    assert _turns(g)[0].attrs["permission_mode"] == "auto"


def test_turn_mode_none_when_turn_has_no_user_message(graph):
    """A turn opened by a tool_result carries no prompt record to read a mode from."""
    g = build_graph(_session([_user("u1", "p1"), _assistant("a1", "M1")]))
    assert _turns(g)[0].attrs["plan_mode"] is None


# --- attribution (plan Step 4) ---------------------------------------------

def test_assistant_carries_attribution_verbatim():
    g = build_graph(_session([
        _user("u1", "p1"),
        _assistant("a1", "M1", attribution_skill="my-arch-doc",
                   attribution_plugin="superpowers",
                   attribution_agent="general-purpose", effort="high"),
    ]))
    node = _nodes_of(g, NodeType.ASSISTANT)[0]
    assert node.attrs["attribution_skill"] == "my-arch-doc"
    assert node.attrs["attribution_plugin"] == "superpowers"
    assert node.attrs["attribution_agent"] == "general-purpose"
    assert node.attrs["effort"] == "high"


def test_turn_attribution_is_always_lists_empty_when_nothing_attributed(graph):
    """Never a scalar, never null -- one published type, exercised on every turn."""
    for turn in _turns(graph):
        attribution = turn.attrs["attribution"]
        assert set(attribution) == {"skill", "plugin", "agent", "effort"}
        for value in attribution.values():
            assert value == []


def test_turn_attribution_single_value_is_still_a_list():
    g = build_graph(_session([_user("u1", "p1"),
                              _assistant("a1", "M1", attribution_skill="my-arch-doc")]))
    assert _turns(g)[0].attrs["attribution"]["skill"] == ["my-arch-doc"]


def test_turn_attribution_two_skills_yields_both_in_first_seen_order():
    g = build_graph(_session([
        _user("u1", "p1"),
        _assistant("a1", "M1", attribution_skill="my-arch-doc"),
        _assistant("a2", "M2", attribution_skill="talium-plan"),
    ]))
    assert _turns(g)[0].attrs["attribution"]["skill"] == ["my-arch-doc", "talium-plan"]


def test_turn_attribution_collapses_duplicates():
    g = build_graph(_session([
        _user("u1", "p1"),
        _assistant("a1", "M1", attribution_skill="my-arch-doc", effort="high"),
        _assistant("a2", "M2", attribution_skill="my-arch-doc", effort="high"),
    ]))
    attribution = _turns(g)[0].attrs["attribution"]
    assert attribution["skill"] == ["my-arch-doc"]
    assert attribution["effort"] == ["high"]


def test_turn_attribution_counts_assistants_only():
    """The rollup reads assistant nodes. A same-named attr on any other node type is not
    attribution and must not be swept in."""
    g = build_graph(_session([_user("u1", "p1"), _assistant("a1", "M1")]))
    turn = _turns(g)[0]
    prompt = [n for n in g.nodes.values() if n.type == NodeType.USER_PROMPT][0]
    prompt.attrs["attribution_skill"] = "not-attribution"
    from analysis.causal_graph.builder import roll_up_turn
    roll_up_turn(g, turn.id)
    assert turn.attrs["attribution"]["skill"] == []


# --- compaction (a harness event, not part of a turn) -----------------------

def _compaction(rid, *, trigger, logical_parent=None):
    return {"event_type": "compaction", "record_id": rid,
            "event_ts": "2026-08-23T10:00:03Z",
            "payload": {"trigger": trigger, "tokens_before": 1000, "tokens_after": 100,
                        "tokens_reclaimed": 900, "duration_ms": 1500,
                        "raw": {"uuid": rid, "logicalParentUuid": logical_parent}}}


def _compact_summary(rid, prompt_id, *, parent):
    """The isCompactSummary user_message: child of the compaction, opens/continues a turn."""
    return {"event_type": "user_message", "record_id": rid, "parent_id": parent,
            "event_ts": "2026-08-23T10:00:04Z",
            "payload": {"text": "continued from a previous conversation", "is_injected": True,
                        "raw": {"promptId": prompt_id, "isCompactSummary": True}}}


def _compactions(graph):
    return [n for n in graph.nodes.values() if n.type == NodeType.COMPACTION]


def test_auto_compaction_mid_turn_marks_and_links_the_one_turn():
    """An auto compaction with no promptId is absorbed into the open turn, then surfaced as a
    session-level node. Mid-turn, the same turn both triggered it and resumed after it."""
    g = build_graph(_session([
        _user("u1", "p1"),
        _assistant("a1", "M1"),
        _compaction("comp1", trigger="auto", logical_parent="a1"),
        _compact_summary("sum1", "p1", parent="comp1"),
        _assistant("a2", "M2"),
    ]))
    comps = _compactions(g)
    assert len(comps) == 1
    comp = comps[0]
    assert comp.id == "comp1"
    assert comp.attrs["trigger"] == "auto"
    assert comp.attrs["tokens_reclaimed"] == 900
    assert comp.attrs["usage_known"] is False
    # A session-level node, but NOT part of the turn sequence.
    assert comp.parent_id is None
    assert "comp1" not in g.roots
    assert g.roots == ["turn:1"]
    turn = g.nodes["turn:1"]
    assert turn.attrs["turn_num"] == 1                     # turn numbering untouched
    assert turn.attrs.get("is_after_compact") is True
    assert turn.attrs.get("triggered_compaction") is True
    assert turn.refs["resumed_from_compaction_ids"] == ["comp1"]
    assert turn.refs["triggered_compaction_ids"] == ["comp1"]
    assert comp.refs["resumed_turn_id"] == "turn:1"
    assert comp.refs["triggering_turn_id"] == "turn:1"
    # Metrics-neutral: the compaction node is not summed into the turn's tokens.
    assert turn.attrs["tokens"]["output_tokens"] == 2      # a1 + a2 only


def test_manual_compaction_marks_resume_but_no_triggering_turn():
    """A user-initiated /compact between turns marks the resumed turn but is attributed to no
    triggering turn -- it was not caused by a turn's growth."""
    g = build_graph(_session([
        _user("u1", "p1"),
        _assistant("a1", "M1"),
        _compaction("comp1", trigger="manual", logical_parent="a1"),
        _compact_summary("sum1", "p2", parent="comp1"),
        _assistant("a2", "M2"),
    ]))
    assert g.roots == ["turn:1", "turn:2"]
    comp = _compactions(g)[0]
    assert comp.attrs["trigger"] == "manual"
    resumed = g.nodes["turn:2"]
    assert resumed.attrs.get("is_after_compact") is True
    assert resumed.refs["resumed_from_compaction_ids"] == ["comp1"]
    assert comp.refs["resumed_turn_id"] == "turn:2"
    # manual: no turn is marked as the trigger, even though logicalParentUuid resolves.
    assert comp.refs["triggering_turn_id"] is None
    assert "triggered_compaction" not in g.nodes["turn:1"].attrs
    assert "triggered_compaction" not in resumed.attrs


def test_session_without_compaction_has_no_compaction_node_or_marks(graph):
    """The ordinary path is untouched: no compaction node, no new turn attrs."""
    assert _compactions(graph) == []
    for turn in _turns(graph):
        assert "is_after_compact" not in turn.attrs
        assert "triggered_compaction" not in turn.attrs


def test_compaction_before_first_prompt_is_still_a_session_node():
    """The complete event stream is scanned, so startup compactions are not discarded."""
    g = build_graph(_session([
        _compaction("comp-startup", trigger="manual"),
        _compact_summary("sum1", "p1", parent="comp-startup"),
        _assistant("a1", "M1"),
    ]))
    comp = _compactions(g)[0]
    assert comp.id == "comp-startup"
    assert comp.refs["resumed_turn_id"] == "turn:1"
    assert g.nodes["turn:1"].refs["resumed_from_compaction_ids"] == ["comp-startup"]


def test_two_compactions_in_one_turn_retain_both_reverse_links():
    """Turn refs are plural because a long-running turn can compact more than once."""
    g = build_graph(_session([
        _user("u1", "p1"),
        _compaction("comp1", trigger="auto", logical_parent="u1"),
        _compact_summary("sum1", "p1", parent="comp1"),
        _compaction("comp2", trigger="auto", logical_parent="sum1"),
        _compact_summary("sum2", "p1", parent="comp2"),
        _assistant("a1", "M1"),
    ]))
    assert [node.id for node in _compactions(g)] == ["comp1", "comp2"]
    turn = g.nodes["turn:1"]
    assert turn.refs["resumed_from_compaction_ids"] == ["comp1", "comp2"]
    assert turn.refs["triggered_compaction_ids"] == ["comp1", "comp2"]


def test_only_compact_summary_child_resolves_resumed_turn():
    """A structural child without the vendor summary marker cannot claim the resume link."""
    ordinary_child = _compact_summary("ordinary", "p1", parent="comp1")
    ordinary_child["payload"]["raw"]["isCompactSummary"] = False
    g = build_graph(_session([
        _compaction("comp1", trigger="manual"),
        ordinary_child,
        _assistant("a1", "M1"),
    ]))
    comp = _compactions(g)[0]
    assert comp.refs["resumed_turn_id"] is None
    assert "is_after_compact" not in g.nodes["turn:1"].attrs
