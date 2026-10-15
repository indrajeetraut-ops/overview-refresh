"""`forecast_export.json` (schema `dashboard-forecast/1.0`): the dashboard's view of a
completed dashboard run (plan §3.4 and the additions of §3.8).

`build_export` reads the forecast run and every preset's cost run, only through
`forecast.forecasting.load_run` and `forecast.cost.load_cost_run`, and builds the document
scope by scope, preset by preset: one scope's sample arrays are in memory at a time (the
cost runs are opened once up front and are memory-mapped). `validate_export` checks a
document on its own, independently of the builder; `build_export` calls it before
returning, and `write_export` writes canonical JSON atomically.

Numbers: money is an integer count of micro-dollars (`int(rint(usd * 1e6))`, computed in
float64 and rounded once, at the end), tokens are integers, and the document contains no
floats. Every percentile is `numpy.quantile` with its default (linear) method.

Per scope and preset (the cost run's USD paths are `[date, sample]` arrays; a scope's path
is the per-sample sum of its series' paths, `CostRun.scope_paths`, and percentiles are
taken after summing):

* `daily`: per date, the mean and the band percentiles over samples, for every date.
* `periods[i].cumulative`: per sample, the running sum over the period's dates from its
  `from` date, then for each date through `end` the mean and the 21 grid percentiles.
* `periods[i].total`: the last cumulative entry (taken from it, so they are equal).
* `periods[i].tokens_by_model`: the USD path of each series divided by that series'
  per-date effective rate, summed over `from`..`end` per sample (mean and p90). The rate is
  `sum(usd) / sum(tokens)` over the agent rows and token types of the preset's
  `daily_expected.jsonl`. For the current period this covers the remaining days only.

`periods[i].from` is `data_through + 1` when that date lies inside the period, else the
period's `start`. When `data_through` is the last day of a month both periods are future
months and `from == start` for both: a period is the current one exactly when `from > start`.

Errors: everything is an `ExecutionError` with `stage="export"` (an unreadable run, runs
that disagree, a number that cannot be exported, a document that fails `validate_export`,
a file that cannot be written), except a `now` that is not timezone-aware (`InputError`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from typing import Callable, Sequence

import numpy as np

from common.msging.log import get_logger

from forecast.cost import load_cost_run
from forecast.cost.errors import ExecutionError as CostExecutionError
from forecast.cost.errors import InputError as CostInputError
from forecast.forecasting import load_run
from forecast.forecasting.errors import ExecutionError as ForecastExecutionError
from forecast.forecasting.errors import InputError as ForecastInputError

from .errors import ExecutionError, InputError
from .runner import DashboardRunResult
from .scopes import DashboardScope

SCHEMA = "dashboard-forecast/1.0"
EXPORT_FILENAME = "forecast_export.json"
STAGE = "export"

#: Percentiles stored for every total and every cumulative date.
QUANTILE_GRID = tuple(range(0, 101, 5))
#: The band shown on charts; both must be entries of `QUANTILE_GRID`.
BAND_LOW = 10
BAND_HIGH = 90
#: Percentile stored for tokens per model.
TOKENS_PERCENTILE = 90

STATUSES = ("complete", "conditional", "inactive", "incomplete")
SCOPE_TYPES = ("org", "team", "repo")

_MICROS = 1_000_000
_READ_ERRORS = (CostInputError, CostExecutionError, ForecastInputError, ForecastExecutionError)
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _fail(message: str) -> ExecutionError:
    return ExecutionError(message, stage=STAGE)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _int(value, where: str, *, non_negative: bool = True) -> int:
    if not _is_int(value):
        raise _fail(f"export invalid: {where} must be an integer, got {value!r}")
    if non_negative and value < 0:
        raise _fail(f"export invalid: {where} must not be negative, got {value}")
    return value


def _int_list(value, where: str, length: int | None = None) -> list[int]:
    if not isinstance(value, list):
        raise _fail(f"export invalid: {where} must be a list")
    if length is not None and len(value) != length:
        raise _fail(f"export invalid: {where} has {len(value)} entries, expected {length}")
    for position, item in enumerate(value):
        _int(item, f"{where}[{position}]")
    return value


def _non_decreasing(values: Sequence[int], where: str) -> None:
    for position in range(1, len(values)):
        if values[position] < values[position - 1]:
            raise _fail(f"export invalid: {where} decreases at position {position}")


def _keys(value, expected: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        raise _fail(f"export invalid: {where} must be an object")
    if set(value) != expected:
        raise _fail(
            f"export invalid: {where} has keys {sorted(value)}, expected {sorted(expected)}"
        )
    return value


def _iso_date(value, where: str) -> date:
    try:
        if not isinstance(value, str):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise _fail(f"export invalid: {where} must be an ISO date, got {value!r}") from None


def _no_floats(value, where: str = "document") -> None:
    if isinstance(value, float):
        raise _fail(f"export invalid: {where} is a float ({value!r})")
    if isinstance(value, dict):
        for key, item in value.items():
            _no_floats(item, f"{where}.{key}")
    elif isinstance(value, list):
        for position, item in enumerate(value):
            _no_floats(item, f"{where}[{position}]")


def validate_export(doc: dict) -> None:
    """Check a whole export document. Raises `ExecutionError` (stage `export`) naming the
    first problem; returns `None` for a sound document. Independent of `build_export`."""
    top = {
        "schema", "generated_at", "timezone", "origin", "data_through", "band",
        "quantile_grid", "dates", "periods", "presets", "scopes",
    }
    _keys(doc, top, "document")
    _no_floats(doc)
    if doc["schema"] != SCHEMA:
        raise _fail(f"export invalid: schema must be {SCHEMA!r}, got {doc['schema']!r}")
    if not isinstance(doc["generated_at"], str) or not _TIMESTAMP.fullmatch(doc["generated_at"]):
        raise _fail(f"export invalid: generated_at must be UTC seconds, got {doc['generated_at']!r}")
    if not isinstance(doc["timezone"], str) or not doc["timezone"]:
        raise _fail("export invalid: timezone must be a non-empty string")
    origin = _iso_date(doc["origin"], "origin")
    data_through = _iso_date(doc["data_through"], "data_through")
    if origin > data_through:
        raise _fail("export invalid: origin is after data_through")

    grid = doc["quantile_grid"]
    _int_list(grid, "quantile_grid")
    if len(grid) < 2 or grid[0] != 0 or grid[-1] != 100 or any(b <= a for a, b in zip(grid, grid[1:])):
        raise _fail("export invalid: quantile_grid must increase strictly from 0 to 100")
    band = _keys(doc["band"], {"low", "high"}, "band")
    for end in ("low", "high"):
        if not _is_int(band[end]) or band[end] not in grid:
            raise _fail(f"export invalid: band.{end} must be an entry of quantile_grid")
    if band["low"] >= band["high"]:
        raise _fail("export invalid: band.low must be below band.high")
    width = len(grid)

    dates = doc["dates"]
    if not isinstance(dates, list) or not dates:
        raise _fail("export invalid: dates must be a non-empty list")
    parsed = [_iso_date(d, f"dates[{i}]") for i, d in enumerate(dates)]
    for position in range(1, len(parsed)):
        if parsed[position] != parsed[position - 1] + timedelta(days=1):
            raise _fail(f"export invalid: dates are not consecutive days at position {position}")
    position_of = {d: i for i, d in enumerate(dates)}

    periods = doc["periods"]
    if not isinstance(periods, list) or len(periods) != 2:
        raise _fail("export invalid: periods must list the two calendar months")
    spans: list[int] = []  # dates from `from` through `end`, per period
    previous_end = None
    for index, period in enumerate(periods):
        _keys(period, {"start", "end", "from"}, f"periods[{index}]")
        start = _iso_date(period["start"], f"periods[{index}].start")
        end = _iso_date(period["end"], f"periods[{index}].end")
        first = _iso_date(period["from"], f"periods[{index}].from")
        if not start <= first <= end:
            raise _fail(f"export invalid: periods[{index}] needs start <= from <= end")
        if period["from"] not in position_of or period["end"] not in position_of:
            raise _fail(f"export invalid: periods[{index}] lies outside dates")
        if previous_end is not None and start <= previous_end:
            raise _fail("export invalid: periods overlap or are out of order")
        previous_end = end
        spans.append(position_of[period["end"]] - position_of[period["from"]] + 1)

    presets = doc["presets"]
    if not isinstance(presets, list) or not presets:
        raise _fail("export invalid: presets must be a non-empty list")
    preset_ids: list[str] = []
    for index, preset in enumerate(presets):
        _keys(preset, {"id", "label", "group", "description", "assumptions"}, f"presets[{index}]")
        for key in ("id", "label", "description"):
            if not isinstance(preset[key], str) or not preset[key]:
                raise _fail(f"export invalid: presets[{index}].{key} must be a non-empty string")
        if preset["group"] is not None and (not isinstance(preset["group"], str) or not preset["group"]):
            raise _fail(f"export invalid: presets[{index}].group must be null or a non-empty string")
        assumptions = preset["assumptions"]
        if (
            not isinstance(assumptions, list)
            or any(not isinstance(a, str) or not a for a in assumptions)
            or assumptions != sorted(set(assumptions))
        ):
            raise _fail(f"export invalid: presets[{index}].assumptions must be sorted unique strings")
        preset_ids.append(preset["id"])
    if len(set(preset_ids)) != len(preset_ids):
        raise _fail("export invalid: preset ids are not unique")

    scopes = doc["scopes"]
    if not isinstance(scopes, list) or not scopes:
        raise _fail("export invalid: scopes must be a non-empty list")
    seen: set[tuple[str, str]] = set()
    for index, scope in enumerate(scopes):
        where = f"scopes[{index}]"
        _keys(scope, {"scope_type", "scope_id", "name", "series"}, where)
        if scope["scope_type"] not in SCOPE_TYPES:
            raise _fail(f"export invalid: {where}.scope_type must be one of {SCOPE_TYPES}")
        for key in ("scope_id", "name"):
            if not isinstance(scope[key], str) or not scope[key]:
                raise _fail(f"export invalid: {where}.{key} must be a non-empty string")
        identity = (scope["scope_type"], scope["scope_id"])
        if identity in seen:
            raise _fail(f"export invalid: duplicate scope {identity}")
        seen.add(identity)
        series = scope["series"]
        if not isinstance(series, dict) or set(series) != set(preset_ids):
            raise _fail(
                f"export invalid: {where}.series keys {sorted(series) if isinstance(series, dict) else series!r} "
                f"must equal the preset ids {sorted(preset_ids)}"
            )
        for preset_id in preset_ids:
            _validate_series(series[preset_id], f"{where}.series[{preset_id!r}]", len(dates), spans, width, band)


def _validate_series(entry, where: str, date_count: int, spans: list[int], width: int, band: dict) -> None:
    if not isinstance(entry, dict) or "status" not in entry:
        raise _fail(f"export invalid: {where} must be an object with a status")
    status = entry["status"]
    if status not in STATUSES:
        raise _fail(f"export invalid: {where}.status {status!r} is not one of {STATUSES}")
    if status == "incomplete":
        if set(entry) != {"status"}:
            raise _fail(f"export invalid: {where} is incomplete and must carry no numbers")
        return
    _keys(entry, {"status", "daily", "periods"}, where)
    daily = _keys(entry["daily"], {"expected", "low", "high"}, f"{where}.daily")
    for key in ("expected", "low", "high"):
        _int_list(daily[key], f"{where}.daily.{key}", date_count)
    if any(low > high for low, high in zip(daily["low"], daily["high"])):
        raise _fail(f"export invalid: {where}.daily has low above high")
    periods = entry["periods"]
    if not isinstance(periods, list) or len(periods) != len(spans):
        raise _fail(f"export invalid: {where}.periods must have {len(spans)} entries")
    for index, (block, span) in enumerate(zip(periods, spans)):
        here = f"{where}.periods[{index}]"
        _keys(block, {"cumulative", "total", "tokens_by_model"}, here)
        cumulative = _keys(block["cumulative"], {"expected", "quantiles"}, f"{here}.cumulative")
        expected = _int_list(cumulative["expected"], f"{here}.cumulative.expected", span)
        _non_decreasing(expected, f"{here}.cumulative.expected")
        rows = cumulative["quantiles"]
        if not isinstance(rows, list) or len(rows) != span:
            raise _fail(f"export invalid: {here}.cumulative.quantiles must have {span} rows")
        for position, row in enumerate(rows):
            _int_list(row, f"{here}.cumulative.quantiles[{position}]", width)
            _non_decreasing(row, f"{here}.cumulative.quantiles[{position}] across the grid")
            if not row[0] <= expected[position] <= row[-1]:
                raise _fail(f"export invalid: {here}.cumulative.expected[{position}] lies outside its quantiles")
        for column in range(width):
            _non_decreasing([row[column] for row in rows], f"{here}.cumulative quantile {column} across dates")
        total = _keys(block["total"], {"expected", "quantiles"}, f"{here}.total")
        _int(total["expected"], f"{here}.total.expected")
        _int_list(total["quantiles"], f"{here}.total.quantiles", width)
        if total["expected"] != expected[-1] or total["quantiles"] != rows[-1]:
            raise _fail(f"export invalid: {here}.total differs from the last cumulative entry")
        models = block["tokens_by_model"]
        if not isinstance(models, list):
            raise _fail(f"export invalid: {here}.tokens_by_model must be a list")
        names = []
        for position, item in enumerate(models):
            _keys(item, {"model", "expected", "p90"}, f"{here}.tokens_by_model[{position}]")
            if not isinstance(item["model"], str) or not item["model"]:
                raise _fail(f"export invalid: {here}.tokens_by_model[{position}].model must be a string")
            _int(item["expected"], f"{here}.tokens_by_model[{position}].expected")
            _int(item["p90"], f"{here}.tokens_by_model[{position}].p90")
            names.append(item["model"])
        if names != sorted(set(names)):
            raise _fail(f"export invalid: {here}.tokens_by_model must be sorted by unique model id")


# ---------------------------------------------------------------------------
# building
# ---------------------------------------------------------------------------


_INT_LIMIT = float(2**62)  # far beyond any real amount; keeps the int64 cast well defined


def _rounded(values, scale: float, what: str) -> np.ndarray:
    scaled = np.asarray(values, dtype=np.float64) * scale
    if not np.all(np.isfinite(scaled)) or np.any(np.abs(scaled) >= _INT_LIMIT):
        raise _fail(f"{what} is not a finite number within range")
    return np.rint(scaled).astype(np.int64)


def _money(values) -> np.ndarray:
    """USD (float64) -> integer micro-dollars, rounded once."""
    return _rounded(values, _MICROS, "a USD amount")


def _utc_stamp(now: datetime | None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise InputError("now must be a timezone-aware datetime", stage=STAGE)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_timezone(series_dir: Path) -> str:
    path = Path(series_dir) / "manifest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("timezone")
    except (OSError, UnicodeError, ValueError, AttributeError) as exc:
        raise _fail(f"cannot read the series timezone from {path}") from exc
    if not isinstance(value, str) or not value:
        raise _fail(f"series manifest has no timezone: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class _Period:
    start: str
    end: str
    first: str  # the `from` date
    lo: int  # position of `from` in `dates`
    hi: int  # position of `end` in `dates`

    def as_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "from": self.first}


def _calendar_periods(forecast, result: DashboardRunResult, dates: list[str]) -> list[_Period]:
    if forecast.periods is None:
        raise _fail("the forecast run records no periods")
    calendar = [p for p in forecast.periods if p["type"] == "calendar"]
    if len(calendar) != 2:
        raise _fail(f"the forecast run has {len(calendar)} calendar period(s), expected 2")
    if forecast.origin != result.origin:
        raise _fail(
            f"forecast origin {forecast.origin} differs from the run's origin {result.origin}"
        )
    position_of = {d: i for i, d in enumerate(dates)}
    after_data = result.data_through + timedelta(days=1)
    periods = []
    for period in calendar:
        start, end = period["start"], period["end"]
        if end <= result.data_through:
            raise _fail(f"period {start}..{end} ends on or before data_through {result.data_through}")
        first = after_data if start <= after_data <= end else start
        for label, value in (("start", start), ("from", first), ("end", end)):
            if value.isoformat() not in position_of:
                raise _fail(f"period {label} {value} is not one of the cost run's dates")
        periods.append(
            _Period(
                start.isoformat(), end.isoformat(), first.isoformat(),
                position_of[first.isoformat()], position_of[end.isoformat()],
            )
        )
    if periods[0].hi >= position_of[calendar[1]["start"].isoformat()]:
        raise _fail("calendar periods overlap")
    return periods


@dataclass
class _RateTable:
    """Per series key, per date: the effective USD per token and whether it is usable.
    `state`: 0 no tokens that day, 1 usable, 2 tokens but no price (zero rate)."""

    rate: dict[tuple[str, str, str], np.ndarray]
    state: dict[tuple[str, str, str], np.ndarray]


def _rate_table(run, dates: list[str]) -> _RateTable:
    position_of = {d: i for i, d in enumerate(dates)}
    usd: dict[tuple, np.ndarray] = {}
    tokens: dict[tuple, np.ndarray] = {}
    for row in run.daily_expected:
        key = (row["scope_type"], row["scope_name"], row["model"])
        position = position_of.get(row["date"])
        if position is None:
            raise _fail(f"daily_expected row for {row['date']} is outside the cost run's dates")
        if key not in usd:
            usd[key] = np.zeros(len(dates))
            tokens[key] = np.zeros(len(dates))
        usd[key][position] += sum(row["usd"].values())
        tokens[key][position] += sum(row["tokens"].values())
    rate, state = {}, {}
    for key, token_sum in tokens.items():
        has_tokens = token_sum > 0
        has_price = usd[key] > 0
        state[key] = np.where(has_tokens, np.where(has_price, 1, 2), 0).astype(np.int8)
        rate[key] = np.divide(usd[key], token_sum, out=np.zeros(len(dates)), where=has_tokens & has_price)
    return _RateTable(rate, state)


def _series_of(run, scope_entry: dict) -> list[dict]:
    """Index entries of the scope's series that carry a path, sorted by model id."""
    found = [
        e for e in run.index["series"]
        if e["scope_type"] == scope_entry["scope_type"]
        and e["scope_name"] == scope_entry["scope_name"]
        and e["path"]
    ]
    return sorted(found, key=lambda e: e["model"])


