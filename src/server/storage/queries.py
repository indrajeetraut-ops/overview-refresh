"""Named-query catalog: config/queries/*.yaml compiled to text() at load (storage.md §4)."""
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

from .errors import QueryConfigError
from .log import get_logger


class QueryCatalog:
    def __init__(self, queries: dict[tuple[str, str], TextClause]) -> None:
        self._q = queries

    @classmethod
    def load(cls, queries_dir: str | Path) -> "QueryCatalog":
        d = Path(queries_dir)
        out: dict[tuple[str, str], TextClause] = {}
        file_count = 0
        for f in sorted(d.glob("*.yaml")):
            file_count += 1
            file_key = f.stem
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                raise QueryConfigError(f"{f}: invalid YAML: {e}") from e
            if not isinstance(data, dict):
                raise QueryConfigError(f"{f}: expected a mapping of name -> statement, got {type(data).__name__}")
            for name, sql in data.items():
                if not isinstance(sql, str) or not sql.strip():
                    raise QueryConfigError(f"{f}:{name}: empty or non-string statement")
                key = (file_key, name)
                if key in out:
                    raise QueryConfigError(f"duplicate query {key}")
                out[key] = text(sql)
        get_logger().info("queries_loaded", files=file_count, query_count=len(out))
        return cls(out)

    def get(self, file_key: str, name: str) -> TextClause:
        try:
            return self._q[(file_key, name)]
        except KeyError:
            avail = ", ".join(f"{k[0]}:{k[1]}" for k in sorted(self._q))
            raise KeyError(f"query {(file_key, name)!r} not found; available: {avail}") from None
