"""Tests for the `causal_graph.*` anomaly counters (`counters.py`, plan Step 3).

Each collision site is exercised in isolation -- a fixture built to trigger exactly ONE
condition -- so a test failure points at a single counter, not a tangle of several firing
together. `causal_graph.reasoning.*` (informational, folded in from `reasoning.py`) is
deliberately excluded from the "isolated" assertions below: every assistant node estimates a
reasoning count as a matter of course, so those series move on every build and are not part of
what "one anomaly, one series" is checking.
"""
from __future__ import annotations

import json
from pathlib import Path

from common import metrics
from analysis.causal_graph.builder import _build_graph_model as build_graph, _claim_children
from analysis.causal_graph.models import Graph, Node, NodeType

FIXTURE = Path(__file__).parents[2] / "fixtures" / "causal_graph" / "tiny_session.json"

# The series `counters.py` owns -- everything EXCEPT the informational `reasoning.*` pair
# folded in from `reasoning.py` (plan Step 3's "one counter idiom, not two"; see that module).
_COUNTER_SERIES = (
    "causal_graph.node_collision",
    "causal_graph.child_collision",
    "causal_graph.results_collision",
    "causal_graph.file_changes_collision",
    "causal_graph.event_outside_turn",
    "causal_graph.prompt_id_noncontiguous",
    "causal_graph.empty_turn",
)


def setup_function():
    """Same idiom as `tests/common/msging/test_log_metrics.py` / `test_reasoning.py`: one
    shared module-level store, reset before each test rather than accumulated across the
    file."""
    metrics.reset_metrics()


def _fired() -> dict[str, int]:
    """`{series: total}` for every `_COUNTER_SERIES` entry that actually incremented (summed
    across label combinations) -- empty when nothing fired."""
    snapshot = metrics.metrics()
    out = {}
    for name in _COUNTER_SERIES:
        total = sum(snapshot.get(name, {}).values())
        if total:
            out[name] = total
    return out


def _session(events: list[dict]) -> dict:
    return {"vendor": "claude_code", "session_id": "s-counters", "events": events}


def _turn_events(prompt_id: str, mid: str, ts: str) -> list[dict]:
    """One minimal turn: a prompt plus one assistant, unique per `mid` -- deliberately not
    triggering `node_collision` or `empty_turn` so a test composing several of these isolates
    whatever OTHER condition it is checking."""
    return [
        {"event_type": "user_message", "record_id": f"u-{mid}", "event_ts": ts,
         "payload": {"text": "go", "is_injected": False, "raw": {"promptId": prompt_id}}},
        {"event_type": "assistant_message", "record_id": f"a-{mid}", "event_ts": ts,
         "payload": {"message_id": mid, "first_block_of_message": True, "model": "m",
                     "input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0,
                     "cache_write_tokens": 0, "estimated_visible_tokens": 1,
                     "tool_call_count": 0}},
    ]


# --- Graph.add: node_collision -----------------------------------------------------------

def test_node_collision_counted_once_and_first_wins():
    g = Graph(session_id="s1", vendor="claude_code", source="jsonl", built="complete")
    g.add(Node(id="x", type=NodeType.TOOL, attrs={"tool_name": "Bash"}))
    g.add(Node(id="x", type=NodeType.TOOL, attrs={"tool_name": "Edit"}))  # collides

    assert _fired() == {"causal_graph.node_collision": 1}
    assert g.nodes["x"].attrs["tool_name"] == "Bash"  # the FIRST node survives, not replaced


def test_node_collision_silent_when_ids_are_distinct():
    g = Graph(session_id="s1", vendor="claude_code", source="jsonl", built="complete")
    g.add(Node(id="x", type=NodeType.TOOL))
    g.add(Node(id="y", type=NodeType.TOOL))
    assert _fired() == {}


# --- child append: child_collision ---------------------------------------------------------

def test_child_collision_counted_once_when_a_different_parent_reclaims():
    claimed: dict[str, str] = {"x": "parent-a"}
    accepted = _claim_children(
        claimed, "parent-b", ["x"], session_id="s1", vendor="claude_code",
    )
    assert accepted == []
    assert _fired() == {"causal_graph.child_collision": 1}


def test_child_collision_counted_once_for_a_duplicate_within_one_list():
    claimed: dict[str, str] = {}
    accepted = _claim_children(
        claimed, "parent-a", ["x", "x"], session_id="s1", vendor="claude_code",
    )
    assert accepted == ["x"]  # only the first occurrence is accepted
    assert _fired() == {"causal_graph.child_collision": 1}


def test_child_collision_silent_when_every_id_is_claimed_once():
    claimed: dict[str, str] = {}
    accepted = _claim_children(
        claimed, "parent-a", ["x", "y"], session_id="s1", vendor="claude_code",
    )
    assert accepted == ["x", "y"]
    assert _fired() == {}


# --- session-scoped maps: results_collision, file_changes_collision -----------------------

