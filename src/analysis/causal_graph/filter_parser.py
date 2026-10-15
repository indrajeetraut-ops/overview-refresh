"""Lark parser and evaluator for the safe causal-graph filter subset."""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path
from typing import Any

import re2
from lark import Lark, Transformer, UnexpectedInput

from .field_registry import FieldRegistry, MISSING
from .filter_ast import Boolean, Comparison, Field, Function, InComparison, Not, StringComparison
from .models import Node
from .query_config import QueryConfig

__all__ = ["FilterError", "evaluate", "parse_filter"]


class FilterError(ValueError):
    def __init__(self, code: str, message: str, *, location: int | None = None) -> None:
        self.code, self.location = code, location
        super().__init__(message)


@lru_cache(maxsize=1)
def _parser() -> Lark:
    grammar = (Path(__file__).resolve().parents[2] / "common/contracts/causal_graph/query_language/filter.lark")
    return Lark(grammar.read_text(encoding="utf-8"), parser="lalr", start="start")


class _TreeToAst(Transformer):
    def field(self, values): return Field(tuple(str(value) for value in values))
    def casefold(self, values): return Function("CASEFOLD", values[1] if len(values) > 1 else values[0])
    def length(self, values): return Function("LENGTH", values[1] if len(values) > 1 else values[0])
    def string(self, values): return ast.literal_eval(str(values[0]))
    def number(self, values):
        text = str(values[0]); return float(text) if any(c in text for c in ".eE") else int(text)
    def true(self, _): return True
    def false(self, _): return False
    def null(self, _): return None
    def comparison(self, values): return Comparison(values[0], str(values[1]), values[2])
    def in_comparison(self, values): return InComparison(values[0], tuple(v for v in values[2:] if str(v) != "IN"))
    def string_comparison(self, values): return StringComparison(values[0], str(values[1]).upper(), values[2])
    def not_expression(self, values): return Not(values[-1])
    def and_expression(self, values):
        expressions = tuple(v for v in values if str(v).upper() != "AND")
        return expressions[0] if len(expressions) == 1 else Boolean("AND", expressions)
    def or_expression(self, values):
        expressions = tuple(v for v in values if str(v).upper() != "OR")
        return expressions[0] if len(expressions) == 1 else Boolean("OR", expressions)


def parse_filter(expression: str, config: QueryConfig):
    if len(expression) > config.filter.max_expression_chars:
        raise FilterError("filter_too_long", "filter exceeds configured maximum")
    try:
        return _TreeToAst().transform(_parser().parse(expression))
    except UnexpectedInput as exc:
        raise FilterError("filter_syntax", str(exc).splitlines()[0], location=exc.pos_in_stream) from exc


def _value(operand: Field | Function, node: Node, registry: FieldRegistry):
    value = registry.value(node, ".".join(operand.path)) if isinstance(operand, Field) else _value(operand.field, node, registry)
    if value is MISSING:
        value = None
    if isinstance(operand, Function):
        if operand.name == "CASEFOLD": return value.casefold() if isinstance(value, str) else None
        if operand.name == "LENGTH": return len(value) if isinstance(value, (str, list, tuple, set, dict)) else None
    return value


def _equals(left: Any, right: Any) -> bool:
    return right in left if isinstance(left, (list, tuple, set)) else left == right


def evaluate(expression, node: Node, registry: FieldRegistry, config: QueryConfig) -> bool:
    if isinstance(expression, Boolean):
        return all(evaluate(item, node, registry, config) for item in expression.expressions) if expression.operator == "AND" else any(evaluate(item, node, registry, config) for item in expression.expressions)
    if isinstance(expression, Not): return not evaluate(expression.expression, node, registry, config)
    if isinstance(expression, Comparison):
        left = _value(expression.left, node, registry); right = expression.right
        if expression.operator == "==": return _equals(left, right)
        if expression.operator == "!=": return not _equals(left, right)
        if left is None or not isinstance(left, (int, float)) or not isinstance(right, (int, float)): return False
        return {">": left > right, ">=": left >= right, "<": left < right, "<=": left <= right}[expression.operator]
    if isinstance(expression, InComparison):
        left = _value(expression.left, node, registry)
        return any(_equals(left, value) for value in expression.values)
    left = _value(expression.left, node, registry)
    if not isinstance(left, str): return False
    if expression.operator == "STARTS_WITH": return left.startswith(expression.right)
    if expression.operator == "ENDS_WITH": return left.endswith(expression.right)
    if expression.operator == "CONTAINS": return expression.right in left
    if len(expression.right) > config.filter.max_regex_chars:
        raise FilterError("regex_too_long", "regular expression exceeds configured maximum")
    try: return re2.search(expression.right, left) is not None
    except re2.error as exc: raise FilterError("regex_invalid", str(exc)) from exc