def _tokens_by_model(
    run, scope_entry: dict, table: _RateTable, periods: list[_Period], dates: list[str]
) -> list[list[dict]]:
    """Per period, `[{model, expected, p90}]` from the series' USD paths and effective rates."""
    out: list[list[dict]] = [[] for _ in periods]
    for entry in _series_of(run, scope_entry):
        key = (entry["scope_type"], entry["scope_name"], entry["model"])
        rate = table.rate.get(key)
        state = table.state.get(key)
        path = run.paths(entry)
        for slot, period in enumerate(periods):
            window = np.asarray(path[period.lo : period.hi + 1])
            if rate is None:
                rates = np.zeros(window.shape[0])
                states = np.zeros(window.shape[0], dtype=np.int8)
            else:
                rates = rate[period.lo : period.hi + 1]
                states = state[period.lo : period.hi + 1]
            label = f"{entry['scope_type']} {entry['scope_name']!r} model {entry['model']!r}"
            if np.any(states == 2):
                day = dates[period.lo + int(np.argmax(states == 2))]
                raise _fail(f"{label}: tokens on {day} but a zero rate, tokens cannot be derived from USD")
            undefined = states == 0
            if np.any(window[undefined] > 0):
                day = dates[period.lo + int(np.argmax(np.any(window > 0, axis=1) & undefined))]
                raise _fail(f"{label}: USD on {day} but no tokens in daily_expected (rate undefined)")
            usable = states == 1
            if usable.any():
                per_sample = (window[usable] / rates[usable, None]).sum(axis=0)
            else:
                per_sample = np.zeros(window.shape[1])
            out[slot].append(
                {
                    "model": entry["model"],
                    "expected": int(_rounded(per_sample.mean(), 1.0, "a token count")),
                    "p90": int(
                        _rounded(np.quantile(per_sample, TOKENS_PERCENTILE / 100), 1.0, "a token count")
                    ),
                }
            )
    return out


