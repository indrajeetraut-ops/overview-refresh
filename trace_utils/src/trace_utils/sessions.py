"""Session listing — the thin, best-effort common view over dataset-native data.

Groups events into sessions using a detected id column (falling back to the source
filename, which makes one-file-per-session datasets Just Work) and summarizes each:
event count, time span, and models seen. Everything is best-effort and degrades
gracefully when a field isn't present — the tool never assumes a schema.
"""
from __future__ import annotations

from dataclasses import dataclass

from .engine import Engine
from .logging import get_logger
from .sources import ResolvedSource

log = get_logger(__name__)

_SESSION_ID_CANDIDATES = ["sessionId", "session_id", "conversation_id", "id", "request_id"]
_TS_CANDIDATES = ["timestamp", "event_ts", "ts", "created_at", "time"]
_MODEL_CANDIDATES = ["model", "message.model"]


@dataclass
class Detected:
    session_expr: str
    session_is_filename: bool
    ts_expr: str | None
    model_expr: str | None


def _first_existing_column(engine: Engine, rs: ResolvedSource, candidates: list[str]) -> str | None:
    cols = set(engine.columns(rs))
    for c in candidates:
        if c in cols:
            return c
    return None


def detect(engine: Engine, rs: ResolvedSource) -> Detected:
    """Detect the columns/expressions used to summarize sessions."""
    from .fields import path_to_expr

    sid_col = _first_existing_column(engine, rs, _SESSION_ID_CANDIDATES)
    if sid_col:
        session_expr = f'"{sid_col}"'
        session_is_filename = False
    else:
        session_expr = "filename"  # added by read_* (filename=true)
        session_is_filename = True

    ts_col = _first_existing_column(engine, rs, _TS_CANDIDATES)
    ts_expr = f'"{ts_col}"' if ts_col else None

    model_expr = None
    for cand in _MODEL_CANDIDATES:
        expr = path_to_expr(cand)
        if engine.expr_ok(rs, expr):
            model_expr = expr
            break

    return Detected(
        session_expr=session_expr,
        session_is_filename=session_is_filename,
        ts_expr=ts_expr,
        model_expr=model_expr,
    )


@dataclass
class SessionSummary:
    session_id: str
    n_events: int
    first_ts: str | None
    last_ts: str | None
    models: str | None


def _basename(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def list_sessions(
    engine: Engine, rs: ResolvedSource, limit: int = 50, where_sql: str | None = None
) -> list[SessionSummary]:
    """Summarize sessions, most events first. `where_sql` restricts the underlying rows."""
    d = detect(engine, rs)
    rel = engine.relation(rs)

    ts_min = f"MIN(CAST({d.ts_expr} AS VARCHAR))" if d.ts_expr else "NULL"
    ts_max = f"MAX(CAST({d.ts_expr} AS VARCHAR))" if d.ts_expr else "NULL"
    models = (
        f"string_agg(DISTINCT CAST({d.model_expr} AS VARCHAR), ', ')"
        if d.model_expr else "NULL"
    )
    # Events lacking a session id (snapshots/meta) fold into their file's bucket
    # rather than a junk NULL group. Normalize the filename fallback to its stem
    # (strip path + extension) so it matches the bare session id in file-per-session
    # datasets instead of forming a separate "<id>.jsonl" bucket.
    fname_stem = "regexp_replace(regexp_replace(filename, '^.*/', ''), '\\.[^.]*$', '')"
    if d.session_is_filename:
        group_expr = fname_stem
    else:
        group_expr = f"COALESCE(CAST({d.session_expr} AS VARCHAR), {fname_stem})"

    filters = ["1=1"]
    if rs.session_filter:
        filters.append(f"{d.session_expr} = '{rs.session_filter.replace(chr(39), chr(39)*2)}'")
    if where_sql:
        filters.append(f"({where_sql})")
    where = " AND ".join(filters)

    sql = (
        f"SELECT {group_expr} AS sid, COUNT(*) AS n, "
        f"{ts_min} AS first_ts, {ts_max} AS last_ts, {models} AS models "
        f"FROM {rel} WHERE {where} "
        f"GROUP BY sid ORDER BY n DESC LIMIT {int(limit)}"
    )
    _, rows = engine.query(sql)
    out = []
    for sid, n, first_ts, last_ts, models_v in rows:
        sid = _basename(sid) if sid and "/" in str(sid) else sid
        out.append(SessionSummary(str(sid), int(n), first_ts, last_ts, models_v))
    log.info("sessions_listed", n=len(out), by_filename=d.session_is_filename)
    return out


def session_events(engine: Engine, rs: ResolvedSource, session_id: str) -> tuple[list[str], list[tuple]]:
    """All raw rows for one session, ordered by timestamp when available."""
    d = detect(engine, rs)
    rel = engine.relation(rs)
    match = session_id.replace("'", "''")
    if d.session_is_filename:
        cond = f"filename LIKE '%{match}%'"
    else:
        # match the session id column, and also anything in a file named for the
        # session (folds in snapshot/meta events that carry no session id)
        cond = f"(CAST({d.session_expr} AS VARCHAR) = '{match}' OR filename LIKE '%{match}%')"
    order = f"ORDER BY {d.ts_expr}" if d.ts_expr else ""
    return engine.query(f"SELECT * FROM {rel} WHERE {cond} {order}")
