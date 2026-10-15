"""Real-data replay tests: run the normalizer over committed, sanitized *real* Claude Code
sessions (normalizer.md §12 "recorded CC sessions"), not hand-built fixtures.

The synthetic `sessions.py` fixtures prove each mapping rule in isolation; these prove the
normalizer survives the messy shape of an actual session -- 60-400 records of every CC record
type, real subagent trees, real tool-result pairing -- without crashing or dropping a record
type. Shape stability across releases is the separate concern of `test_shape_contract.py`.
"""
from __future__ import annotations

import pytest

from fixtures.normalizer import recorded_sessions


def _fingerprint(events):
    return [(e.event_type, e.record_id, e.parent_id, e.event_ts, e.payload) for e in events]


@pytest.fixture(params=recorded_sessions.RECORDED_NAMES)
def recorded_name(request):
    return request.param


def test_normalizes_without_crashing(normalizer, recorded_name):
    src = recorded_sessions.load_recorded(recorded_name)
    sessions = list(normalizer.normalize(src))
    assert sessions, "a real session must yield at least the top-level NormalizedSession"


def test_every_session_starts_with_session_start(normalizer, recorded_name):
    src = recorded_sessions.load_recorded(recorded_name)
    for session in normalizer.normalize(src):
        assert session.events, f"{session.session_id} produced no events"
        assert session.events[0].event_type == "session_start"


def test_every_event_carries_its_raw_record(normalizer, recorded_name):
    """Losslessness (§9): whatever the extracted fields miss is still in `raw`."""
    src = recorded_sessions.load_recorded(recorded_name)
    for session in normalizer.normalize(src):
        for event in session.events:
            assert "raw" in event.payload
            assert isinstance(event.payload["raw"], dict)


def test_no_record_type_is_silently_dropped(normalizer, recorded_name):
    """Every record *type* present in the source must surface as some event's raw -- the
    §3/§8 "shipped, never silently dropped" rule, checked against a real session's full
    spread of record types (mode, attachment, ai-title, file-history-snapshot, ...)."""
    src = recorded_sessions.load_recorded(recorded_name)
    source_types = {r.get("type") for r in src.records}
    raw_types = {
        e.payload["raw"].get("type")
        for session in normalizer.normalize(src)
        for e in session.events
    }
    assert source_types <= raw_types, f"dropped record types: {source_types - raw_types}"


def test_run_twice_is_idempotent_on_real_data(normalizer, recorded_name):
    """The §9 dedup-key invariant: identical input → identical events. Reloads from disk each
    time so laziness, subagent recursion, and ordering are all exercised, not just replayed."""
    first = list(normalizer.normalize(recorded_sessions.load_recorded(recorded_name)))
    second = list(normalizer.normalize(recorded_sessions.load_recorded(recorded_name)))
    assert [s.session_id for s in first] == [s.session_id for s in second]
    for s1, s2 in zip(first, second):
        assert _fingerprint(s1.events) == _fingerprint(s2.events)


# -- subagent-specific (the real spawner fixture) --------------------------------------------

def test_real_subagents_discovered_and_linked(normalizer):
    src = recorded_sessions.load_recorded("session-subagents")
    by_id = {s.session_id: s for s in normalizer.normalize(src)}

    assert "session-subagents" in by_id
    children = [s for s in by_id.values() if s.parent_session_id == "session-subagents"]
    assert len(children) == 2, "the fixture spawns exactly two Task subagents"

    parent = by_id["session-subagents"]
    spawns = [e for e in parent.events if e.event_type == "sub_agent_spawn"]
    assert len(spawns) == 2
    for spawn in spawns:
        child_id = spawn.payload["child_session_id"]
        assert child_id in by_id, "each spawn must point at a discovered child session"
        assert by_id[child_id].parent_session_id == "session-subagents"


def test_real_subagent_children_are_preorder_after_parent(normalizer):
    src = recorded_sessions.load_recorded("session-subagents")
    order = [s.session_id for s in normalizer.normalize(src)]
    assert order[0] == "session-subagents", "parent is yielded before its children (§7 pre-order)"
    assert set(order[1:]) == {"agent-a15f40f2871d0aa1b", "agent-a91d4028434ad77ae"}
