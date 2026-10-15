"""The strict reader of the stored forecast run (`server/api/manager/forecast_store.py`), no
database: `_parse_run` over a row built from a valid hand-made export, with one thing damaged
at a time. The loader checks the document's shape only, so every one of these must read as
`ForecastUnreadable` (the service turns it into "no forecast" and a log line), never as an
exception of another type."""
import copy
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from server.api.manager import forecast_store as store
from server.api.manager.forecast_store import ForecastUnreadable, parse_series

from .forecast_fixtures import make_export


def _row(doc=None, **over):
    doc = doc or make_export()
    meta = {k: copy.deepcopy(doc[k]) for k in ("timezone", "band", "quantile_grid", "dates",
                                                 "periods", "presets")}
    meta["scopes"] = [{k: sc[k] for k in ("scope_type", "scope_id", "name")} for sc in doc["scopes"]]
    row = SimpleNamespace(run_id="run-1", origin=doc["origin"], data_through=doc["data_through"],
                          meta=meta)
    for key, value in over.items():
        setattr(row, key, value)
    return row


def _run():
    return store._parse_run(_row())


def test_a_valid_row_reads():
    run = _run()
    assert run.run_id == "run-1" and run.timezone == "Asia/Kolkata"
    assert (run.origin, run.data_through) == (date(2026, 9, 30), date(2026, 10, 8))
    assert [p.current for p in run.periods] == [True, False]
    assert run.periods[0].forecast_from == date(2026, 10, 9) and run.periods[0].length == 23
    assert run.band_low == 10 and run.band_high == 90 and len(run.grid) == 21
    assert [p["id"] for p in run.presets] == ["baseline", "usage-up-20", "opus-to-sonnet-30"]


def test_a_valid_month_end_row_reads_with_no_current_period():
    run = store._parse_run(_row(make_export(data_through="2026-10-31")))
    assert [p.current for p in run.periods] == [False, False]
    assert run.periods[0].start == date(2026, 11, 1)


def _meta(fn):
    def apply(row):
        fn(row.meta)
    return apply


def _drop(*path):
    def go(meta):
        node = meta
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]
    return _meta(go)


def _set(value, *path):
    def go(meta):
        node = meta
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return _meta(go)


def _swap_grid(meta):
    meta["quantile_grid"][1], meta["quantile_grid"][2] = meta["quantile_grid"][2], meta["quantile_grid"][1]


def _attr(name, value):
    def go(row):
        setattr(row, name, value)
    return go