def test_results_collision_counted_once_for_a_duplicate_tool_result():
    events = [
        *_turn_events("p1", "m1", "2026-08-23T10:00:00Z"),
        {"event_type": "tool_call", "record_id": "tc1", "event_ts": "2026-08-23T10:00:01Z",
         "payload": {"tool_name": "Bash", "message_id": "m1", "input": {}}},
        {"event_type": "tool_result", "record_id": "tr1", "event_ts": "2026-08-23T10:00:02Z",
         "payload": {"tool_use_id": "tc1", "is_error": False, "tool_denial_kind": None,
                     "raw": {"promptId": "p1"}}},
        {"event_type": "tool_result", "record_id": "tr1b", "event_ts": "2026-08-23T10:00:03Z",
         "payload": {"tool_use_id": "tc1", "is_error": False, "tool_denial_kind": None,
                     "raw": {"promptId": "p1"}}},  # byte-identical replay, same tool_use_id
    ]
    build_graph(_session(events))
    assert _fired() == {"causal_graph.results_collision": 1}


def test_file_changes_collision_counted_once_for_a_duplicate_record():
    file_change = {
        "event_type": "file_change", "record_id": "fc1", "event_ts": "2026-08-23T10:00:02Z",
        "payload": {"path": "/x.py", "extension": ".py", "artifact_class": "code",
                     "operation": "edit", "derived_from_event_id": "tc1", "hunks": []},
    }
    events = [
        *_turn_events("p1", "m1", "2026-08-23T10:00:00Z"),
        {"event_type": "tool_call", "record_id": "tc1", "event_ts": "2026-08-23T10:00:01Z",
         "payload": {"tool_name": "Edit", "message_id": "m1", "input": {}, "files": []}},
        file_change,
        dict(file_change),  # same record_id, same source call -- an exact replay, not a
                             # second distinct file touched by the same call
    ]
    build_graph(_session(events))
    assert _fired() == {"causal_graph.file_changes_collision": 1}


def test_file_changes_not_counted_for_two_distinct_files_from_one_call():
    """The ordinary, legitimate case -- one tool call touching two different files -- must NOT
    be flagged as a collision."""
    events = [
        *_turn_events("p1", "m1", "2026-08-23T10:00:00Z"),
        {"event_type": "tool_call", "record_id": "tc1", "event_ts": "2026-08-23T10:00:01Z",
         "payload": {"tool_name": "Edit", "message_id": "m1", "input": {}, "files": []}},
        {"event_type": "file_change", "record_id": "fc1", "event_ts": "2026-08-23T10:00:02Z",
         "payload": {"path": "/a.py", "extension": ".py", "artifact_class": "code",
                     "operation": "edit", "derived_from_event_id": "tc1", "hunks": []}},
        {"event_type": "file_change", "record_id": "fc2", "event_ts": "2026-08-23T10:00:02Z",
         "payload": {"path": "/b.py", "extension": ".py", "artifact_class": "code",
                     "operation": "edit", "derived_from_event_id": "tc1", "hunks": []}},
    ]
    g = build_graph(_session(events))
    assert _fired() == {}
    assert [f["path"] for f in g.nodes["tc1"].attrs["files"]] == ["/a.py", "/b.py"]


# --- prompt_id_noncontiguous ----------------------------------------------------------------

def test_prompt_id_noncontiguous_counted_once():
    events = [
        *_turn_events("p1", "m1", "2026-08-23T10:00:00Z"),
        *_turn_events("p2", "m2", "2026-08-23T10:00:01Z"),
        *_turn_events("p1", "m3", "2026-08-23T10:00:02Z"),  # p1 reappears after p2 intervened
    ]
    build_graph(_session(events))
    assert _fired() == {"causal_graph.prompt_id_noncontiguous": 1}


def test_prompt_id_contiguous_runs_are_silent():
    events = [*_turn_events("p1", "m1", "2026-08-23T10:00:00Z"),
              *_turn_events("p2", "m2", "2026-08-23T10:00:01Z")]
    build_graph(_session(events))
    assert _fired() == {}


# --- empty_turn: a counter, not an error -----------------------------------------------------

def test_empty_turn_counted_once_per_assistant_less_turn():
    events = [
        {"event_type": "user_message", "record_id": "u1", "event_ts": "2026-08-23T10:00:00Z",
         "payload": {"text": "go", "is_injected": False, "raw": {"promptId": "p1"}}},
    ]
    build_graph(_session(events))
    assert _fired() == {"causal_graph.empty_turn": 1}


# --- a clean build ---------------------------------------------------------------------------

def test_clean_session_leaves_every_counter_series_at_zero():
    """The hand-authored fixture used throughout `test_builder.py` -- no replays, no
    cross-turn collisions, one legitimately empty turn (P2). `empty_turn` is expected to move
    (it is a gauge, not an alarm, per its own docstring); every collision/invariant series
    must stay at zero."""
    session = json.loads(FIXTURE.read_text())
    build_graph(session)
    fired = _fired()
    assert fired.get("causal_graph.empty_turn") == 1  # P2 in the fixture: a real empty turn
    anomalies = {k: v for k, v in fired.items() if k != "causal_graph.empty_turn"}
    assert anomalies == {}
