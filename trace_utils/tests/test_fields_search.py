import pytest

from trace_utils.fields import as_text, path_to_expr
from trace_utils.search import build_where_sql, parse_where


def test_path_to_expr_toplevel_and_nested():
    assert path_to_expr("permissionMode") == '"permissionMode"'
    assert path_to_expr("message.model") == '"message"[\'model\']'
    assert path_to_expr("message.usage.output_tokens") == '"message"[\'usage\'][\'output_tokens\']'


def test_list_path_rejected():
    with pytest.raises(ValueError):
        path_to_expr("message.content[].type")


def test_as_text_casts():
    assert as_text("model") == 'CAST("model" AS VARCHAR)'


def test_parse_where_operators():
    preds = parse_where("message.model=claude-opus-4 && permissionMode != default && cwd~/repo")
    assert [(p.path, p.op, p.value) for p in preds] == [
        ("message.model", "=", "claude-opus-4"),
        ("permissionMode", "!=", "default"),
        ("cwd", "~", "/repo"),
    ]


def test_parse_where_quoted_value():
    (p,) = parse_where('user_prompt="hello world"')
    assert p.value == "hello world"


def test_parse_where_bad_clause():
    with pytest.raises(ValueError):
        parse_where("this is not a predicate")


def test_build_where_sql_combines_filter_and_fts():
    sql = build_where_sql(where="model=opus", in_field="message.content", in_term="sound")
    assert 'CAST("model" AS VARCHAR) = \'opus\'' in sql
    assert "ILIKE '%sound%'" in sql
    assert " AND " in sql


def test_build_where_sql_regex_and_escaping():
    sql = build_where_sql(in_field="content", in_term="a%b_c", regex=False)
    assert "\\%" in sql and "\\_" in sql  # LIKE metacharacters escaped
    rx = build_where_sql(in_field="content", in_term="foo.*bar", regex=True)
    assert "regexp_matches" in rx


def test_build_where_sql_none_when_empty():
    assert build_where_sql() is None
