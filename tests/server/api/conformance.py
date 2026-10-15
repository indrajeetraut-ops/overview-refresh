"""Shared envelope-conformance assertions (A1's "cheap guard against rule 3 being quietly
skipped"). Not a test module itself -- imported by each endpoint's test file (A3-A8) so every
response is checked against the same mode-contract shape, rather than each endpoint re-deriving
its own notion of "looks about right".
"""
from __future__ import annotations


def assert_session_scoped_envelope(response: dict) -> None:
    """DASH §8.1 rule 1 for a single-session response (A5, each row of A4)."""
    assert "datasource" in response, "session-scoped response missing datasource"
    assert "vendor" in response, "session-scoped response missing vendor"
    assert response["datasource"] in (None, "both", "jsonl_only", "otel_only")


def assert_aggregate_envelope(response: dict) -> None:
    """DASH §8.1 rule 1 for a timeframe/user-scoped response (A3, A7)."""
    assert "modes_present" in response, "aggregate response missing modes_present"
    assert "sessions_by_mode" in response, "aggregate response missing sessions_by_mode"
    assert isinstance(response["modes_present"], list)
    assert isinstance(response["sessions_by_mode"], dict)


def assert_unavailable(block: dict | None, requires: str | None = None) -> None:
    """Rule 3's `unavailable` shape specifically -- distinct from cold-start (`None`) and
    empty (a real empty value), which callers should assert directly rather than through
    here, since conflating the three shapes is exactly the bug this exists to catch."""
    assert isinstance(block, dict), f"expected an unavailable() shape, got {block!r}"
    assert "unavailable" in block
    assert "block" in block["unavailable"]
    assert "requires" in block["unavailable"]
    if requires is not None:
        assert block["unavailable"]["requires"] == requires


def assert_sourced(figure: dict, expected_source: str | None = None) -> None:
    """Rule 2's `sourced()` shape."""
    assert isinstance(figure, dict), f"expected a sourced() shape, got {figure!r}"
    assert "value" in figure
    assert "source" in figure
    if expected_source is not None:
        assert figure["source"] == expected_source


def assert_cold_start(block) -> None:
    """Rule 3's cold-start shape (R7.6/R7.7): a capability that will compute once its engine
    has run, distinct from `unavailable` (structurally absent, mode-gated) and a real empty
    value (the engine ran, there's nothing there). Plain `None`, never `unavailable()`'s dict
    and never `[]`/`0`/`{}` -- conflating any of the three is exactly the bug this (and
    `assert_unavailable`) exist to catch."""
    assert block is None, f"expected cold-start (None), got {block!r}"
