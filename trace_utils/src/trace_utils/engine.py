"""DuckDB scan/query engine.

Central place that knows how to turn a :class:`ResolvedSource` into a readable DuckDB
relation (``read_parquet`` / ``read_json_auto``), run queries, and sample rows. All
parquet/jsonl reading and remote (hf://, s3://) access lives behind this seam so tests
can point at local fixture files unchanged.
"""
from __future__ import annotations

from typing import Any

import duckdb

from .logging import get_logger
from .sources import ResolvedSource

log = get_logger(__name__)

_MAX_OBJECT_SIZE = 128 * 1024 * 1024  # jsonl events can be large (full content)


def _sql_list(uris: list[str]) -> str:
    inner = ", ".join("'" + u.replace("'", "''") + "'" for u in uris)
    return f"[{inner}]"


class Engine:
    def __init__(self, token: str | None = None):
        self.con = duckdb.connect()
        for ext in ("httpfs", "json"):
            try:
                self.con.execute(f"INSTALL {ext}; LOAD {ext};")
            except Exception as e:  # pragma: no cover - environment dependent
                log.warning("extension_load_failed", extension=ext, error=str(e))
        if token:
            try:
                self.con.execute(
                    "CREATE OR REPLACE SECRET hf (TYPE huggingface, TOKEN ?)", [token]
                )
            except Exception as e:  # pragma: no cover
                log.warning("hf_secret_failed", error=str(e))

    # -- relation building -------------------------------------------------
    def read_expr(self, uris: list[str], fmt: str, filename: bool = True) -> str:
        """A DuckDB table expression reading the given uris."""
        flist = _sql_list(uris)
        if fmt == "parquet":
            return (
                f"read_parquet({flist}, union_by_name=true, "
                f"filename={'true' if filename else 'false'})"
            )
        return (
            f"read_json_auto({flist}, union_by_name=true, format='newline_delimited', "
            f"maximum_object_size={_MAX_OBJECT_SIZE}, ignore_errors=true, "
            f"filename={'true' if filename else 'false'})"
        )

    def relation(self, rs: ResolvedSource, filename: bool = True) -> str:
        return self.read_expr(rs.uris, rs.fmt, filename=filename)

    # -- querying ----------------------------------------------------------
    def query(self, sql: str, params: list | None = None) -> tuple[list[str], list[tuple]]:
        cur = self.con.execute(sql, params or [])
        cols = [d[0] for d in cur.description] if cur.description else []
        return cols, cur.fetchall()

    def columns(self, rs: ResolvedSource) -> list[str]:
        """Top-level column names of the relation (schema, no data scan of note)."""
        cols, _ = self.query(f"SELECT * FROM {self.relation(rs)} LIMIT 0")
        return cols

    def expr_ok(self, rs: ResolvedSource, expr: str) -> bool:
        """True if `expr` can be selected from the relation (probe for a field path)."""
        try:
            self.query(f"SELECT {expr} FROM {self.relation(rs)} LIMIT 1")
            return True
        except Exception:
            return False

    def sample_rows(self, rs: ResolvedSource, total: int) -> list[dict[str, Any]]:
        """Sample up to `total` rows, spread across files so every shard contributes."""
        files = rs.uris
        per_file = max(1, total // max(1, len(files)))
        rows: list[dict[str, Any]] = []
        for uri in files:
            if len(rows) >= total:
                break
            expr = self.read_expr([uri], rs.fmt, filename=False)
            try:
                cols, data = self.query(f"SELECT * FROM {expr} LIMIT {per_file}")
            except Exception as e:
                log.warning("sample_file_failed", uri=uri, error=str(e)[:200])
                continue
            for r in data:
                rows.append(dict(zip(cols, r)))
        return rows[:total]

    def close(self) -> None:
        self.con.close()
