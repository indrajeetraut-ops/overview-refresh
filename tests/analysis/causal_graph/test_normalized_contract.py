"""Contract guard: every payload field `build_graph` reads must still be published in
`src/common/events/normalized_schema.json`.

The builder reads every key through `.get(...)`, so an upstream rename or removal returns
`None` silently -- no exception, no failing test, just null attrs and short budgets. This is
what catches that: `_REQUIRED_PAYLOAD_FIELDS` names exactly what each transform reads, and
this test fails loud the moment one of those names stops being in the published schema. Had
the builder read `visible_output_tokens`, the 2.0.0 rename (`estimated_visible_tokens`) would
have done exactly the silent-null thing this guards against.

Does not catch the builder reading a field it never declared here -- that half is a code-review
concern, not a test's.
"""
import json
from pathlib import Path

from analysis.causal_graph.builder import _REQUIRED_PAYLOAD_FIELDS

SCHEMA_PATH = Path(__file__).parents[3] / "src" / "common" / "events" / "normalized_schema.json"


def _schema_fields() -> dict[str, set[str]]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return {
        event_type: set(spec["fields"])
        for event_type, spec in schema["payload_by_type"].items()
    }


def test_every_required_event_type_is_published():
    published = _schema_fields()
    missing_types = set(_REQUIRED_PAYLOAD_FIELDS) - set(published)
    assert not missing_types, f"event types the builder reads but the schema no longer has: {missing_types}"


def test_every_required_field_is_published():
    published = _schema_fields()
    problems = []
    for event_type, fields in _REQUIRED_PAYLOAD_FIELDS.items():
        missing = fields - published.get(event_type, set())
        if missing:
            problems.append(f"{event_type}: {sorted(missing)}")
    assert not problems, "builder reads fields the schema no longer publishes:\n" + "\n".join(problems)


def test_required_fields_is_non_trivial():
    """A guard over an empty dict would pass forever and catch nothing -- pin a floor so an
    accidental gutting of the map fails here instead of silently stopping to protect anything."""
    total = sum(len(fields) for fields in _REQUIRED_PAYLOAD_FIELDS.values())
    assert total >= 20
    assert "assistant_message" in _REQUIRED_PAYLOAD_FIELDS
    assert "estimated_visible_tokens" in _REQUIRED_PAYLOAD_FIELDS["assistant_message"]
    assert "tool_call_count" in _REQUIRED_PAYLOAD_FIELDS["assistant_message"]
    assert "reasoning_tokens" in _REQUIRED_PAYLOAD_FIELDS["assistant_message"]
