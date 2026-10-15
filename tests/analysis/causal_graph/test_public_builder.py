"""Public storage-neutral graph builder contract."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from analysis.causal_graph import GraphBuildError, build_graph
from analysis.causal_graph.models import Graph

FIXTURE = Path(__file__).parents[2] / "fixtures/causal_graph/tiny_session.json"


def test_build_graph_returns_round_trippable_plain_dictionary() -> None:
    session = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = build_graph(session)

    assert type(result) is dict
    assert result["session_id"] == session["session_id"]
    assert result["parent_session_id"] == session.get("parent_session_id")
    assert Graph.from_dict(result).to_dict() == result


def test_build_graph_preserves_subagent_identity() -> None:
    session = json.loads(FIXTURE.read_text(encoding="utf-8"))
    session["session_id"] = "child-1"
    session["parent_session_id"] = "master-1"
    result = build_graph(session)
    assert (result["session_id"], result["parent_session_id"]) == ("child-1", "master-1")


@pytest.mark.parametrize("value", [None, [], {}, {"vendor": "claude_code"}])
def test_build_graph_rejects_malformed_normalized_session(value: object) -> None:
    with pytest.raises(GraphBuildError) as raised:
        build_graph(value)  # type: ignore[arg-type]
    assert raised.value.code == "graph_build_failed"


def test_build_family_does_not_call_public_serializing_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    from analysis.causal_graph import builder, family

    session = json.loads(FIXTURE.read_text(encoding="utf-8"))
    monkeypatch.setattr(builder, "build_graph", lambda *_args, **_kwargs: pytest.fail("public builder called"))
    result = family.build_family({"sessions": [session]})
    assert result.master.session_id == session["session_id"]


def test_build_graph_example_keeps_storage_in_consumer(tmp_path: Path) -> None:
    destination = tmp_path / "graph.json"
    run = subprocess.run(
        [sys.executable, "examples/causal_graph/build_graph.py", str(FIXTURE), str(destination)],
        cwd=Path(__file__).parents[3], capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(destination.read_text(encoding="utf-8"))["session_id"] == "sess-tiny-0001"
