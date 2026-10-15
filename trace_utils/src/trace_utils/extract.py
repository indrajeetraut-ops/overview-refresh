"""Extract — pull a selected subset (session / glob / --where matches) out to disk or S3.

Distinct from bulk download: extract writes the *result* of a selection to a file the
user keeps or shares, in a chosen format. Uses DuckDB ``COPY`` so ``s3://`` targets work
via httpfs when AWS creds are present in the environment.
"""
from __future__ import annotations

from pathlib import Path

from .engine import Engine
from .logging import get_logger
from .sessions import detect
from .sources import ResolvedSource

log = get_logger(__name__)

_EXT = {"jsonl": ".jsonl", "json": ".json", "parquet": ".parquet"}


def _copy_options(fmt: str) -> str:
    if fmt == "jsonl":
        return "FORMAT json"  # newline-delimited
    if fmt == "json":
        return "FORMAT json, ARRAY true"
    if fmt == "parquet":
        return "FORMAT parquet"
    raise ValueError(f"unsupported extract format {fmt!r}")


def _resolve_out_path(out: str, repo_id: str, fmt: str) -> str:
    ext = _EXT[fmt]
    is_s3 = out.startswith("s3://")
    if out.endswith(ext):
        return out
    if is_s3:
        base = out.rstrip("/")
        return f"{base}/{repo_id.replace('/', '__')}{ext}"
    p = Path(out)
    if p.suffix.lower() in _EXT.values():
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    p.mkdir(parents=True, exist_ok=True)
    return str(p / f"{repo_id.replace('/', '__')}{ext}")


def extract(
    engine: Engine,
    rs: ResolvedSource,
    out: str,
    fmt: str = "jsonl",
    where_sql: str | None = None,
) -> tuple[str, int]:
    """Write matching rows to `out`. Returns (path, n_rows)."""
    rel = engine.relation(rs, filename=True)
    conds = ["1=1"]
    if rs.session_filter:
        d = detect(engine, rs)
        sid = rs.session_filter.replace("'", "''")
        conds.append(f"CAST({d.session_expr} AS VARCHAR) = '{sid}'")
    if where_sql:
        conds.append(f"({where_sql})")
    where = " AND ".join(conds)

    select = f"SELECT * EXCLUDE (filename) FROM {rel} WHERE {where}"
    n = engine.query(f"SELECT COUNT(*) FROM ({select}) t")[1][0][0]

    path = _resolve_out_path(out, rs.repo_id, fmt)
    engine.query(f"COPY ({select}) TO '{path.replace(chr(39), chr(39)*2)}' ({_copy_options(fmt)})")
    log.info("extracted", path=path, n_rows=int(n), fmt=fmt)
    return path, int(n)
