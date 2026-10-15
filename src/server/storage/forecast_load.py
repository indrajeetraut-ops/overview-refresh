"""Load a dashboard forecast export (`forecast_export.json`, schema `dashboard-forecast/1.x`)
into `forecast_runs` / `forecast_scope_series` (docs/plan/2026-10-05-forecast-dashboard-integration.md
§3.4, §3.5, T5).

The file is produced by the forecast package's dashboard export. Mainline never imports
`forecast.*` (the dependency direction stays one-way, like `synthetic_load`): this module reads
the JSON as plain dicts and re-implements the structural checks it needs. It does not re-verify
every number -- the exporter's own validator did that -- but it refuses a document whose shape
the readers would trip over, before the database is touched, so a bad file never writes a
partial run.

Row split (documented in docs/modules/storage.md §3.8):

* `forecast_runs`: one row. `run_id` is the first 16 hex characters of the sha256 of the file's
  bytes (the same file always gives the same id; any change to the bytes gives another).
  `origin`, `data_through` and `generated_at` are columns. `meta` holds `schema` (the version
  string), `timezone`, `band`, `quantile_grid`, `dates`, `periods`, `presets`, and `scopes`
  (`[{scope_type, scope_id, name}]` in export order -- the display names exist nowhere else),
  plus every extra top-level key of a newer minor version under its own name (must-ignore:
  extra keys are kept, not rejected).
* `forecast_scope_series`: one row per scope x preset. `status` is a column; `payload` is the
  series entry without `status` -- `{daily, periods}` for a scope with numbers, `{}` for an
  `incomplete` one, and any extra key of a newer minor is carried along.

One run at a time: `replace_run` deletes both tables' rows and inserts the new run inside the
caller's transaction (it never commits), so a failure leaves the previous run intact.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.orm import Session as DbSession

from .config import StorageConfig
from .dbm import operations as dbm_operations
from .engine import dispose_engine, get_engine, session_scope
from .queries import QueryCatalog

_log = structlog.get_logger(module="storage.forecast_load")

SCHEMA_PREFIX = "dashboard-forecast/"
SUPPORTED_MAJOR = 1

_SCOPE_TYPES = ("org", "team", "repo")
_STATUSES = ("complete", "conditional", "inactive", "incomplete")
_KNOWN_TOP_LEVEL = frozenset({
    "schema", "generated_at", "timezone", "origin", "data_through", "band", "quantile_grid",
    "dates", "periods", "presets", "scopes"})
_SCHEMA_RE = re.compile(r"^dashboard-forecast/(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_BATCH_SIZE = 50


class ForecastLoadError(Exception):
    """A bad export file, an unsupported version, a dataset mismatch or the `_synth` guard
    (CLI exit 2). Database failures are not wrapped; they propagate (exit 1)."""


@dataclass(frozen=True)
class ScopeSeriesRow:
    scope_type: str
    scope_id: str
    preset_id: str
    status: str
    payload: dict


@dataclass(frozen=True)
class ParsedExport:
    run_id: str
    origin: date
    data_through: date
    generated_at: datetime            # timezone-aware, UTC
    meta: dict
    series: list[ScopeSeriesRow]
    scope_count: int
    preset_count: int


@dataclass(frozen=True)
class ForecastLoadResult:
    run_id: str
    scope_series_rows: int
    scope_count: int
    preset_count: int
    origin: date
    data_through: date
    duration_ms: float


# --------------------------------------------------------------------------------------------
# Parsing and validation (pure: no database)
# --------------------------------------------------------------------------------------------

def _fail(message: str) -> ForecastLoadError:
    return ForecastLoadError(message)


def _reject_constant(name: str):
    raise ValueError(f"{name} is not valid JSON for this export")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _req(doc: dict, key: str, where: str = "export") -> Any:
    if key not in doc:
        raise _fail(f"{where}: missing required key {key!r}")
    return doc[key]


def _str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(f"{what}: expected a non-empty string, got {value!r}")
    return value


def _obj(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise _fail(f"{what}: expected an object, got {type(value).__name__}")
    return value


def _list(value: Any, what: str) -> list:
    if not isinstance(value, list):
        raise _fail(f"{what}: expected a list, got {type(value).__name__}")
    return value


def _date(value: Any, what: str) -> date:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise _fail(f"{what}: expected an ISO date YYYY-MM-DD, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _fail(f"{what}: not a calendar date: {value!r}") from None


def _timestamp(value: Any, what: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise _fail(f"{what}: expected an ISO-8601 timestamp, got {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _fail(f"{what}: not an ISO-8601 timestamp: {value!r}") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _fail(f"{what}: the timestamp must carry a UTC offset (e.g. a 'Z' suffix), "
                    f"got {value!r}")
    try:
        return parsed.astimezone(timezone.utc)
    except OverflowError:
        raise _fail(f"{what}: timestamp out of range: {value!r}") from None


def _check_values(doc: Any) -> None:
    """Refuse what JSON allows but the database or the JSON writer cannot hold, so it fails
    here with a clear message instead of part-way through a load: NUL characters and lone
    surrogates in any string or key, and non-finite numbers (`1e999`). Iterative, so a deeply
    nested document cannot exhaust the stack."""
    stack: list[Any] = [doc]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                _check_text(key)
                stack.append(item)
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str):
            _check_text(value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise _fail("export contains a number outside the representable range")


def _check_text(text: str) -> None:
    if "\x00" in text:
        raise _fail("export contains a NUL character in a string, which the database cannot store")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise _fail("export contains a string with an unpaired surrogate (invalid Unicode)") from None


def _parse_schema(doc: dict) -> str:
    value = doc.get("schema")
    if not isinstance(value, str):
        raise _fail(f"export: 'schema' must be a string like 'dashboard-forecast/1.0', "
                    f"got {value!r}")
    match = _SCHEMA_RE.match(value)
    if not match:
        raise _fail(f"export: malformed schema {value!r}; expected 'dashboard-forecast/<major>.<minor>'")
    if int(match.group(1)) != SUPPORTED_MAJOR:
        raise _fail(f"export: unsupported schema version {value!r}; this loader reads "
                    f"major version {SUPPORTED_MAJOR} ({SCHEMA_PREFIX}{SUPPORTED_MAJOR}.x) only")
    return value


def _array(value: Any, what: str, length: int | None = None) -> list:
    items = _list(value, what)
    if length is not None and len(items) != length:
        raise _fail(f"{what}: expected {length} entries, got {len(items)}")
    return items


def _parse_grid(doc: dict) -> list[int]:
    grid = _list(_req(doc, "quantile_grid"), "quantile_grid")
    if len(grid) < 2 or not all(_is_int(q) for q in grid):
        raise _fail("quantile_grid: expected a list of at least two integers")
    if grid[0] != 0 or grid[-1] != 100:
        raise _fail(f"quantile_grid: must run from 0 to 100, got {grid[0]}..{grid[-1]}")
    if any(b <= a for a, b in zip(grid, grid[1:])):
        raise _fail("quantile_grid: must be strictly increasing")
    return grid


def _parse_band(doc: dict, grid: list[int]) -> dict:
    band = _obj(_req(doc, "band"), "band")
    low, high = _req(band, "low", "band"), _req(band, "high", "band")
    if not _is_int(low) or not _is_int(high):
        raise _fail(f"band: 'low' and 'high' must be integers, got {low!r} and {high!r}")
    for name, value in (("low", low), ("high", high)):
        if value not in grid:
            raise _fail(f"band: {name} {value} is not an entry of quantile_grid")
    if low >= high:
        raise _fail(f"band: low ({low}) must be below high ({high})")
    return band


def _parse_dates(doc: dict) -> list[str]:
    raw = _list(_req(doc, "dates"), "dates")
    if not raw:
        raise _fail("dates: must not be empty")
    parsed = [_date(item, f"dates[{i}]") for i, item in enumerate(raw)]
    for i in range(1, len(parsed)):
        if (parsed[i] - parsed[i - 1]).days != 1:
            raise _fail(f"dates: not consecutive at index {i} ({raw[i - 1]} then {raw[i]})")
    return raw


def _parse_periods(doc: dict, date_index: dict[str, int]) -> list[dict]:
    periods = _list(_req(doc, "periods"), "periods")
    if not periods:
        raise _fail("periods: must not be empty")
    for i, period in enumerate(periods):
        where = f"periods[{i}]"
        period = _obj(period, where)
        start = _date(_req(period, "start", where), f"{where}.start")
        end = _date(_req(period, "end", where), f"{where}.end")
        from_ = _date(_req(period, "from", where), f"{where}.from")
        if not (start <= from_ <= end):
            raise _fail(f"{where}: 'from' ({from_}) must lie between start ({start}) and end ({end})")
        for key in ("from", "end"):
            if period[key] not in date_index:
                raise _fail(f"{where}.{key}: {period[key]!r} is not one of the export's dates")
    return periods


def _parse_presets(doc: dict) -> list[dict]:
    presets = _list(_req(doc, "presets"), "presets")
    if not presets:
        raise _fail("presets: must not be empty")
    seen: set[str] = set()
    for i, preset in enumerate(presets):
        where = f"presets[{i}]"
        preset = _obj(preset, where)
        pid = _str(_req(preset, "id", where), f"{where}.id")
        if pid in seen:
            raise _fail(f"{where}: duplicate preset id {pid!r}")
        seen.add(pid)
        if not isinstance(_req(preset, "label", where), str):
            raise _fail(f"{where}.label: expected a string")
    return presets


def _parse_series_entry(entry: Any, where: str, *, n_dates: int, periods: list[dict],
                        date_index: dict[str, int], n_grid: int) -> tuple[str, dict]:
    entry = _obj(entry, where)
    status = _req(entry, "status", where)
    if status not in _STATUSES:
        raise _fail(f"{where}.status: unknown status {status!r}; expected one of "
                    f"{', '.join(_STATUSES)}")
    payload = {k: v for k, v in entry.items() if k != "status"}
    if status == "incomplete":
        return status, payload

    daily = _obj(_req(entry, "daily", where), f"{where}.daily")
    for key in ("expected", "low", "high"):
        _array(_req(daily, key, f"{where}.daily"), f"{where}.daily.{key}", n_dates)
    series_periods = _list(_req(entry, "periods", where), f"{where}.periods")
    if len(series_periods) != len(periods):
        raise _fail(f"{where}.periods: expected {len(periods)} entries (one per export period), "
                    f"got {len(series_periods)}")
    for i, (period, item) in enumerate(zip(periods, series_periods)):
        pwhere = f"{where}.periods[{i}]"
        item = _obj(item, pwhere)
        length = date_index[period["end"]] - date_index[period["from"]] + 1
        if length < 1:
            raise _fail(f"periods[{i}]: 'end' lies before 'from' on the export's date axis")
        cumulative = _obj(_req(item, "cumulative", pwhere), f"{pwhere}.cumulative")
        _array(_req(cumulative, "expected", f"{pwhere}.cumulative"),
                  f"{pwhere}.cumulative.expected", length)
        rows = _array(_req(cumulative, "quantiles", f"{pwhere}.cumulative"),
                         f"{pwhere}.cumulative.quantiles", length)
        for r, row in enumerate(rows):
            _array(row, f"{pwhere}.cumulative.quantiles[{r}]", n_grid)
        total = _obj(_req(item, "total", pwhere), f"{pwhere}.total")
        _array(_req(total, "quantiles", f"{pwhere}.total"), f"{pwhere}.total.quantiles", n_grid)
    return status, payload


def parse_export(path: str | Path) -> ParsedExport:
    """Read, validate and split an export file. Pure: no database. Raises `ForecastLoadError`
    for anything wrong with the file."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _fail(f"cannot read forecast export {path}: {exc}") from exc
    try:
        doc = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeError, ValueError) as exc:
        raise _fail(f"forecast export {path} is not valid JSON: {exc}") from exc
    except RecursionError:
        raise _fail(f"forecast export {path} is nested too deeply") from None
    if not isinstance(doc, dict):
        raise _fail(f"forecast export {path}: expected a JSON object at the top level, "
                    f"got {type(doc).__name__}")
    _check_values(doc)

    schema = _parse_schema(doc)
    generated_at = _timestamp(_req(doc, "generated_at"), "generated_at")
    tz_name = _str(_req(doc, "timezone"), "timezone")
    origin = _date(_req(doc, "origin"), "origin")
    data_through = _date(_req(doc, "data_through"), "data_through")
    if origin > data_through:
        raise _fail(f"origin ({origin}) must not be after data_through ({data_through})")
    grid = _parse_grid(doc)
    band = _parse_band(doc, grid)
    dates = _parse_dates(doc)
    date_index = {d: i for i, d in enumerate(dates)}
    periods = _parse_periods(doc, date_index)
    presets = _parse_presets(doc)
    preset_ids = [p["id"] for p in presets]

    scopes = _list(_req(doc, "scopes"), "scopes")
    if not scopes:
        raise _fail("scopes: must not be empty")
    seen: set[tuple[str, str]] = set()
    rows: list[ScopeSeriesRow] = []
    scope_index: list[dict] = []
    org_scopes = 0
    for i, scope in enumerate(scopes):
        where = f"scopes[{i}]"
        scope = _obj(scope, where)
        scope_type = _req(scope, "scope_type", where)
        if scope_type not in _SCOPE_TYPES:
            raise _fail(f"{where}.scope_type: unknown scope type {scope_type!r}; expected one of "
                        f"{', '.join(_SCOPE_TYPES)}")
        scope_id = _str(_req(scope, "scope_id", where), f"{where}.scope_id")
        name = _str(_req(scope, "name", where), f"{where}.name")
        if (scope_type, scope_id) in seen:
            raise _fail(f"{where}: duplicate scope ({scope_type}, {scope_id!r})")
        seen.add((scope_type, scope_id))
        if scope_type == "org":
            org_scopes += 1
            if org_scopes > 1:
                raise _fail(f"{where}: more than one 'org' scope")
        series = _obj(_req(scope, "series", where), f"{where}.series")
        if set(series) != set(preset_ids):
            missing = sorted(set(preset_ids) - set(series))
            extra = sorted(set(series) - set(preset_ids))
            raise _fail(f"{where}.series: keys must be exactly the preset ids "
                        f"(missing {missing}, unexpected {extra})")
        scope_index.append({"scope_type": scope_type, "scope_id": scope_id, "name": name})
        for pid in preset_ids:
            status, payload = _parse_series_entry(
                series[pid], f"{where}.series[{pid!r}]", n_dates=len(dates), periods=periods,
                date_index=date_index, n_grid=len(grid))
            rows.append(ScopeSeriesRow(scope_type, scope_id, pid, status, payload))

    meta: dict[str, Any] = {
        "schema": schema, "timezone": tz_name, "band": band, "quantile_grid": grid,
        "dates": dates, "periods": periods, "presets": presets, "scopes": scope_index}
    for key, value in doc.items():
        if key not in _KNOWN_TOP_LEVEL:
            meta[key] = value

    return ParsedExport(
        run_id=hashlib.sha256(raw).hexdigest()[:16], origin=origin, data_through=data_through,
        generated_at=generated_at, meta=meta, series=rows, scope_count=len(scope_index),
        preset_count=len(preset_ids))


