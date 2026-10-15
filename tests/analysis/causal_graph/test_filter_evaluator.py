from analysis.causal_graph.field_registry import FieldRegistry
from analysis.causal_graph.filter_parser import FilterError, evaluate, parse_filter
from analysis.causal_graph.models import Node, NodeType
from analysis.causal_graph.query_config import load_query_config


def _tool():
    return Node(id="t", type=NodeType.TOOL, attrs={"tool_name": "Bash", "cmd": ["git", "git", "sed"], "input": {"command": "git status"}, "is_error": False})


def _matches(text: str) -> bool:
    config = load_query_config()
    return evaluate(parse_filter(text, config), _tool(), FieldRegistry(), config)


def test_collection_membership_nulls_and_casefolding():
    assert _matches("Tool.cmd == 'git'")
    assert _matches("Tool.cmd IN ['nope', 'sed']")
    assert _matches("CASEFOLD(Tool.name) == 'bash'")
    assert _matches("missing == null")
    assert not _matches("Tool.name == 'bash'")


def test_string_and_re2_operations():
    assert _matches("Tool.input.command CONTAINS 'status'")
    assert _matches("Tool.input.command MATCHES '^git\\\\s+status$'")
    with __import__("pytest").raises(FilterError, match="regular expression"):
        _matches("Tool.name MATCHES '" + ("x" * 600) + "'")
