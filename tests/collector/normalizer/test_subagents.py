from fixtures.normalizer import sessions


def test_spawn_events_emitted_for_both_children(normalizer):
    normalized = list(normalizer.normalize(sessions.mixed_model_subagent_session()))
    parent = normalized[0]
    spawns = [e for e in parent.events if e.event_type == "sub_agent_spawn"]
    assert len(spawns) == 2

    sonnet_spawn = next(e for e in spawns if e.payload["child_session_id"] == "agent-sonnet-1")
    assert sonnet_spawn.payload["agent_type"] == "Explore"
    assert sonnet_spawn.record_id == "toolu_spawn_sonnet"

    haiku_spawn = next(e for e in spawns if e.payload["child_session_id"] == "agent-haiku-1")
    assert haiku_spawn.payload["agent_type"] == "Plan"
    assert haiku_spawn.record_id == "toolu_spawn_haiku"


def test_children_yielded_as_own_sessions_with_own_models(normalizer):
    normalized = list(normalizer.normalize(sessions.mixed_model_subagent_session()))
    by_id = {s.session_id: s for s in normalized}
    assert set(by_id) == {"session-parent", "agent-sonnet-1", "agent-haiku-1", "agent-grandchild-1"}

    def model_of(session_id):
        session = by_id[session_id]
        return next(e for e in session.events if e.event_type == "assistant_message").payload["model"]

    assert model_of("session-parent") == "claude-fable-5"
    assert model_of("agent-sonnet-1") == "claude-sonnet-5"
    assert model_of("agent-haiku-1") == "claude-haiku-5"
    assert model_of("agent-grandchild-1") == "claude-haiku-5"

    assert by_id["agent-sonnet-1"].parent_session_id == "session-parent"
    assert by_id["agent-haiku-1"].parent_session_id == "session-parent"
    assert by_id["agent-grandchild-1"].parent_session_id == "agent-sonnet-1"


def test_incomplete_subagent_still_ships_its_own_events(normalizer):
    normalized = list(normalizer.normalize(sessions.mixed_model_subagent_session()))
    haiku = next(s for s in normalized if s.session_id == "agent-haiku-1")

    assert [e.event_type for e in haiku.events] == [
        "session_start",
        "user_message",
        "assistant_message",
        "tool_call",
        "tool_result",
    ]
    tool_call = next(e for e in haiku.events if e.event_type == "tool_call")
    assert tool_call.payload["tool_name"] == "Read"
    assert tool_call.payload["exit_status"] == 0  # its own tool_result did arrive


def test_parent_yielded_before_children_preorder(normalizer):
    normalized = list(normalizer.normalize(sessions.mixed_model_subagent_session()))
    order = [s.session_id for s in normalized]
    assert order == ["session-parent", "agent-sonnet-1", "agent-grandchild-1", "agent-haiku-1"]