def check_against_dataset(parsed: ParsedExport, *, org_id: str | None,
                          team_ids: set[str], repo_ids: set[str]) -> None:
    """Refuse an export that belongs to another org: the `org` scope id must equal the
    dataset's org id, every `team` scope id must be a directory team and every `repo` scope id
    a repository of the dataset. Raises `ForecastLoadError` naming the unknown ids."""
    unknown: list[str] = []
    for scope in parsed.meta["scopes"]:
        stype, sid = scope["scope_type"], scope["scope_id"]
        if stype == "org":
            if sid != org_id:
                unknown.append(f"org {sid!r} (the dataset's org is {org_id!r})")
        elif stype == "team":
            if sid not in team_ids:
                unknown.append(f"team {sid!r}")
        elif stype == "repo":
            if sid not in repo_ids:
                unknown.append(f"repo {sid!r}")
    if unknown:
        raise _fail("forecast export does not belong to this dataset; unknown scope ids: "
                    + ", ".join(unknown))


# --------------------------------------------------------------------------------------------
# Database write
# --------------------------------------------------------------------------------------------

def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def replace_run(session: DbSession, catalog: QueryCatalog, parsed: ParsedExport, *,
                batch_size: int = _BATCH_SIZE, log: Any = None) -> int:
    """Inside the caller's transaction: lock, delete every row of both tables, then insert the
    run and its scope x preset rows (in batches). Returns the number of series rows inserted.
    Never commits -- the caller owns the transaction, so any failure rolls the whole replace
    back and the previous run stays."""
    log = log or _log
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    started = time.monotonic()
    session.execute(catalog.get("forecast", "lock_tables"))
    session.execute(catalog.get("forecast", "delete_series"))
    session.execute(catalog.get("forecast", "delete_runs"))
    session.execute(catalog.get("forecast", "insert_run"), {
        "run_id": parsed.run_id, "origin": parsed.origin, "data_through": parsed.data_through,
        "generated_at": parsed.generated_at, "meta": _dumps(parsed.meta)})
    insert_series = catalog.get("forecast", "insert_series")
    inserted = 0
    for i in range(0, len(parsed.series), batch_size):
        batch = parsed.series[i:i + batch_size]
        session.execute(insert_series, [
            {"run_id": parsed.run_id, "scope_type": r.scope_type, "scope_id": r.scope_id,
             "preset_id": r.preset_id, "status": r.status, "payload": _dumps(r.payload)}
            for r in batch])
        inserted += len(batch)
    log.info("forecast_load.replaced", run_id=parsed.run_id, scope_series_rows=inserted,
             duration_ms=round((time.monotonic() - started) * 1000, 1))
    return inserted


