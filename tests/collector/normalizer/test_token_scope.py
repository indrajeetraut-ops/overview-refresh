"""Response-scoped usage on block-scoped events (spec: docs/modules/normalizer.md §3.1).

Claude Code splits one API response across several JSONL records -- a thinking-only record,
then a text-only one, then a tool_use-only one, all sharing one `message.id` -- and repeats the
*whole* usage block on each. The normalizer emits one event per record, so summing any token
field across events over-counts. On the reference corpus: 116,755,244 naive against 30,022,598
deduped, a 3.89x inflation, and nothing in the output said so.

The values are not wrong. They are response-scoped and replicated onto block-scoped events.
`usage_scope` says that, `message_id` groups them, and `first_block_of_message` picks exactly
one event per response. Purely additive -- the usage fields are untouched, so nothing reading
them today changes behaviour.
"""
import pytest

from collector.normalizer.claude_code import ClaudeCodeNormalizer
from fixtures.normalizer import sessions

_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _multi_block_session():
    """One response split across three records, exactly as Claude Code writes it: same
    message.id, same usage repeated, one content block each."""
    usage = {
        "input_tokens": 3384,
        "output_tokens": 239,
        "cache_read_input_tokens": 19245,
        "cache_creation_input_tokens": 7652,
    }

    def record(uuid, parent, blocks):
        return {
            "type": "assistant",
            "uuid": uuid,
            "parentUuid": parent,
            "timestamp": "2026-07-09T18:00:00.000Z",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-5",
                "id": "msg_shared_response",
                "content": blocks,
                "usage": dict(usage),
                "stop_reason": "tool_use",
            },
        }

    src = sessions.simple_session()
    src.records = [
        src.records[0],
        record("evt-a", "evt-1", [{"type": "thinking", "thinking": "", "signature": "sig"}]),
        record("evt-b", "evt-a", [{"type": "text", "text": "Running the tests."}]),
        record("evt-c", "evt-b", [{"type": "tool_use", "id": "toolu_x", "name": "Bash", "input": {"command": "ls"}}]),
    ]
    return src


def _assistants(session):
    return [e for e in session.events if e.event_type == "assistant_message"]


# -- the markers ---------------------------------------------------------------------------


def test_split_response_shares_one_message_id(normalizer, normalize_one):
    session = normalize_one(normalizer, _multi_block_session())
    assert {e.payload["message_id"] for e in _assistants(session)} == {"msg_shared_response"}


def test_exactly_one_event_is_flagged_first(normalizer, normalize_one):
    session = normalize_one(normalizer, _multi_block_session())
    flags = [e.payload["first_block_of_message"] for e in _assistants(session)]
    assert flags == [True, False, False]


def test_usage_values_are_left_intact_on_every_event(normalizer, normalize_one):
    """Additive, not destructive: nulling the repeats would change an existing field's meaning
    and cut against §9 losslessness."""
    session = normalize_one(normalizer, _multi_block_session())
    for event in _assistants(session):
        assert event.payload["input_tokens"] == 3384
        assert event.payload["cache_read_tokens"] == 19245
        assert event.payload["usage_scope"] == "response"


def test_dedup_filter_yields_the_response_total(normalizer, normalize_one):
    session = normalize_one(normalizer, _multi_block_session())
    events = _assistants(session)
    naive = sum(e.payload["input_tokens"] for e in events)
    deduped = sum(e.payload["input_tokens"] for e in events if e.payload["first_block_of_message"])
    assert (naive, deduped) == (3384 * 3, 3384)


@pytest.mark.parametrize("field", _TOKEN_FIELDS)
def test_every_token_field_dedups_the_same_way(normalizer, normalize_one, field):
    session = normalize_one(normalizer, _multi_block_session())
    first = [e for e in _assistants(session) if e.payload["first_block_of_message"]]
    assert len(first) == 1
    assert first[0].payload[field] == {"input_tokens": 3384, "output_tokens": 239,
                                       "cache_read_tokens": 19245, "cache_write_tokens": 7652}[field]