def _series_entry(
    run, scope_entry: dict, table: _RateTable, periods: list[_Period], dates: list[str], grid: np.ndarray
) -> dict:
    status = scope_entry["status"]
    if status not in STATUSES:
        raise _fail(f"cost scope status {status!r} is not one of {STATUSES}")
    if status == "incomplete":
        return {"status": "incomplete"}
    usd = run.scope_paths(scope_entry)
    if usd is None:
        raise _fail(f"{scope_entry['scope_type']} {scope_entry['scope_name']!r} has status {status!r} but no paths")
    expected_shape = (len(run.index["dates"]), run.index["samples"])
    if usd.shape != expected_shape:
        raise _fail(f"scope paths have shape {usd.shape}, expected {expected_shape}")
    band = np.array([BAND_LOW, BAND_HIGH], dtype=np.float64) / 100.0
    low_high = np.quantile(usd, band, axis=1)
    entry: dict = {
        "status": status,
        "daily": {
            "expected": _money(usd.mean(axis=1)).tolist(),
            "low": _money(low_high[0]).tolist(),
            "high": _money(low_high[1]).tolist(),
        },
    }
    tokens = _tokens_by_model(run, scope_entry, table, periods, dates)
    blocks = []
    for slot, period in enumerate(periods):
        cumulative = np.cumsum(usd[period.lo : period.hi + 1], axis=0)
        expected = _money(cumulative.mean(axis=1))
        rows = _money(np.quantile(cumulative, grid, axis=1)).T  # [date, grid]
        # The total is the last cumulative entry itself, so the two cannot disagree.
        blocks.append(
            {
                "cumulative": {"expected": expected.tolist(), "quantiles": rows.tolist()},
                "total": {"expected": int(expected[-1]), "quantiles": rows[-1].tolist()},
                "tokens_by_model": tokens[slot],
            }
        )
    entry["periods"] = blocks
    return entry


