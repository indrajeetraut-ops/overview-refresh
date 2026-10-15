import pytest

from server.api.envelope import aggregate_mode_block, mode_block, sourced, unavailable

from .conformance import (
    assert_aggregate_envelope,
    assert_session_scoped_envelope,
    assert_sourced,
    assert_unavailable,
)


def test_sourced_shape():
    figure = sourced(1234, "otel_measures")
    assert figure == {"value": 1234, "source": "otel_measures"}
    assert_sourced(figure, "otel_measures")


def test_unavailable_shape_is_never_zero_or_empty():
    block = unavailable("by_query_source", "otel")
    assert block == {"unavailable": {"block": "by_query_source", "requires": "otel"}}
    assert block != {}
    assert block != 0
    assert_unavailable(block, "otel")


def test_mode_block_session_scoped():
    response = {"session_id": "s1", **mode_block("both", "claude_code")}
    assert_session_scoped_envelope(response)
    assert response["datasource"] == "both"
    assert response["vendor"] == "claude_code"


def test_mode_block_accepts_null_datasource_for_a_pre_tick_row():
    # PM ground rule 2: a session_info row the true-up hasn't ticked yet has no mode. The
    # envelope must pass that through honestly, not coerce it to a guessed mode.
    response = mode_block(None, "claude_code")
    assert_session_scoped_envelope(response)
    assert response["datasource"] is None


def test_aggregate_mode_block():
    response = aggregate_mode_block(["both", "otel_only"], {"both": 4, "otel_only": 2})
    assert_aggregate_envelope(response)
    assert response["modes_present"] == ["both", "otel_only"]
    assert response["sessions_by_mode"] == {"both": 4, "otel_only": 2}


def test_conformance_checker_rejects_a_missing_mode_block():
    with pytest.raises(AssertionError):
        assert_session_scoped_envelope({"session_id": "s1"})
    with pytest.raises(AssertionError):
        assert_aggregate_envelope({"z1": {}})


def test_cold_start_empty_and_unavailable_are_three_distinct_shapes():
    """The three states rule 3 exists to keep apart. A block that hasn't run yet is `None`
    (cold-start); a block that ran and found nothing is a real empty value; a block this mode
    cannot ever produce is `unavailable(...)`. Collapsing any two of these into each other is
    the exact bug A1 exists to prevent."""
    cold_start = None
    empty = []
    not_derivable = unavailable("provenance", "jsonl")

    assert cold_start is None
    assert empty == [] and empty is not None
    assert_unavailable(not_derivable)
    assert cold_start != empty
    assert empty != not_derivable
    assert not_derivable != cold_start
