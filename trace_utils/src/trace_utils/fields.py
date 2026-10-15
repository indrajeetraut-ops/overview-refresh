"""Shared helpers to turn discovered dotted field paths into DuckDB expressions."""
from __future__ import annotations


def path_to_expr(path: str) -> str:
    """Convert a dotted path to a DuckDB scalar expression.

    ``permissionMode``          -> "permissionMode"
    ``message.model``           -> "message"['model']
    ``message.usage.output_tokens`` -> "message"['usage']['output_tokens']

    Struct/MAP bracket access is used for nested segments (works for the STRUCTs that
    read_json_auto infers). List (``[]``) segments are not supported here — full-text
    search stringifies the whole subtree instead.
    """
    if "[]" in path:
        raise ValueError(f"list path {path!r} is not usable as a scalar predicate")
    segs = path.split(".")
    expr = '"' + segs[0].replace('"', '""') + '"'
    for s in segs[1:]:
        expr += "[" + "'" + s.replace("'", "''") + "'" + "]"
    return expr


def as_text(path: str) -> str:
    """Expression casting a path (possibly a nested subtree) to VARCHAR for matching."""
    return f"CAST({path_to_expr(path)} AS VARCHAR)"