def test_distinct_responses_are_each_flagged_first(normalizer, normalize_one):
    """tool_pairing_session has two responses with different message ids and no splitting --
    every event must count."""
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    events = _assistants(session)
    assert all(e.payload["first_block_of_message"] for e in events)
    assert len({e.payload["message_id"] for e in events}) == len(events)


def test_missing_message_id_fails_open(normalizer, normalize_one):
    """A record we cannot group counts as its own response. Failing open preserves the total;
    failing closed would silently drop tokens."""
    src = _multi_block_session()
    for rec in src.records[1:]:
        rec["message"].pop("id")
    session = normalize_one(normalizer, src)
    events = _assistants(session)
    assert [e.payload["message_id"] for e in events] == [None, None, None]
    assert all(e.payload["first_block_of_message"] for e in events)


# -- the sibling guard ---------------------------------------------------------------------


def test_tool_call_carries_message_id_for_sibling_detection(normalizer, normalize_one):
    """Two tool calls in one response are siblings -- requested together, no ordering, no
    dependency. Sharing a message_id is what makes that checkable instead of assumed."""
    src = sessions.simple_session()
    src.records[1]["message"]["id"] = "msg_parallel"
    src.records[1]["message"]["content"] = [
        {"type": "tool_use", "id": "toolu_a", "name": "Read", "input": {"file_path": "a.py"}},
        {"type": "tool_use", "id": "toolu_b", "name": "Read", "input": {"file_path": "b.py"}},
    ]
    session = normalize_one(normalizer, src)
    calls = [e for e in session.events if e.event_type == "tool_call"]
    assert [e.payload["message_id"] for e in calls] == ["msg_parallel", "msg_parallel"]


def test_file_change_and_spawn_carry_message_id(normalizer, normalize_one):
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    change = next(e for e in session.events if e.event_type == "file_change")
    call = next(e for e in session.events if e.payload.get("tool_name") == "Edit")
    assert change.payload["message_id"] == call.payload["message_id"] is not None

    parent = next(iter(normalizer.normalize(sessions.mixed_model_subagent_session())))
    spawns = [e for e in parent.events if e.event_type == "sub_agent_spawn"]
    assert len({e.payload["message_id"] for e in spawns}) == 1  # both spawned in one response


def test_tool_result_has_no_message_id(normalizer, normalize_one):
    """A tool_result is a user-role record and carries no message.id -- confirmed across all
    5,404 user records in the reference corpus. Nothing to group it by, and no usage on it."""
    session = normalize_one(normalizer, sessions.tool_pairing_session())
    results = [e for e in session.events if e.event_type == "tool_result"]
    assert results and all("message_id" not in e.payload for e in results)


# -- scoping -------------------------------------------------------------------------------


def test_seen_ids_do_not_leak_across_sessions(normalizer):
    """message.id is unique within a transcript, and a subagent stream is its own session. A
    normalizer-wide set would mark a child's first block as a repeat."""
    for build in (sessions.mixed_model_subagent_session, sessions.mixed_model_subagent_session):
        for session in normalizer.normalize(build()):
            firsts = [e for e in session.events if e.event_type == "assistant_message"
                      and e.payload["first_block_of_message"]]
            ids = {e.payload["message_id"] for e in session.events
                   if e.event_type == "assistant_message" and e.payload["message_id"]}
            assert len(firsts) >= len(ids)


def test_reused_normalizer_instance_is_not_stateful(normalizer, normalize_one):
    """Same source twice through one normalizer must produce identical flags."""
    first = normalize_one(normalizer, _multi_block_session())
    second = normalize_one(normalizer, _multi_block_session())
    assert [e.payload["first_block_of_message"] for e in _assistants(first)] == \
           [e.payload["first_block_of_message"] for e in _assistants(second)]