DAMAGE = {
    # missing keys
    "no_timezone": _drop("timezone"), "no_band": _drop("band"), "no_grid": _drop("quantile_grid"),
    "no_dates": _drop("dates"), "no_periods": _drop("periods"), "no_presets": _drop("presets"),
    "no_scopes": _drop("scopes"), "no_band_low": _drop("band", "low"),
    "no_period_from": _drop("periods", 0, "from"), "no_period_end": _drop("periods", 1, "end"),
    "no_preset_id": _drop("presets", 0, "id"), "no_preset_group": _drop("presets", 1, "group"),
    "no_preset_description": _drop("presets", 0, "description"),
    "no_preset_assumptions": _drop("presets", 2, "assumptions"),
    "no_scope_name": _drop("scopes", 0, "name"),
    # wrong types: a float or a bool where an integer is required
    "float_band_low": _set(10.0, "band", "low"), "bool_band_high": _set(True, "band", "high"),
    "float_in_grid": _set(5.0, "quantile_grid", 1), "bool_in_grid": _set(True, "quantile_grid", 1),
    "string_in_grid": _set("5", "quantile_grid", 1),
    "timezone_int": _set(5, "timezone"), "timezone_empty": _set("", "timezone"),
    "timezone_unknown": _set("Not/AZone", "timezone"), "timezone_path": _set("../etc/passwd", "timezone"),
    "timezone_long": _set("a" * 300, "timezone"),
    "meta_is_a_list": lambda row: setattr(row, "meta", [1]),
    "preset_group_int": _set(3, "presets", 1, "group"),
    "assumption_int": _set([1], "presets", 1, "assumptions"),
    "dates_not_a_list": _set("x", "dates"), "date_garbage": _set("2026-13-45", "dates", 3),
    "dates_empty": _set([], "dates"), "periods_empty": _set([], "periods"),
    "presets_empty": _set([], "presets"),
    # grid and band
    "grid_unordered": _meta(_swap_grid), "grid_duplicate": _set(5, "quantile_grid", 2),
    "grid_one_entry": _set([0], "quantile_grid"),
    "band_not_in_grid": _set(12, "band", "low"), "band_reversed": _meta(
        lambda m: m.__setitem__("band", {"low": 90, "high": 10})),
    # periods against the dates axis
    "from_not_in_dates": _set("2027-05-01", "periods", 0, "from"),
    "end_not_in_dates": _set("2027-05-01", "periods", 1, "end"),
    "from_before_start": _set("2026-09-30", "periods", 0, "from"),
    "from_after_end": _set("2026-11-05", "periods", 0, "from"),
    # data_through / origin against the periods
    "data_through_9999": _attr("data_through", "9999-12-31"),
    "data_through_3000": _attr("data_through", "3000-01-01"),
    "data_through_2000": _attr("data_through", "2000-01-01"),
    "data_through_infinity": _attr("data_through", "infinity"),
    "data_through_minus_infinity": _attr("data_through", "-infinity"),
    "data_through_garbage": _attr("data_through", "soon"), "data_through_none": _attr("data_through", None),
    "data_through_off_by_one": _attr("data_through", "2026-10-09"),
    "data_through_before_current_from": _attr("data_through", "2026-10-07"),
    "origin_infinity": _attr("origin", "infinity"), "origin_garbage": _attr("origin", "x"),
    "origin_after_data_through": _attr("origin", "2026-10-09"),
}


@pytest.mark.parametrize("name", sorted(DAMAGE))
def test_a_damaged_row_is_unreadable_with_a_reason_and_the_run_id(name):
    row = _row()
    DAMAGE[name](row)
    with pytest.raises(ForecastUnreadable) as caught:
        store._parse_run(row)
    assert caught.value.reason
    assert caught.value.run_id == "run-1"


def test_month_end_data_through_must_match_the_first_period():
    doc = make_export(data_through="2026-10-31")
    for wrong in ("2026-10-30", "2026-11-01", "9999-12-31"):
        with pytest.raises(ForecastUnreadable):
            store._parse_run(_row(doc, data_through=wrong))


def test_two_current_periods_are_unreadable():
    row = _row()
    row.meta["periods"][1]["from"] = "2026-11-02"            # also "current": from > start
    with pytest.raises(ForecastUnreadable):
        store._parse_run(row)


def test_an_actuals_window_longer_than_the_cap_is_unreadable():
    """The first period lies far before data_through (a period list that still agrees with the
    dates axis): the 28-day lookback would fetch more than `MAX_ACTUAL_DAYS`."""
    doc = make_export()
    first = date(2025, 1, 1)
    last = date(2026, 11, 30)
    dates = [(first + timedelta(days=k)).isoformat() for k in range((last - first).days + 1)]
    row = _row(doc)
    row.meta["dates"] = dates
    row.meta["periods"] = [{"start": "2025-01-01", "end": "2025-01-31", "from": "2025-01-01"},
                           {"start": "2026-10-01", "end": "2026-10-31", "from": "2026-10-09"}]
    with pytest.raises(ForecastUnreadable) as caught:
        store._parse_run(row)
    assert "actuals window" in caught.value.reason
    # the same run just inside the cap reads: first period starts 300 days before data_through
    row.meta["periods"][0] = {"start": "2025-12-10", "end": "2025-12-31", "from": "2025-12-10"}
    row.meta["periods"][1] = {"start": "2026-10-01", "end": "2026-10-31", "from": "2026-10-09"}
    run = store._parse_run(row)
    assert (run.data_through - (run.periods[0].start - timedelta(days=28))).days < store.MAX_ACTUAL_DAYS


