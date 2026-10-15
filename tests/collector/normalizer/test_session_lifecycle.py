from fixtures.normalizer import sessions


def test_session_start_uses_first_record_context(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    start = session.events[0]
    assert start.event_type == "session_start"
    assert start.payload["cwd"] == "/Users/dev/repo"
    assert start.payload["git_branch"] == "main"
    assert start.payload["repo_id"] is None
    assert start.parent_id is None
    assert start.record_id is None


def test_no_session_end_emitted(normalizer):
    builders = [
        sessions.simple_session,
        sessions.tool_pairing_session,
        sessions.api_error_and_compaction_session,
        sessions.passthrough_session,
    ]
    for build in builders:
        (session,) = list(normalizer.normalize(build()))
        assert all(e.event_type != "session_end" for e in session.events)


def test_subagent_sessions_also_get_their_own_session_start(normalizer):
    normalized = list(normalizer.normalize(sessions.mixed_model_subagent_session()))
    for session in normalized:
        assert session.events[0].event_type == "session_start"
