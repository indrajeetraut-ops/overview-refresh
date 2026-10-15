"""Read side of the precomputed dashboard forecast (docs/plan/2026-10-05-forecast-dashboard-
integration.md §3.5, T6): the one loaded run in `forecast_runs` / `forecast_scope_series`
(written by `server.storage.forecast_load`, documented in docs/modules/storage.md §3.8).

Framework-free. The loader checks the document's *shape* only; it does not check that
`meta.timezone` is a real zone, that arrays hold numbers, or that every optional key is there. So
every reader here is strict: anything missing, mistyped or of the wrong length raises
`ForecastUnreadable`, which the service turns into "no forecast" plus a
`forecast.run_unreadable` log line. A stored run is data we do not control, and it must never
become a 500.

Mainline never imports `forecast.*`: the run is read as plain dicts and lists.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session as DbSession

from server.storage.queries import QueryCatalog


class ForecastUnreadable(Exception):
    """The stored run (or part of it) cannot be read. `reason` says what was wrong; `run_id`
    is set once the run row has been found."""

    def __init__(self, reason: str, run_id: str | None = None) -> None:
        self.reason = reason
        self.run_id = run_id
        super().__init__(reason)


#: Longest stretch of actual spend a run may ask for (periods[0].start - 28 days .. data_through).
#: A real run covers about 60 days; a stored `data_through` far from its periods must not turn
#: into a huge fetch and response.
MAX_ACTUAL_DAYS = 400
ACTUAL_LOOKBACK_DAYS = 28


@dataclass(frozen=True)
class PeriodDesc:
    """One calendar period of the run. `i_from` / `i_end` are the positions of `forecast_from`
    and `end` on `StoredRun.dates`: the period's cumulative arrays cover `dates[i_from..i_end]`."""

    start: date
    end: date
    forecast_from: date
    i_from: int
    i_end: int

    @property
    def current(self) -> bool:
        """The period that contains `data_through`: forecasting starts after its first day. In
        the month-end case (data ends on a month's last day) neither period is current."""
        return self.forecast_from > self.start

    @property
    def length(self) -> int:
        return self.i_end - self.i_from + 1


@dataclass(frozen=True)
class StoredRun:
    run_id: str
    origin: date
    data_through: date
    timezone: str
    tz: ZoneInfo
    band_low: int
    band_high: int
    grid: list[int]
    dates: list[date]
    periods: list[PeriodDesc]
    presets: list[dict]
    scopes: list[dict]


# ------------------------------------------------------------------------------ type guards