# ------------------------------------------------------------------------------ series

def _series(status="complete", **mutate):
    doc = make_export()
    entry = copy.deepcopy(doc["scopes"][0]["series"]["baseline"])
    payload = {k: v for k, v in entry.items() if k != "status"}
    for dotted, value in mutate.items():
        node = payload
        keys = dotted.split("__")
        for key in keys[:-1]:
            node = node[int(key)] if key.isdigit() else node[key]
        last = keys[-1]
        if value is _DELETE:
            del node[int(last) if last.isdigit() else last]
        else:
            node[int(last) if last.isdigit() else last] = value
    return status, payload


_DELETE = object()
BAD_SERIES = {
    "no_daily": {"daily": _DELETE}, "no_periods": {"periods": _DELETE},
    "daily_short": {"daily__expected": [1, 2, 3]},
    "daily_float": {"daily__low__0": 1.5}, "daily_bool": {"daily__high__0": True},
    "daily_string": {"daily__expected__4": "7"},
    "one_period_missing": {"periods": []},
    "extra_period": {},
    "no_cumulative": {"periods__0__cumulative": _DELETE},
    "cumulative_expected_short": {"periods__0__cumulative__expected": [1]},
    "cumulative_expected_float": {"periods__0__cumulative__expected__0": 2.5},
    "quantile_row_short": {"periods__0__cumulative__quantiles__3": [1, 2]},
    "quantile_row_float": {"periods__1__cumulative__quantiles__0__2": 0.5},
    "quantile_row_bool": {"periods__1__cumulative__quantiles__0__2": False},
    "quantiles_missing_rows": {"periods__0__cumulative__quantiles": []},
    "no_total": {"periods__0__total": _DELETE},
    "total_expected_float": {"periods__0__total__expected": 1.0},
    "total_expected_bool": {"periods__0__total__expected": True},
    "total_expected_missing": {"periods__0__total__expected": _DELETE},
    "total_quantiles_short": {"periods__1__total__quantiles": [1, 2, 3]},
    "total_quantiles_float": {"periods__1__total__quantiles__5": 3.5},
    "no_tokens": {"periods__0__tokens_by_model": _DELETE},
    "tokens_not_a_list": {"periods__0__tokens_by_model": "x"},
    "token_model_missing": {"periods__0__tokens_by_model__0__model": _DELETE},
    "token_expected_float": {"periods__0__tokens_by_model__0__expected": 1.5},
    "token_p90_bool": {"periods__0__tokens_by_model__1__p90": True},
    "payload_not_an_object": None,
}


@pytest.mark.parametrize("name", sorted(BAD_SERIES))
def test_a_damaged_series_is_unreadable(name):
    status, payload = _series(**(BAD_SERIES[name] or {}))
    if BAD_SERIES[name] is None:
        payload = [1]
    elif name == "extra_period":
        payload["periods"].append(copy.deepcopy(payload["periods"][0]))
    with pytest.raises(ForecastUnreadable):
        parse_series(_run(), "baseline", status, payload)


def test_a_valid_series_reads_and_unknown_status_does_not():
    status, payload = _series()
    out = parse_series(_run(), "baseline", status, payload)
    assert out["status"] == "complete" and len(out["periods"]) == 2
    assert out["periods"][0]["total_expected"] == payload["periods"][0]["total"]["expected"]
    assert len(out["daily"]["expected"]) == 61
    assert parse_series(_run(), "baseline", "incomplete", {}) == {
        "status": "incomplete", "daily": None, "periods": []}
    for status in ("weird", None, "COMPLETE"):
        with pytest.raises(ForecastUnreadable):
            parse_series(_run(), "baseline", status, payload)
