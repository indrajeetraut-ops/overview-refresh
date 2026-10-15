"""Search — structured filters (`--where`) + user-scoped full-text (`--in`).

Predicates target discovered field paths. A session matches if *any* of its events
matches, so search answers "find the sessions I'm interested in". Full-text is
case-insensitive substring (or regex) over the CAST-to-text of a user-chosen field,
which works whether the field is a plain string or a nested content subtree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .engine import Engine
from .fields import as_text, path_to_expr
from .logging import get_logger
from .sessions import list_sessions, SessionSummary
from .sources import ResolvedSource

log = get_logger(__name__)

# order matters: match "!=" and "~" before "="
_PRED_RE = re.compile(r"^\s*([A-Za-z0-9_.\[\]]+)\s*(!=|~|=)\s*(.*?)\s*$")


@dataclass
class Predicate:
    path: str
    op: str  # "=", "!=", "~"
    value: str


def parse_where(expr: str) -> list[Predicate]:
    """Parse ``a=x && b!=y && c~regex`` into predicates (boolean AND for v1)."""
    preds = []
    for clause in expr.split("&&"):
        clause = clause.strip()
        if not clause:
            continue
        m = _PRED_RE.match(clause)
        if not m:
            raise ValueError(f"cannot parse predicate: {clause!r} (expected path=value)")
        path, op, value = m.group(1), m.group(2), m.group(3)
        value = value.strip().strip("'\"")
        preds.append(Predicate(path=path, op=op, value=value))
    return preds


def _pred_sql(p: Predicate) -> str:
    lit = "'" + p.value.replace("'", "''") + "'"
    txt = as_text(p.path)
    if p.op == "=":
        return f"{txt} = {lit}"
    if p.op == "!=":
        return f"{txt} <> {lit}"
    if p.op == "~":
        return f"regexp_matches({txt}, {lit})"
    raise ValueError(f"unknown operator {p.op!r}")


def build_where_sql(
    where: str | None = None,
    in_field: str | None = None,
    in_term: str | None = None,
    regex: bool = False,
) -> str | None:
    """Compile filters + full-text into a single SQL boolean condition (or None)."""
    conds: list[str] = []
    if where:
        conds.extend(_pred_sql(p) for p in parse_where(where))
    if in_field and in_term is not None:
        txt = as_text(in_field)
        lit_val = in_term.replace("'", "''")
        if regex:
            conds.append(f"regexp_matches({txt}, '{lit_val}')")
        else:
            like = lit_val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conds.append(f"{txt} ILIKE '%{like}%' ESCAPE '\\'")
    if not conds:
        return None
    return " AND ".join(f"({c})" for c in conds)


def search(
    engine: Engine,
    rs: ResolvedSource,
    where: str | None = None,
    in_field: str | None = None,
    in_term: str | None = None,
    regex: bool = False,
    limit: int = 50,
) -> list[SessionSummary]:
    """Return sessions with at least one event matching the filters/full-text."""
    where_sql = build_where_sql(where, in_field, in_term, regex)
    log.info("search", has_where=bool(where), in_field=in_field, has_term=in_term is not None)
    return list_sessions(engine, rs, limit=limit, where_sql=where_sql)