def load_forecast_export(path: str | Path, dsn: str, *, log: Any = None,
                         queries_dir: str = "config/queries") -> ForecastLoadResult:
    """Standalone load of one export into a `*_synth` database. Order: guard (before anything,
    including reading the file), parse (a malformed file never reaches the database), migrate,
    then one transaction doing `replace_run`."""
    log = log or _log
    started = time.monotonic()

    # Lazy import: synthetic_load imports this module, and pulls in far more than the guard.
    from .synthetic_load import SyntheticLoadError, _guard_dsn
    try:
        _guard_dsn(dsn)
    except SyntheticLoadError as exc:
        raise ForecastLoadError(str(exc)) from exc

    parsed = parse_export(path)
    log.info("forecast_load.parsed", run_id=parsed.run_id, scopes=parsed.scope_count,
             presets=parsed.preset_count, scope_series_rows=len(parsed.series),
             origin=parsed.origin.isoformat(), data_through=parsed.data_through.isoformat())

    cfg = StorageConfig(dsn=dsn, queries_dir=queries_dir)
    catalog = QueryCatalog.load(queries_dir)
    dbm_operations.migrate(cfg)
    get_engine(cfg)
    try:
        with session_scope() as s:
            rows = replace_run(s, catalog, parsed, log=log)
    finally:
        dispose_engine()

    result = ForecastLoadResult(
        run_id=parsed.run_id, scope_series_rows=rows, scope_count=parsed.scope_count,
        preset_count=parsed.preset_count, origin=parsed.origin, data_through=parsed.data_through,
        duration_ms=(time.monotonic() - started) * 1000)
    log.info("forecast_load.completed", run_id=result.run_id, scope_series_rows=rows,
             duration_ms=round(result.duration_ms, 1))
    return result
