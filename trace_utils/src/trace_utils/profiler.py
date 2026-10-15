"""Field profiler — produces the "discovered shape" of a dataset.

Samples rows via the engine, flattens nested JSON (dicts, lists, and JSON-encoded
strings) into dotted field paths, and reports per-path type / coverage% / sample values.
This is the concrete answer to "how would I know what to search for": the paths it lists
are exactly what `--where` and `--in` can target.
"""
from __future__ import annotations

import datetime as _dt
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator

from .engine import Engine
from .logging import get_logger
from .sources import ResolvedSource

log = get_logger(__name__)

_MAX_SAMPLE_VALUES = 12
_MAX_LIST_ELEMS = 3  # how many list elements to descend into for path discovery
_MAX_STR_PARSE = 200_000  # only try json.loads on strings under this length


def _maybe_json(value: str) -> Any:
    s = value.strip()
    if s[:1] in ("{", "[") and len(value) <= _MAX_STR_PARSE:
        try:
            return json.loads(s)
        except (ValueError, TypeError):
            return value
    return value


def flatten(obj: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Yield (dotted_path, scalar_value) pairs. Lists use a ``[]`` path segment."""
    if isinstance(obj, str):
        parsed = _maybe_json(obj)
        if parsed is obj:
            yield prefix, obj
            return
        obj = parsed
    if isinstance(obj, dict):
        if not obj:
            yield prefix, {}
            return
        for k, v in obj.items():
            child = f"{prefix}.{k}" if prefix else str(k)
            yield from flatten(v, child)
    elif isinstance(obj, (list, tuple)):
        if not obj:
            yield f"{prefix}[]", []
            return
        for elem in list(obj)[:_MAX_LIST_ELEMS]:
            yield from flatten(elem, f"{prefix}[]")
    else:
        yield prefix, obj


def _typename(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, (dict,)):
        return "object"
    if isinstance(v, (list, tuple)):
        return "list"
    if isinstance(v, (_dt.datetime, _dt.date)):
        return "datetime"
    if v is None:
        return "null"
    return "str"


@dataclass
class FieldStat:
    path: str
    types: set[str] = field(default_factory=set)
    present: int = 0
    values: list[Any] = field(default_factory=list)
    numeric_min: float | None = None
    numeric_max: float | None = None
    _seen: set = field(default_factory=set, repr=False)

    def observe(self, value: Any) -> None:
        self.present += 1
        t = _typename(value)
        self.types.add(t)
        if t in ("int", "float") and value is not None:
            self.numeric_min = value if self.numeric_min is None else min(self.numeric_min, value)
            self.numeric_max = value if self.numeric_max is None else max(self.numeric_max, value)
        elif value not in (None, {}, []):
            key = str(value)[:120]
            if key not in self._seen and len(self.values) < _MAX_SAMPLE_VALUES:
                self._seen.add(key)
                self.values.append(key)

    def coverage(self, total: int) -> float:
        return self.present / total if total else 0.0

    def sample_repr(self) -> str:
        if self.numeric_min is not None:
            lo, hi = self.numeric_min, self.numeric_max
            return f"{lo} … {hi}" if lo != hi else f"{lo}"
        return ", ".join(self.values) if self.values else "—"

    def type_repr(self) -> str:
        return "/".join(sorted(t for t in self.types if t != "null")) or "null"


@dataclass
class Profile:
    total_rows: int
    fields: list[FieldStat]


def profile(engine: Engine, rs: ResolvedSource, sample: int = 200) -> Profile:
    """Build a :class:`Profile` from up to `sample` rows across the dataset's files."""
    rows = engine.sample_rows(rs, sample)
    stats: dict[str, FieldStat] = defaultdict(lambda: FieldStat(path=""))
    for row in rows:
        # Collect each path's values within this row. DuckDB pads missing STRUCT
        # fields with NULL, so coverage counts only rows where a path is non-null —
        # a field that is null everywhere isn't something you can search on.
        collected: dict[str, list[Any]] = defaultdict(list)
        for path, value in flatten(row):
            collected[path].append(value)
        for path, values in collected.items():
            non_null = [v for v in values if v is not None]
            if not non_null:
                continue
            st = stats[path]
            st.path = path
            st.observe(non_null[0])  # observe once per row (coverage = fraction of rows)
    total = len(rows)
    ordered = sorted(stats.values(), key=lambda s: (-s.coverage(total), s.path))
    log.info("profiled", n_rows=total, n_fields=len(ordered), fmt=rs.fmt)
    return Profile(total_rows=total, fields=ordered)