def build_export(
    result: DashboardRunResult,
    scopes: Sequence[DashboardScope],
    *,
    series_dir: Path,
    now: datetime | None = None,
    load_forecast: Callable = load_run,
    load_cost: Callable = load_cost_run,
) -> dict:
    """The export document for a completed run (validated before it is returned).

    `scopes` come from `map_scopes` for the same run. `series_dir` supplies the timezone.
    `now` (timezone-aware) stamps `generated_at`; the default is the current UTC time.
    `load_forecast` and `load_cost` are the readers of the forecast run and of each cost
    run; the defaults are the only readers production code uses, they are parameters so
    a test can hand in prepared runs.
    """
    log = get_logger(__name__)
    generated_at = _utc_stamp(now)
    timezone_name = _read_timezone(series_dir)
    log.info(
        "dashboard.export.started",
        output_dir=str(result.output_dir),
        scope_count=len(scopes),
        preset_count=len(result.presets),
    )
    try:
        forecast = load_forecast(result.forecast_dir)
        runs = {preset.id: load_cost(result.cost_dirs[preset.id]) for preset in result.presets}
    except (*_READ_ERRORS, KeyError, OSError) as exc:
        raise _fail(f"cannot read the run: {exc.strerror if isinstance(exc, OSError) and exc.strerror else exc}") from exc

    dates = list(next(iter(runs.values())).index["dates"])
    # Provenance only: the one file read outside the two loaders, to hash it.
    forecast_manifest = Path(result.forecast_dir) / "manifest.json"
    for preset in result.presets:
        run = runs[preset.id]
        if list(run.index["dates"]) != dates:
            raise _fail(f"cost run {preset.id!r} covers different dates than the others")
        if run.request.get("scenario") != preset.id:
            raise _fail(f"cost run {preset.id!r} was made for scenario {run.request.get('scenario')!r}")
        recorded = (run.request.get("forecast_run") or {}).get("manifest_sha256")
        if isinstance(recorded, str) and forecast_manifest.is_file() and recorded != _sha256(forecast_manifest):
            raise _fail(f"cost run {preset.id!r} was priced from a different forecast run")
    periods = _calendar_periods(forecast, result, dates)
    if dates[0] != periods[0].start or dates[-1] != periods[1].end:
        raise _fail("the cost run's dates do not span exactly the two calendar periods")

    try:
        indexes = {}
        for preset in result.presets:
            entries = {(s["scope_type"], s["scope_name"]): s for s in runs[preset.id].index["scopes"]}
            wanted = {(s.run_scope.scope_type, s.run_scope.name) for s in scopes}
            if set(entries) != wanted:
                raise _fail(
                    f"cost run {preset.id!r} has scopes {sorted(set(entries) ^ wanted)} "
                    "that differ from the run's scope list"
                )
            indexes[preset.id] = entries
        tables = {preset.id: _rate_table(runs[preset.id], dates) for preset in result.presets}
        presets_doc = [
            {
                "id": preset.id,
                "label": preset.label,
                "group": preset.group,
                "description": preset.description,
                "assumptions": sorted(
                    {a for s in runs[preset.id].index["scopes"] for a in s["assumptions"]}
                ),
            }
            for preset in result.presets
        ]
        grid = np.array(QUANTILE_GRID, dtype=np.float64) / 100.0
        scopes_doc = []
        for scope in scopes:
            series = {}
            for preset in result.presets:
                entry = indexes[preset.id][(scope.run_scope.scope_type, scope.run_scope.name)]
                series[preset.id] = _series_entry(runs[preset.id], entry, tables[preset.id], periods, dates, grid)
            scopes_doc.append(
                {"scope_type": scope.scope_type, "scope_id": scope.scope_id, "name": scope.name, "series": series}
            )
            log.info(
                "dashboard.export.scope_exported",
                scope_type=scope.scope_type,
                scope_id=scope.scope_id,
                preset_count=len(series),
            )
    except (*_READ_ERRORS, OSError) as exc:
        raise _fail(f"cannot read the run: {exc}") from exc

    doc = {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "timezone": timezone_name,
        "origin": result.origin.isoformat(),
        "data_through": result.data_through.isoformat(),
        "band": {"low": BAND_LOW, "high": BAND_HIGH},
        "quantile_grid": list(QUANTILE_GRID),
        "dates": dates,
        "periods": [p.as_dict() for p in periods],
        "presets": presets_doc,
        "scopes": scopes_doc,
    }
    validate_export(doc)
    return doc


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def canonical_bytes(doc: dict) -> bytes:
    """Sorted keys, no spaces, UTF-8, one trailing newline."""
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def write_export(doc: dict, output_dir: Path) -> Path:
    """Write `<output_dir>/forecast_export.json` atomically (a temporary file in the same
    directory, then `os.replace`); a failure leaves any earlier file untouched and no
    temporary file behind. An `OSError` is an `ExecutionError`."""
    target = Path(output_dir) / EXPORT_FILENAME
    data = canonical_bytes(doc)
    # Created like any ordinary file (permissions from the umask), unlike mkstemp's 0600.
    temporary = target.parent / f".{EXPORT_FILENAME}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise _fail(f"cannot write {target}: {exc.strerror or exc}") from exc
    return target


@dataclass(frozen=True)
class ExportResult:
    path: Path
    bytes: int
    document: dict


def export_forecast(
    result: DashboardRunResult,
    scopes: Sequence[DashboardScope],
    *,
    series_dir: Path,
    now: datetime | None = None,
) -> ExportResult:
    """Build, validate and write the export into the run's output directory."""
    log = get_logger(__name__)
    doc = build_export(result, scopes, series_dir=series_dir, now=now)
    path = write_export(doc, result.output_dir)
    size = path.stat().st_size
    log.info("dashboard.export.completed", path=str(path), bytes=size)
    return ExportResult(path, size, doc)
