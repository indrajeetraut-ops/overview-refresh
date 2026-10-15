from analysis.causal_graph.field_registry import FieldRegistry, MISSING, default_summary_fields
from analysis.causal_graph.models import Node, NodeType


def test_registry_exposes_logical_tool_aliases_only():
    tool = Node(id="t", type=NodeType.TOOL, attrs={"tool_name": "Bash", "cmd": ["git"], "result": {"outcome": "error"}})
    registry = FieldRegistry()
    assert registry.value(tool, "Tool.name") == "Bash"
    assert registry.value(tool, "Tool.cmd") == ["git"]
    assert registry.value(tool, "Tool.outcome") == "failure"
    assert registry.value(tool, "Turn.status") is MISSING
    assert "Tool.cmd" in default_summary_fields(NodeType.TOOL)


def test_registry_exposes_memory_access_as_a_stored_tool_field():
    tool = Node(id="t", type=NodeType.TOOL, attrs={"memory_access": True})
    registry = FieldRegistry()
    registry.validate("Tool.memory_access", NodeType.TOOL)
    assert registry.value(tool, "Tool.memory_access") is True


def test_tool_outcome_distinguishes_completed_failure_and_unknown_calls():
    registry = FieldRegistry()
    success = Node(id="success", type=NodeType.TOOL, attrs={
        "result": {"outcome": "ok", "process": {"interrupted": False}},
    })
    interrupted = Node(id="interrupted", type=NodeType.TOOL, attrs={
        "result": {"outcome": "ok", "process": {"interrupted": True}},
    })
    unknown = Node(id="unknown", type=NodeType.TOOL, attrs={"is_error": False})

    assert registry.value(success, "Tool.outcome") == "success"
    assert registry.value(interrupted, "Tool.outcome") == "failure"
    assert registry.value(unknown, "Tool.outcome") == "unknown"
