"""The committed corpus exercises the complete Python consumer lifecycle."""
from pathlib import Path

from .validate_consumer_interface import validate


def test_consumer_interface_against_committed_corpus() -> None:
    root = Path(__file__).parents[3]
    result = validate(root / "examples/causal_graph/graphs")
    assert result["failed"] == 0
    assert result["cases"] >= 30
