from analysis.causal_graph.filter_ast import Boolean, Comparison, Not
from analysis.causal_graph.filter_parser import FilterError, parse_filter
from analysis.causal_graph.query_config import load_query_config


def test_precedence_and_quoted_literals():
    expr = parse_filter("Tool.name == 'Read' OR Tool.name == \"Edit\" AND NOT Tool.outcome == 'failure'", load_query_config())
    assert isinstance(expr, Boolean) and expr.operator == "OR"
    assert isinstance(expr.expressions[1], Boolean) and isinstance(expr.expressions[1].expressions[1], Not)


def test_syntax_error_has_character_offset():
    try:
        parse_filter("Tool.name ==", load_query_config())
    except FilterError as exc:
        assert exc.code == "filter_syntax" and exc.location is not None
    else:
        raise AssertionError("invalid syntax was accepted")