def _bad(reason: str) -> ForecastUnreadable:
    return ForecastUnreadable(reason)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _dict(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise _bad(f"{what}: expected an object")
    return value


def _list(value: Any, what: str, length: int | None = None) -> list:
    if not isinstance(value, list):
        raise _bad(f"{what}: expected a list")
    if length is not None and len(value) != length:
        raise _bad(f"{what}: expected {length} entries, got {len(value)}")
    return value


def _key(obj: dict, key: str, what: str) -> Any:
    if key not in obj:
        raise _bad(f"{what}: missing key {key!r}")
    return obj[key]


def _int(value: Any, what: str) -> int:
    if not _is_int(value):
        raise _bad(f"{what}: expected an integer, got {value!r}")
    return value


def _str(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise _bad(f"{what}: expected a string, got {value!r}")
    return value


def _ints(value: Any, what: str, length: int | None = None) -> list[int]:
    items = _list(value, what, length)
    for v in items:
        if not _is_int(v):
            raise _bad(f"{what}: expected integers, found {v!r}")
    return items


def _date(value: Any, what: str) -> date:
    try:
        return date.fromisoformat(_str(value, what))
    except ValueError:
        raise _bad(f"{what}: not an ISO date: {value!r}") from None


# ------------------------------------------------------------------------------ the run

def _parse_run(row: Any) -> StoredRun:
    run_id = row.run_id
    try:
        meta = _dict(row.meta, "meta")
        tz_name = _str(_key(meta, "timezone", "meta"), "meta.timezone")
        try:
            tz = ZoneInfo(tz_name)
        except Exception as exc:  # unknown zone, malformed key, OS error: all just "unreadable"
            raise _bad(f"meta.timezone {tz_name!r} is not a usable zone ({type(exc).__name__})") from None

        grid = _ints(_key(meta, "quantile_grid", "meta"), "meta.quantile_grid")
        if len(grid) < 2 or any(b <= a for a, b in zip(grid, grid[1:])):
            raise _bad("meta.quantile_grid: expected at least two strictly increasing integers")
        band = _dict(_key(meta, "band", "meta"), "meta.band")
        band_low = _int(_key(band, "low", "meta.band"), "meta.band.low")
        band_high = _int(_key(band, "high", "meta.band"), "meta.band.high")
        if band_low not in grid or band_high not in grid or band_low >= band_high:
            raise _bad("meta.band: low and high must be grid entries with low < high")

        dates = [_date(d, f"meta.dates[{i}]")
                 for i, d in enumerate(_list(_key(meta, "dates", "meta"), "meta.dates"))]
        if not dates:
            raise _bad("meta.dates: empty")
        index = {d: i for i, d in enumerate(dates)}

        periods = []
        for i, p in enumerate(_list(_key(meta, "periods", "meta"), "meta.periods")):
            where = f"meta.periods[{i}]"
            p = _dict(p, where)
            start = _date(_key(p, "start", where), f"{where}.start")
            end = _date(_key(p, "end", where), f"{where}.end")
            from_ = _date(_key(p, "from", where), f"{where}.from")
            if from_ not in index or end not in index or not (start <= from_ <= end):
                raise _bad(f"{where}: 'from' and 'end' must be run dates with start <= from <= end")
            if index[end] < index[from_]:
                raise _bad(f"{where}: 'end' lies before 'from'")
            periods.append(PeriodDesc(start, end, from_, index[from_], index[end]))
        if not periods:
            raise _bad("meta.periods: empty")

        presets = []
        for i, p in enumerate(_list(_key(meta, "presets", "meta"), "meta.presets")):
            where = f"meta.presets[{i}]"
            p = _dict(p, where)
            group = _key(p, "group", where)
            if group is not None:
                _str(group, f"{where}.group")
            assumptions = _list(_key(p, "assumptions", where), f"{where}.assumptions")
            for a in assumptions:
                _str(a, f"{where}.assumptions[]")
            presets.append({
                "id": _str(_key(p, "id", where), f"{where}.id"),
                "label": _str(_key(p, "label", where), f"{where}.label"),
                "group": group,
                "description": _str(_key(p, "description", where), f"{where}.description"),
                "assumptions": list(assumptions),
            })
        if not presets:
            raise _bad("meta.presets: empty")

        scopes = []
        for i, sc in enumerate(_list(_key(meta, "scopes", "meta"), "meta.scopes")):
            where = f"meta.scopes[{i}]"
            sc = _dict(sc, where)
            scopes.append({
                "scope_type": _str(_key(sc, "scope_type", where), f"{where}.scope_type"),
                "scope_id": _str(_key(sc, "scope_id", where), f"{where}.scope_id"),
                "name": _str(_key(sc, "name", where), f"{where}.name"),
            })

        origin = _date(row.origin, "origin")
        data_through = _date(row.data_through, "data_through")
        if origin > data_through:
            raise _bad(f"origin {origin} is after data_through {data_through}")
        # `data_through` must agree with the periods the run was validated against: the first
        # forecast day of the current period is the day after it; with no current period (the
        # data ends on a month's last day) the first period starts the day after it. This also
        # bounds the window of actual spend the API fetches.
        current = [p for p in periods if p.current]
        if len(current) > 1:
            raise _bad("meta.periods: more than one period is current")
        anchor = current[0].forecast_from if current else periods[0].start
        if data_through != anchor - timedelta(days=1):
            raise _bad(f"data_through {data_through} does not match the periods "
                       f"(expected the day before {anchor})")
        first = periods[0].start - timedelta(days=ACTUAL_LOOKBACK_DAYS)
        if not 0 <= (data_through - first).days < MAX_ACTUAL_DAYS:
            raise _bad(f"the actuals window {first}..{data_through} is outside 0..{MAX_ACTUAL_DAYS} days")
        return StoredRun(
            run_id=run_id, origin=origin, data_through=data_through, timezone=tz_name,
            tz=tz, band_low=band_low, band_high=band_high, grid=grid, dates=dates,
            periods=periods, presets=presets, scopes=scopes)
    except (ValueError, OverflowError, TypeError) as exc:
        # date arithmetic or a conversion on a stored value: unreadable, with the reason
        raise ForecastUnreadable(f"{type(exc).__name__}: {exc}", run_id) from None
    except ForecastUnreadable as exc:
        exc.run_id = run_id
        raise


def load_run(s: DbSession, catalog: QueryCatalog) -> StoredRun | None:
    """The run to serve (greatest `loaded_at`, ties the greatest `run_id`); `None` when nothing
    is loaded. Raises `ForecastUnreadable` when the run row cannot be read: a damaged `meta`, a
    non-date `origin` / `data_through` (they arrive as text, so a stored `infinity` is just a
    rejected value), `origin` after `data_through`, or a `data_through` that does not match the
    periods (the day before the current period's first forecast day, or before the first period
    when none is current) or puts the actuals window beyond `MAX_ACTUAL_DAYS`.

    Not checked, deliberately: `dates[0] == origin + 1`. The export format lets a newer minor
    version start the date axis elsewhere, and nothing here reads the days before `periods[0]`."""
    row = s.execute(catalog.get("forecast", "latest_run")).first()
    if row is None:
        return None
    return _parse_run(row)


# ------------------------------------------------------------------------------ series

def _check_totals(total: Any, what: str, n_grid: int) -> tuple[int, list[int]]:
    total = _dict(total, what)
    return (_int(_key(total, "expected", what), f"{what}.expected"),
            _ints(_key(total, "quantiles", what), f"{what}.quantiles", n_grid))


def _tokens(value: Any, what: str) -> list[dict]:
    out = []
    for i, t in enumerate(_list(value, what)):
        where = f"{what}[{i}]"
        t = _dict(t, where)
        out.append({"model": _str(_key(t, "model", where), f"{where}.model"),
                    "expected": _int(_key(t, "expected", where), f"{where}.expected"),
                    "p90": _int(_key(t, "p90", where), f"{where}.p90")})
    return out


_STATUSES = ("complete", "conditional", "inactive", "incomplete")


def parse_series(run: StoredRun, preset_id: str, status: Any, payload: Any) -> dict:
    """One scope x preset: `{status, daily, periods}` with every number validated (`daily` is
    `None` and `periods` empty for an `incomplete` scope, which carries none).

    `periods[i]` = `{cumulative_expected, cumulative_quantiles, total_expected, total_quantiles,
    tokens_by_model}`."""
    where = f"series[{preset_id!r}]"
    if status not in _STATUSES:
        raise _bad(f"{where}: unknown status {status!r}")
    if status == "incomplete":
        return {"status": status, "daily": None, "periods": []}
    payload = _dict(payload, f"{where}.payload")
    n_dates, n_grid = len(run.dates), len(run.grid)
    daily = _dict(_key(payload, "daily", where), f"{where}.daily")
    out_daily = {k: _ints(_key(daily, k, f"{where}.daily"), f"{where}.daily.{k}", n_dates)
                 for k in ("expected", "low", "high")}
    stored = _list(_key(payload, "periods", where), f"{where}.periods", len(run.periods))
    periods = []
    for i, (desc, item) in enumerate(zip(run.periods, stored)):
        pw = f"{where}.periods[{i}]"
        item = _dict(item, pw)
        cum = _dict(_key(item, "cumulative", pw), f"{pw}.cumulative")
        c_expected = _ints(_key(cum, "expected", f"{pw}.cumulative"),
                           f"{pw}.cumulative.expected", desc.length)
        rows = _list(_key(cum, "quantiles", f"{pw}.cumulative"),
                     f"{pw}.cumulative.quantiles", desc.length)
        c_quantiles = [_ints(r, f"{pw}.cumulative.quantiles[{k}]", n_grid)
                       for k, r in enumerate(rows)]
        t_expected, t_quantiles = _check_totals(_key(item, "total", pw), f"{pw}.total", n_grid)
        periods.append({
            "cumulative_expected": c_expected, "cumulative_quantiles": c_quantiles,
            "total_expected": t_expected, "total_quantiles": t_quantiles,
            "tokens_by_model": _tokens(_key(item, "tokens_by_model", pw), f"{pw}.tokens_by_model"),
        })
    return {"status": status, "daily": out_daily, "periods": periods}


def load_scope_series(s: DbSession, catalog: QueryCatalog, run: StoredRun, scope_type: str,
                      scope_id: str) -> dict[str, dict]:
    """Every preset's validated series for one scope, keyed by preset id. Raises
    `ForecastUnreadable` for a missing or damaged row."""
    rows = s.execute(catalog.get("forecast", "scope_series"),
                     {"run_id": run.run_id, "scope_type": scope_type, "scope_id": scope_id}).all()
    by_preset = {r.preset_id: r for r in rows}
    out = {}
    try:
        for preset in run.presets:
            row = by_preset.get(preset["id"])
            if row is None:
                raise _bad(f"series[{preset['id']!r}]: no stored row for scope {scope_id!r}")
            out[preset["id"]] = parse_series(run, preset["id"], row.status, row.payload)
    except ForecastUnreadable as exc:
        exc.run_id = run.run_id
        raise
    return out


def load_plan_series(s: DbSession, catalog: QueryCatalog, run: StoredRun) -> dict[tuple[str, str], dict[str, dict]]:
    """The planner's view of every team and repo scope: `{(scope_type, scope_id): {preset_id:
    {status, periods: [{total_expected, total_quantiles, tokens_by_model}]}}}`. Only each
    period's `total` and `tokens_by_model` leave the database (`plan_series`). Raises
    `ForecastUnreadable` when any of them is missing or damaged."""
    rows = s.execute(catalog.get("forecast", "plan_series"), {"run_id": run.run_id}).all()
    n_grid = len(run.grid)
    out: dict[tuple[str, str], dict[str, dict]] = {}
    try:
        for r in rows:
            entry = out.setdefault((r.scope_type, r.scope_id), {}).setdefault(
                r.preset_id, {"status": r.status, "periods": []})
            if r.status not in _STATUSES:
                raise _bad(f"series[{r.preset_id!r}]: unknown status {r.status!r}")
            if r.status == "incomplete":
                continue
            where = f"series[{r.preset_id!r}].periods[{r.ord}]"
            if r.ord is None:
                raise _bad(f"series[{r.preset_id!r}]: no periods stored")
            t_expected, t_quantiles = _check_totals(r.total, f"{where}.total", n_grid)
            entry["periods"].append({
                "total_expected": t_expected, "total_quantiles": t_quantiles,
                "tokens_by_model": _tokens(r.tokens_by_model, f"{where}.tokens_by_model")})
        wanted = [sc for sc in run.scopes if sc["scope_type"] in ("team", "repo")]
        for sc in wanted:
            per_preset = out.get((sc["scope_type"], sc["scope_id"]), {})
            for preset in run.presets:
                entry = per_preset.get(preset["id"])
                if entry is None:
                    raise _bad(f"series[{preset['id']!r}]: no stored row for scope {sc['scope_id']!r}")
                if entry["status"] != "incomplete" and len(entry["periods"]) != len(run.periods):
                    raise _bad(f"series[{preset['id']!r}] of {sc['scope_id']!r}: expected "
                               f"{len(run.periods)} periods, got {len(entry['periods'])}")
    except ForecastUnreadable as exc:
        exc.run_id = run.run_id
        raise
    return out
