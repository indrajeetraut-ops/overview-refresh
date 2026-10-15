"""Typed, non-executable expression tree for QuerySpec filters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Field:
    path: tuple[str, ...]


@dataclass(frozen=True)
class Function:
    name: str
    field: Field


@dataclass(frozen=True)
class Comparison:
    left: Field | Function
    operator: str
    right: Any


@dataclass(frozen=True)
class InComparison:
    left: Field | Function
    values: tuple[Any, ...]


@dataclass(frozen=True)
class StringComparison:
    left: Field | Function
    operator: str
    right: str


@dataclass(frozen=True)
class Not:
    expression: Any


@dataclass(frozen=True)
class Boolean:
    operator: str
    expressions: tuple[Any, ...]
