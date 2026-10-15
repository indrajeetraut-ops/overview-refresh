from __future__ import annotations

import pytest
from pydantic import ValidationError

from analysis.causal_graph.query_models import NodeIdentity, QueryResult, QuerySpec


def _nodes_spec() -> dict:
    return {
        "version": "1",
        "scope": {"kind": "tree"},
        "select": {"type": "Tool", "where": "Tool.name == 'Read'"},
        "return": {"kind": "nodes", "verbosity": "summary"},
    }


def test_design_node_example_validates_without_translation():
    spec = _nodes_spec()
    spec["order_by"] = [{"field": "Tool.ts", "direction": "asc"}]
    spec["limit"] = 100
    parsed = QuerySpec.model_validate(spec)
    assert parsed.select.type == "Tool"
    assert parsed.return_.verbosity == "summary"


def test_design_group_example_validates_without_translation():
    spec = _nodes_spec()
    spec.update({
        "group_by": ["Tool.name"],
        "aggregates": [{"name": "call_count", "function": "COUNT"}],
        "having": "call_count > 5",
        "return": {"kind": "groups", "verbosity": "summary", "include_members": False},
    })
    assert QuerySpec.model_validate(spec).aggregates[0].function.value == "COUNT"


@pytest.mark.parametrize("mutate", [
    lambda spec: spec.update({"unknown": True}),
    lambda spec: spec["scope"].update({"typo": "tree"}),
    lambda spec: spec["return"].update({"unknown": True}),
    lambda spec: spec["scope"].update({"session_id": "s1"}),
    lambda spec: spec.update({"limit": 0}),
])
def test_unknown_or_invalid_request_clauses_are_rejected(mutate):
    spec = _nodes_spec()
    mutate(spec)
    with pytest.raises(ValidationError):
        QuerySpec.model_validate(spec)


@pytest.mark.parametrize("mutate", [
    lambda spec: spec.update({"group_by": ["Tool.name"], "return": {"kind": "nodes", "verbosity": "summary"}}),
    lambda spec: spec.update({"return": {"kind": "groups", "verbosity": "summary"}}),
    lambda spec: spec.update({"return": {"kind": "aggregate"}}),
    lambda spec: spec.update({"aggregates": [{"name": "n", "function": "COUNT"}]}),
    lambda spec: spec.update({"having": "n > 1"}),
])
def test_invalid_clause_combinations_are_rejected(mutate):
    spec = _nodes_spec()
    mutate(spec)
    with pytest.raises(ValidationError):
        QuerySpec.model_validate(spec)


def test_session_scope_requires_and_accepts_its_session_id():
    spec = _nodes_spec()
    spec["scope"] = {"kind": "SESSION", "session_id": "child"}
    assert QuerySpec.model_validate(spec).scope.kind == "session"


def test_result_identity_never_accepts_a_bare_family_wide_node_id():
    with pytest.raises(ValidationError):
        NodeIdentity.model_validate({"node_id": "same-in-two-sessions"})
    with pytest.raises(ValidationError):
        NodeIdentity.model_validate({"session_id": "s", "node_id": "n", "turn_num": 1})


def test_query_result_exposes_exactly_one_result_collection():
    result = QueryResult.model_validate({
        "version": "1", "status": "complete", "kind": "nodes", "items": [],
        "truncated": False, "warnings": [],
    })
    assert result.items == []
    with pytest.raises(ValidationError):
        QueryResult.model_validate({
            "version": "1", "status": "complete", "kind": "nodes", "items": [], "groups": [],
            "truncated": False, "warnings": [],
        })
