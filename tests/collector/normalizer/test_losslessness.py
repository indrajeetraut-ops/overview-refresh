from fixtures.normalizer import sessions


def _fingerprint(events):
    return [(e.event_type, e.record_id, e.parent_id, e.payload) for e in events]


def test_raw_record_travels_in_every_event(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    for event in session.events:
        assert "raw" in event.payload
        assert event.payload["raw"].get("type") is not None


def test_unmapped_types_ship_as_unknown_not_dropped(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.passthrough_session())
    shipped_types = {e.payload["raw"].get("type") for e in session.events}
    assert {"mode", "permission-mode", "some-future-record-type"} <= shipped_types


def test_run_twice_idempotency(normalizer):
    builders = [
        sessions.simple_session,
        sessions.tool_pairing_session,
        sessions.mixed_model_subagent_session,
        sessions.api_error_and_compaction_session,
        sessions.passthrough_session,
    ]
    for build in builders:
        first = list(normalizer.normalize(build()))
        second = list(normalizer.normalize(build()))
        assert [s.session_id for s in first] == [s.session_id for s in second]
        for s1, s2 in zip(first, second):
            assert _fingerprint(s1.events) == _fingerprint(s2.events)
