"""Export: shape, per-sample summing before percentiles, the `from` rules, statuses,
rounding, determinism, `validate_export` on corrupted documents, atomic writing.

Most of the numeric tests build the document from small hand-made runs (stand-ins that
look like loaded runs and are handed to `build_export` through its loader arguments), so
the expected figures can be worked out by hand; the shape and `from` tests also use a
real run on the shared fixture dataset."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import structlog
import yaml

from forecast.cost import load_cost_run
from forecast.cost.errors import InputError as CostInputError
from forecast.dashboard import (
    DEFAULT_PRESETS_PATH,
    DashboardRunResult,
    DashboardScope,
    ExecutionError,
    InputError,
    RunScope,
    build_export,
    export_forecast,
    load_presets,
    map_scopes,
    run_dashboard_forecast,
    validate_export,
    write_export,
)
from forecast.dashboard import export as export_module
from forecast.dashboard.export import QUANTILE_GRID, canonical_bytes

NOW = datetime(2026, 10, 8, 9, 30, 15, tzinfo=timezone.utc)
PRESETS = load_presets(DEFAULT_PRESETS_PATH)
BASELINE, SONNET_30 = PRESETS[0], PRESETS[1]
SAMPLES = 100


# --- hand-made runs ------------------------------------------------------------


class FakeCostRun:
    """Looks like a loaded cost run: an index, a request, `paths`, `scope_paths` and
    `daily_expected`. `spec` maps (scope_type, scope_name) to
    `{"status", "assumptions", "series": {model: (tokens[date, sample], rate[date])}}`;
    a series' USD path is tokens x rate and its daily rows are built from the same."""

    def __init__(self, preset_id, dates, spec, *, rows=None):
        self.request = {"scenario": preset_id}
        self.spec = spec
        self.arrays = {}
        series, scopes = [], []
        for (scope_type, scope_name), item in spec.items():
            scopes.append(
                {
                    "scope_type": scope_type, "scope_name": scope_name, "status": item["status"],
                    "assumptions": list(item.get("assumptions", [])),
                }
            )
            for model, (tokens, rate) in item["series"].items():
                tokens = np.asarray(tokens, dtype=np.float64)
                rate = np.asarray(rate, dtype=np.float64)
                key = f"{scope_type}/{scope_name}/{model}"
                self.arrays[key] = tokens * rate[:, None]
                series.append(
                    {
                        "scope_type": scope_type, "scope_name": scope_name, "model": model,
                        "status": "qualified", "path": key, "assumptions": [],
                    }
                )
        self.index = {"dates": list(dates), "samples": SAMPLES, "scopes": scopes, "series": series}
        self._rows = rows

    def paths(self, entry):
        return self.arrays[entry["path"]]

    def scope_paths(self, scope_entry):
        if scope_entry["status"] == "incomplete":
            return None
        total = np.zeros((len(self.index["dates"]), SAMPLES))
        for entry in self.index["series"]:
            if entry["scope_type"] == scope_entry["scope_type"] and entry["scope_name"] == scope_entry["scope_name"]:
                total = total + self.arrays[entry["path"]]
        return total

    @property
    def daily_expected(self):
        if self._rows is not None:
            return self._rows
        rows = self._rows = []
        for (scope_type, scope_name), item in self.spec.items():
            for model, (tokens, rate) in item["series"].items():
                mean = np.asarray(tokens, dtype=np.float64).mean(axis=1)
                for position, day in enumerate(self.index["dates"]):
                    rows.append(
                        {
                            "date": day, "scope_type": scope_type, "scope_name": scope_name, "model": model,
                            "agent_type": "claude_code",
                            "tokens": {"input": float(mean[position])},
                            "usd": {"input": float(mean[position] * rate[position])},
                        }
                    )
        return rows


DAYS = [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(6)]
ONES = [1.0] * 6


def const(value):
    return np.full((6, SAMPLES), float(value))


def ramp(scale=1.0):
    """tokens[d, s] = scale * s: the same ramp on every date."""
    return np.tile(np.arange(SAMPLES) * scale, (6, 1))


def scope_spec(series, *, status="complete", assumptions=()):
    return {"status": status, "assumptions": list(assumptions), "series": series}


class World:
    """Everything `build_export` needs for a two-period, six-day run."""

    def __init__(self, tmp_path, specs, *, presets=(BASELINE,), data_through=date(2026, 10, 1),
                 origin=date(2026, 9, 30), periods=None, scope_names=None, timezone_name="Asia/Kolkata"):
        self.series_dir = tmp_path / "series"
        self.series_dir.mkdir(exist_ok=True)
        manifest = {} if timezone_name is None else {"timezone": timezone_name}
        (self.series_dir / "manifest.json").write_text(json.dumps(manifest))
        self.runs = {p.id: FakeCostRun(p.id, DAYS, specs[p.id] if isinstance(specs, dict) and p.id in specs else specs) for p in presets}
        calendar = periods or [
            {"type": "calendar", "start": date(2026, 10, 1), "end": date(2026, 10, 3)},
            {"type": "calendar", "start": date(2026, 10, 4), "end": date(2026, 10, 6)},
        ]
        self.forecast = SimpleNamespace(
            origin=origin,
            periods=({"type": "requested", "start": date(2026, 10, 1), "end": date(2026, 10, 6)}, *calendar),
        )
        keys = list(next(iter(self.runs.values())).spec)
        run_scopes = tuple(RunScope(t, n) for t, n in keys)
        self.result = DashboardRunResult(
            data_through=data_through, origin=origin, org_id="acme", output_dir=tmp_path / "out",
            forecast_dir=tmp_path / "forecast", presets=tuple(presets),
            cost_dirs={p.id: tmp_path / "cost" / p.id for p in presets}, scopes=run_scopes,
        )
        names = scope_names or {}
        self.scopes = tuple(
            DashboardScope(s, {"organization": "org", "team": "team", "project": "repo"}[s.scope_type],
                           names.get(s.name, s.name.lower()), s.name)
            for s in run_scopes
        )

    def build(self, **kwargs):
        kwargs.setdefault("now", NOW)
        return build_export(
            self.result, self.scopes, series_dir=self.series_dir,
            load_forecast=lambda path: self.forecast,
            load_cost=lambda path: self.runs[Path(path).name], **kwargs,
        )


def team(name="T", **kwargs):
    return {("team", name): scope_spec(kwargs.pop("series"), **kwargs)}


@pytest.fixture
def world(tmp_path):
    def make(specs, **kwargs):
        return World(tmp_path, specs, **kwargs)
    return make


# --- per-sample summing, rounding, the daily band -------------------------------


def test_percentiles_are_taken_after_summing_the_series_per_sample(world):
    # Two anti-correlated series: every sample's total is the same constant, so every
    # percentile of the total is that constant. Adding the series' own percentiles would
    # give twice the percentile of a ramp instead.
    ramp_up = ramp(1.0)
    ramp_down = 99.0 - ramp(1.0)
    w = world(team(series={"m-a": (ramp_up, ONES), "m-b": (ramp_down, ONES)}))
    doc = w.build()
    entry = doc["scopes"][0]["series"]["baseline"]
    p0, p1 = entry["periods"]
    assert entry["daily"] == {"expected": [99_000_000] * 6, "low": [99_000_000] * 6, "high": [99_000_000] * 6}
    assert p0["total"]["quantiles"] == [198_000_000] * 21  # 2 days from Oct 2
    assert p1["total"]["quantiles"] == [297_000_000] * 21  # 3 days from Oct 4
    assert p0["cumulative"]["quantiles"] == [[99_000_000] * 21, [198_000_000] * 21]
    separate = 2 * np.quantile(np.arange(100.0), 0.10) * 2  # 2 series x 2 days, p10 of each added up
    assert round(separate * 1e6) != 198_000_000


def test_daily_values_are_the_mean_and_the_band_percentiles(world):
    w = world(team(series={"m-a": (ramp(), ONES)}))
    daily = w.build()["scopes"][0]["series"]["baseline"]["daily"]
    assert daily["expected"] == [49_500_000] * 6
    assert daily["low"] == [9_900_000] * 6  # p10 of 0..99 is 9.9
    assert daily["high"] == [89_100_000] * 6  # p90 is 89.1


def test_money_is_rounded_once_from_the_exact_sum(world):
    w = world(team(series={"m": (const(1.2345678), ONES)}))
    entry = w.build()["scopes"][0]["series"]["baseline"]
    assert entry["daily"]["expected"] == [1_234_568] * 6
    p0, p1 = entry["periods"]
    assert p0["cumulative"]["expected"] == [1_234_568, 2_469_136]  # 2.4691356 -> ...136
    # 3 x 1.2345678 = 3.7037034 -> 3703703; the sum of the rounded daily values would be 3703704.
    assert p1["cumulative"]["expected"] == [1_234_568, 2_469_136, 3_703_703]
    assert p1["total"]["expected"] == 3_703_703


def test_the_total_is_the_last_cumulative_entry(world):
    w = world(team(series={"m-a": (ramp(), ONES), "m-b": (ramp(0.5), [2.0] * 6)}))
    for series in w.build()["scopes"][0]["series"].values():
        for period in series["periods"]:
            assert period["total"]["expected"] == period["cumulative"]["expected"][-1]
            assert period["total"]["quantiles"] == period["cumulative"]["quantiles"][-1]


def test_tokens_by_model_divide_each_series_by_its_own_daily_rate(world):
    rates = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    w = world(team(series={"m-b": (ramp(10), rates), "m-a": (ramp(10), ONES)}))
    p0, p1 = w.build()["scopes"][0]["series"]["baseline"]["periods"]
    # Tokens are 10 x the sample number on every date, whatever the rate was; period 1 is
    # 3 days: mean 3 x 495, p90 3 x 10 x 89.1.
    assert p1["tokens_by_model"] == [
        {"model": "m-a", "expected": 1485, "p90": 2673},
        {"model": "m-b", "expected": 1485, "p90": 2673},
    ]
    assert [m["expected"] for m in p0["tokens_by_model"]] == [990, 990]  # 2 days from Oct 2


def test_tokens_are_rounded_to_the_nearest_integer(world):
    # 1.9 tokens a day: 2 days from Oct 2 = 3.8 -> 4, 3 days in November = 5.7 -> 6
    # (truncation would give 3 and 5).
    w = world(team(series={"m": (const(1.9), ONES)}))
    p0, p1 = w.build()["scopes"][0]["series"]["baseline"]["periods"]
    assert p0["tokens_by_model"] == [{"model": "m", "expected": 4, "p90": 4}]
    assert p1["tokens_by_model"] == [{"model": "m", "expected": 6, "p90": 6}]


def test_an_amount_beyond_the_integer_range_is_an_error(world):
    w = world(team(series={"m": (const(1e30), ONES)}))
    with pytest.raises(ExecutionError, match="finite number within range"):
        w.build()


def test_tokens_are_zero_where_there_is_nothing_to_price(world):
    zero = const(0)
    w = world(team(series={"m-a": (zero, ONES), "m-b": (ramp(), ONES)}))
    p0, _ = w.build()["scopes"][0]["series"]["baseline"]["periods"]
    assert p0["tokens_by_model"][0] == {"model": "m-a", "expected": 0, "p90": 0}


def test_usd_without_tokens_is_an_error_not_a_zero(world):
    w = world(team(series={"m": (ramp(), ONES)}))
    rows = [r for r in w.runs["baseline"].daily_expected if r["date"] != "2026-10-02"]
    w.runs["baseline"]._rows = rows  # the rate of Oct 2 is now undefined while its USD is not zero
    with pytest.raises(ExecutionError, match="2026-10-02") as caught:
        w.build()
    assert caught.value.stage == "export"


def test_tokens_at_a_zero_rate_are_an_error(world):
    w = world(team(series={"m": (ramp(), ONES)}))
    for row in w.runs["baseline"].daily_expected:
        row["usd"] = {"input": 0.0}
    with pytest.raises(ExecutionError, match="zero rate"):
        w.build()


# --- statuses ----------------------------------------------------------------------


def test_an_incomplete_scope_carries_only_its_status(world):
    specs = {
        ("team", "Ok"): scope_spec({"m": (ramp(), ONES)}),
        ("team", "Bad"): scope_spec({}, status="incomplete"),
    }
    doc = world(specs).build()
    assert doc["scopes"][1]["series"] == {"baseline": {"status": "incomplete"}}
    assert doc["scopes"][0]["series"]["baseline"]["status"] == "complete"
    validate_export(doc)


@pytest.mark.parametrize("status", ["complete", "conditional", "inactive"])
def test_the_cost_scope_status_is_passed_through(world, status):
    series = {"m": (const(0), ONES)} if status == "inactive" else {"m": (ramp(), ONES)}
    doc = world(team(series=series, status=status)).build()
    entry = doc["scopes"][0]["series"]["baseline"]
    assert entry["status"] == status
    assert "daily" in entry and len(entry["periods"]) == 2
    if status == "inactive":
        assert entry["periods"][0]["total"]["quantiles"] == [0] * 21


def test_an_unknown_scope_status_is_refused(world):
    with pytest.raises(ExecutionError, match="status"):
        world(team(series={"m": (ramp(), ONES)}, status="mystery")).build()


# --- presets ------------------------------------------------------------------------


def test_presets_follow_the_file_order_with_labels_groups_and_assumptions(world):
    specs = {
        "baseline": team(series={"m": (ramp(), ONES)}, assumptions=["fixed_mix_estimate", "b_code"]),
        SONNET_30.id: {
            **team(series={"m": (ramp(), ONES)}, assumptions=["fixed_mix_estimate", "scenario_constant_tokens"]),
            ("team", "U"): scope_spec({"m": (ramp(), ONES)}, assumptions=["a_code"]),
        },
    }
    specs["baseline"][("team", "U")] = scope_spec({"m": (ramp(), ONES)}, assumptions=["a_code"])
    doc = world(specs, presets=(BASELINE, SONNET_30)).build()
    assert doc["presets"] == [
        {
            "id": "baseline", "label": BASELINE.label, "group": None, "description": BASELINE.description,
            "assumptions": ["a_code", "b_code", "fixed_mix_estimate"],
        },
        {
            "id": SONNET_30.id, "label": SONNET_30.label, "group": "Model mix", "description": SONNET_30.description,
            "assumptions": ["a_code", "fixed_mix_estimate", "scenario_constant_tokens"],
        },
    ]
    for scope in doc["scopes"]:
        assert list(scope["series"]) == ["baseline", SONNET_30.id]


# --- the `from` rules ------------------------------------------------------------------


@pytest.mark.parametrize(
    "data_through, origin, firsts, lengths",
    [
        (date(2026, 10, 1), date(2026, 9, 30), ["2026-10-02", "2026-10-04"], [2, 3]),  # first of month
        (date(2026, 10, 2), date(2026, 9, 30), ["2026-10-03", "2026-10-04"], [1, 3]),  # mid-month
        (date(2026, 9, 30), date(2026, 9, 30), ["2026-10-01", "2026-10-04"], [3, 3]),  # month end
        (date(2026, 10, 3), date(2026, 9, 30), ["2026-10-04", "2026-10-04"], [0, 3]),  # see below
    ],
)
def test_from_is_the_day_after_the_data_when_it_lies_inside_the_period(world, data_through, origin, firsts, lengths):
    w = world(team(series={"m": (ramp(), ONES)}), data_through=data_through, origin=origin)
    if lengths[0] == 0:
        # The data ends on the last day of period 0: that period is already over.
        with pytest.raises(ExecutionError, match="ends on or before data_through"):
            w.build()
        return
    doc = w.build()
    assert [p["from"] for p in doc["periods"]] == firsts
    entry = doc["scopes"][0]["series"]["baseline"]
    assert [len(p["cumulative"]["expected"]) for p in entry["periods"]] == lengths
    assert [len(p["cumulative"]["quantiles"]) for p in entry["periods"]] == lengths
    assert len(entry["daily"]["expected"]) == 6  # daily covers every date, elapsed days included


# --- reading the runs -------------------------------------------------------------------


def test_the_timezone_comes_from_the_series_manifest(world):
    assert world(team(series={"m": (ramp(), ONES)}), timezone_name="Europe/Berlin").build()["timezone"] == "Europe/Berlin"


@pytest.mark.parametrize("value", [None, ""])
def test_a_series_manifest_without_a_timezone_is_an_error(world, value):
    w = world(team(series={"m": (ramp(), ONES)}), timezone_name=None)
    if value is not None:
        (w.series_dir / "manifest.json").write_text(json.dumps({"timezone": value}))
    with pytest.raises(ExecutionError, match="timezone") as caught:
        w.build()
    assert caught.value.stage == "export"


def test_now_must_be_timezone_aware(world):
    w = world(team(series={"m": (ramp(), ONES)}))
    with pytest.raises(InputError, match="timezone-aware"):
        w.build(now=datetime(2026, 10, 8, 9, 30))


def test_generated_at_is_utc_seconds(world):
    ist = timezone(timedelta(hours=5, minutes=30))
    w = world(team(series={"m": (ramp(), ONES)}))
    assert w.build(now=datetime(2026, 10, 8, 15, 0, 15, 999, tzinfo=ist))["generated_at"] == "2026-10-08T09:30:15Z"


def test_read_errors_become_export_errors(world):
    w = world(team(series={"m": (ramp(), ONES)}))

    def broken(path):
        raise CostInputError("artifact checksum mismatch: daily_expected.jsonl")

    with pytest.raises(ExecutionError, match="checksum mismatch") as caught:
        build_export(w.result, w.scopes, series_dir=w.series_dir, load_forecast=lambda p: w.forecast, load_cost=broken)
    assert caught.value.stage == "export"


def test_a_cost_run_for_another_scenario_is_refused(world):
    w = world(team(series={"m": (ramp(), ONES)}))
    w.runs["baseline"].request["scenario"] = "something-else"
    with pytest.raises(ExecutionError, match="something-else"):
        w.build()


def test_cost_runs_over_different_dates_are_refused(world):
    w = world(team(series={"m": (ramp(), ONES)}), presets=(BASELINE, SONNET_30))
    w.runs[SONNET_30.id].index["dates"] = DAYS[:-1] + ["2026-10-09"]
    with pytest.raises(ExecutionError, match="different dates"):
        w.build()


def test_a_cost_run_priced_from_another_forecast_run_is_refused(world, tmp_path):
    w = world(team(series={"m": (ramp(), ONES)}))
    w.result.forecast_dir.mkdir()
    (w.result.forecast_dir / "manifest.json").write_text("{}")
    w.runs["baseline"].request["forecast_run"] = {"manifest_sha256": "0" * 64}
    with pytest.raises(ExecutionError, match="different forecast run"):
        w.build()


def test_the_forecast_must_have_two_calendar_periods(world):
    w = world(team(series={"m": (ramp(), ONES)}))
    w.forecast.periods = w.forecast.periods[:2]
    with pytest.raises(ExecutionError, match="1 calendar period"):
        w.build()
    w.forecast.periods = None
    with pytest.raises(ExecutionError, match="records no periods"):
        w.build()


def test_the_forecast_origin_must_match_the_run(world):
    w = world(team(series={"m": (ramp(), ONES)}))
    w.forecast.origin = date(2026, 9, 29)
    with pytest.raises(ExecutionError, match="origin"):
        w.build()


def test_the_cost_scopes_must_be_the_runs_scopes(world):
    w = world(team(series={"m": (ramp(), ONES)}))
    w.runs["baseline"].index["scopes"].append(
        {"scope_type": "team", "scope_name": "Extra", "status": "complete", "assumptions": []}
    )
    with pytest.raises(ExecutionError, match="scope list"):
        w.build()


# --- determinism ---------------------------------------------------------------------------


def test_two_builds_with_a_pinned_clock_are_byte_identical(world):
    w = world(team(series={"m-a": (ramp(), ONES), "m-b": (ramp(0.3), [2.0] * 6)}))
    assert canonical_bytes(w.build()) == canonical_bytes(w.build())


def test_builds_at_different_times_differ_only_in_generated_at(world):
    w = world(team(series={"m": (ramp(), ONES)}))
    first, second = w.build(), w.build(now=NOW + timedelta(seconds=7))
    assert first["generated_at"] != second["generated_at"]
    first.pop("generated_at"), second.pop("generated_at")
    assert first == second


def test_the_default_clock_is_the_current_utc_time(world):
    w = world(team(series={"m": (ramp(), ONES)}))
    before = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = w.build(now=None)["generated_at"]
    after = datetime.now(timezone.utc)
    parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert before <= parsed <= after


# --- validate_export on corrupted documents -------------------------------------------------


@pytest.fixture
def good_doc(world):
    specs = {
        ("team", "T"): scope_spec({"m-a": (ramp(), ONES), "m-b": (ramp(0.5), ONES)}),
        ("team", "Bad"): scope_spec({}, status="incomplete"),
    }
    doc = world(specs, presets=(BASELINE, SONNET_30)).build()
    validate_export(doc)
    return doc


def _entry(doc, preset="baseline", scope=0):
    return doc["scopes"][scope]["series"][preset]


def _period(doc, index=0):
    return _entry(doc)["periods"][index]


def _set(path_to, value):
    def apply(doc):
        target = doc
        for key in path_to[:-1]:
            target = target[key]
        target[path_to[-1]] = value
    return apply


def _pop(path_to):
    def apply(doc):
        target = doc
        for key in path_to[:-1]:
            target = target[key]
        del target[path_to[-1]]
    return apply


def _entry_path(*rest, preset="baseline", scope=0):
    return ["scopes", scope, "series", preset, *rest]


def _lift_first_date(period):
    """Raise the whole first row one micro-dollar above the second, so each row is still
    sound by itself and only the columns decrease across dates."""
    rows = period["cumulative"]["quantiles"]
    rows[0] = [value + 1 for value in rows[1]]
    period["cumulative"]["expected"][0] = rows[0][0]


CORRUPTIONS = {
    "schema id": (_set(["schema"], "dashboard-forecast/2.0"), "schema"),
    "extra top-level key": (_set(["extra"], 1), "document has keys"),
    "missing key": (_pop(["origin"]), "document has keys"),
    "generated_at format": (_set(["generated_at"], "2026-10-08 09:30"), "generated_at"),
    "empty timezone": (_set(["timezone"], ""), "timezone"),
    "origin not a date": (_set(["origin"], "yesterday"), "origin"),
    "origin after data_through": (_set(["origin"], "2026-12-01"), "origin is after"),
    "band low off the grid": (_set(["band", "low"], 11), "band.low"),
    "band high off the grid": (_set(["band", "high"], 97), "band.high"),
    "band reversed": (_set(["band", "low"], 90), "band.low must be below"),
    "grid not increasing": (_set(["quantile_grid", 3], 0), "quantile_grid"),
    "grid not ending at 100": (_set(["quantile_grid"], list(range(0, 100, 5))), "quantile_grid"),
    "dates not consecutive": (_set(["dates", 2], "2026-10-09"), "consecutive"),
    "date not a date": (_set(["dates", 1], "2026-13-01"), "ISO date"),
    "one period": (lambda d: d["periods"].pop(), "two calendar months"),
    "from before start": (_set(["periods", 1, "from"], "2026-10-03"), "start <= from <= end"),
    "period outside the dates": (_set(["periods", 1, "end"], "2026-10-20"), "outside dates"),
    "periods overlap": (_set(["periods", 1, "start"], "2026-10-02"), "overlap"),
    "duplicate preset ids": (_set(["presets", 1, "id"], "baseline"), "preset ids are not unique"),
    "unsorted assumptions": (_set(["presets", 0, "assumptions"], ["b", "a"]), "sorted unique"),
    "empty group": (_set(["presets", 1, "group"], ""), "group"),
    "series keys differ from the presets": (lambda d: d["scopes"][0]["series"].pop(SONNET_30.id), "must equal the preset ids"),
    "duplicate scope": (lambda d: d["scopes"].append(deepcopy(d["scopes"][0])), "duplicate scope"),
    "unknown scope type": (_set(["scopes", 0, "scope_type"], "person"), "scope_type"),
    "empty scope id": (_set(["scopes", 0, "scope_id"], ""), "scope_id"),
    "unknown status": (_set(_entry_path("status"), "mystery"), "status"),
    "incomplete with numbers": (lambda d: _entry(d, scope=1).update(daily={}), "carry no numbers"),
    "daily too short": (lambda d: _entry(d)["daily"]["expected"].pop(), "daily.expected"),
    "daily low above high": (_set(_entry_path("daily", "low", 0), 10**12), "low above high"),
    "float in daily": (_set(_entry_path("daily", "expected", 0), 1.5), "float"),
    "bool in daily": (_set(_entry_path("daily", "high", 0), True), "integer"),
    "negative daily": (_set(_entry_path("daily", "expected", 0), -1), "negative"),
    "one period only": (lambda d: _entry(d)["periods"].pop(), "periods must have 2"),
    "cumulative expected decreasing": (_set(_entry_path("periods", 1, "cumulative", "expected", 2), 0), "decreases"),
    "cumulative expected outside its quantiles": (
        _set(_entry_path("periods", 0, "cumulative", "expected", 1), 10**13), "outside its quantiles"),
    "quantile row decreasing across the grid": (
        _set(_entry_path("periods", 0, "cumulative", "quantiles", 1, 5), 0), "across the grid"),
    "quantile column decreasing across dates": (lambda d: _lift_first_date(_period(d)), "across dates"),
    "quantile row of the wrong width": (lambda d: _period(d)["cumulative"]["quantiles"][0].pop(), "has 20 entries"),
    "too few quantile rows": (lambda d: _period(d)["cumulative"]["quantiles"].pop(), "rows"),
    "total expected differs": (lambda d: _period(d)["total"].update(expected=_period(d)["total"]["expected"] + 1), "total differs"),
    "total quantile differs": (_set(_entry_path("periods", 0, "total", "quantiles", 20), 1), "total differs"),
    "float in total": (_set(_entry_path("periods", 0, "total", "expected"), 3.0), "float"),
    "tokens unsorted": (lambda d: _period(d)["tokens_by_model"].reverse(), "sorted by unique model"),
    "tokens negative": (_set(_entry_path("periods", 0, "tokens_by_model", 0, "p90"), -5), "negative"),
    "tokens float": (_set(_entry_path("periods", 0, "tokens_by_model", 0, "expected"), 1.0), "float"),
    "tokens extra key": (_set(_entry_path("periods", 0, "tokens_by_model", 0, "p50"), 1), "keys"),
    "period block missing a key": (_pop(_entry_path("periods", 0, "tokens_by_model")), "keys"),
    "a float anywhere": (_set(["scopes", 0, "name"], 1.5), "float"),
}


@pytest.mark.parametrize("name", CORRUPTIONS)
def test_validate_export_fires_on_each_corruption(good_doc, name):
    change, fragment = CORRUPTIONS[name]
    bad = deepcopy(good_doc)
    change(bad)
    assert bad != good_doc
    with pytest.raises(ExecutionError, match=fragment) as caught:
        validate_export(bad)
    assert caught.value.stage == "export"


def test_validate_export_rejects_a_non_object():
    with pytest.raises(ExecutionError, match="object"):
        validate_export([])


# --- writing --------------------------------------------------------------------------------


def test_canonical_bytes_are_sorted_compact_utf8_with_a_newline():
    doc = {"b": 1, "a": ["ü", {"z": 0, "y": None}]}
    assert canonical_bytes(doc) == '{"a":["ü",{"y":null,"z":0}],"b":1}\n'.encode("utf-8")


def test_write_export_leaves_exactly_the_export(world, tmp_path):
    doc = world(team(series={"m": (ramp(), ONES)})).build()
    out = tmp_path / "write"
    out.mkdir()
    path = write_export(doc, out)
    assert path == out / "forecast_export.json"
    assert sorted(p.name for p in out.iterdir()) == ["forecast_export.json"]
    assert path.read_bytes() == canonical_bytes(doc)
    assert json.loads(path.read_text()) == doc


def test_the_export_gets_ordinary_file_permissions(world, tmp_path):
    doc = world(team(series={"m": (ramp(), ONES)})).build()
    (tmp_path / "plain").write_text("x")
    path = write_export(doc, tmp_path)
    assert path.stat().st_mode == (tmp_path / "plain").stat().st_mode


def test_a_failed_write_keeps_the_old_file_and_leaves_no_temporary(world, tmp_path, monkeypatch):
    doc = world(team(series={"m": (ramp(), ONES)})).build()
    out = tmp_path / "write"
    out.mkdir()
    (out / "forecast_export.json").write_text("old")

    def refuse(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(export_module.os, "replace", refuse)
    with pytest.raises(ExecutionError, match="No space left") as caught:
        write_export(doc, out)
    assert caught.value.stage == "export"
    assert (out / "forecast_export.json").read_text() == "old"
    assert [p.name for p in out.iterdir()] == ["forecast_export.json"]


def test_a_missing_output_directory_is_an_execution_error(world, tmp_path):
    doc = world(team(series={"m": (ramp(), ONES)})).build()
    with pytest.raises(ExecutionError, match="cannot write"):
        write_export(doc, tmp_path / "nowhere")


# --- a real run on the fixture dataset ------------------------------------------------------


@pytest.fixture(scope="module")
def real_doc(dashboard_run, dashboard_scopes, org_demo):
    return build_export(dashboard_run, dashboard_scopes, series_dir=org_demo / "series", now=NOW)


def test_real_export_shape(real_doc, dashboard_run):
    doc = real_doc
    validate_export(doc)
    assert doc["schema"] == "dashboard-forecast/1.0" and doc["generated_at"] == "2026-10-08T09:30:15Z"
    assert doc["timezone"] == "Asia/Kolkata"
    assert (doc["origin"], doc["data_through"]) == ("2026-09-30", "2026-10-15")
    assert doc["band"] == {"low": 10, "high": 90} and doc["quantile_grid"] == list(QUANTILE_GRID)
    assert doc["dates"][0] == "2026-10-01" and doc["dates"][-1] == "2026-11-30" and len(doc["dates"]) == 61
    assert doc["periods"] == [
        {"start": "2026-10-01", "end": "2026-10-31", "from": "2026-10-16"},
        {"start": "2026-11-01", "end": "2026-11-30", "from": "2026-11-01"},
    ]
    assert [(s["scope_type"], s["scope_id"], s["name"]) for s in doc["scopes"]] == [
        ("org", "default", "fixture.dev"),
        ("team", "alpha", "Alpha"),
        ("team", "beta", "Beta"),
        ("repo", "github:acme/checkout-flow", "acme/checkout-flow"),
        ("repo", "github:acme/search-index", "acme/search-index"),
        ("repo", "github:acme/billing-sync", "acme/billing-sync"),
    ]
    assert [p["id"] for p in doc["presets"]] == [p.id for p in dashboard_run.presets]
    assert doc["presets"][0]["group"] is None and doc["presets"][1]["group"] == "Model mix"
    for scope in doc["scopes"]:
        assert list(scope["series"]) == [p.id for p in dashboard_run.presets]
        for entry in scope["series"].values():
            assert entry["status"] == "complete"
            assert [len(p["cumulative"]["expected"]) for p in entry["periods"]] == [16, 30]  # Oct 16..31, Nov 1..30
            assert [m["model"] for m in entry["periods"][0]["tokens_by_model"]] == sorted(
                ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"]
            )


def test_real_export_has_no_floats(real_doc):
    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        else:
            assert not isinstance(value, float)
    walk(real_doc)


def test_real_presets_assumptions_are_the_union_over_the_cost_runs_scopes(real_doc, dashboard_run):
    for preset in real_doc["presets"]:
        index = json.loads((dashboard_run.cost_dirs[preset["id"]] / "paths" / "index.json").read_text())
        assert preset["assumptions"] == sorted({a for s in index["scopes"] for a in s["assumptions"]})
    by_id = {p["id"]: p["assumptions"] for p in real_doc["presets"]}
    assert "scenario_constant_tokens" in by_id["opus-to-sonnet-30"]
    assert "scenario_constant_tokens" not in by_id["baseline"]


def test_a_future_period_total_matches_the_cost_runs_own_period_summary(real_doc, dashboard_run):
    # November is entirely forecast, so the export's total is the cost run's period total.
    grid = real_doc["quantile_grid"]
    for preset in dashboard_run.presets:
        summary = load_cost_run(dashboard_run.cost_dirs[preset.id]).period_summary["entries"]
        for scope in real_doc["scopes"]:
            name = {"org": "fixture.dev"}.get(scope["scope_type"], scope["name"])
            scope_type = {"org": "organization", "repo": "project"}.get(scope["scope_type"], scope["scope_type"])
            if scope_type == "project":
                name = {"acme/checkout-flow": "checkout_flow", "acme/search-index": "search_index",
                        "acme/billing-sync": "billing_sync"}[scope["name"]]
            (entry,) = [
                e for e in summary
                if e["scope_type"] == scope_type and e["scope_name"] == name and e["model"] is None
                and e["period_type"] == "calendar" and e["start"] == "2026-11-01"
            ]
            total = scope["series"][preset.id]["periods"][1]["total"]
            assert total["expected"] == pytest.approx(entry["expected"] * 1e6, abs=1)
            for key, q in (("p10", 10), ("p50", 50), ("p90", 90)):
                assert total["quantiles"][grid.index(q)] == pytest.approx(entry[key] * 1e6, abs=1)


@pytest.fixture(scope="module")
def month_end_doc(month_end_series_dir, fast_template, pinned_catalog, org_demo, tmp_path_factory):
    result = run_dashboard_forecast(
        month_end_series_dir, DEFAULT_PRESETS_PATH, tmp_path_factory.mktemp("month-end") / "run",
        forecast_template=fast_template, catalog=pinned_catalog,
    )
    scopes = map_scopes(org_demo, result.scopes, org_id=result.org_id)
    return build_export(result, scopes, series_dir=month_end_series_dir, now=NOW)


def test_data_ending_on_a_month_end_makes_both_periods_future(month_end_doc):
    doc = month_end_doc
    assert doc["origin"] == doc["data_through"] == "2026-09-30"
    assert doc["periods"] == [
        {"start": "2026-10-01", "end": "2026-10-31", "from": "2026-10-01"},
        {"start": "2026-11-01", "end": "2026-11-30", "from": "2026-11-01"},
    ]
    for scope in doc["scopes"]:
        for entry in scope["series"].values():
            assert [len(p["cumulative"]["expected"]) for p in entry["periods"]] == [31, 30]  # the whole months
    validate_export(doc)


def test_export_forecast_writes_the_file_and_logs_its_events(dashboard_run, dashboard_scopes, org_demo, tmp_path):
    # `export_forecast` writes into the run's output directory: point a copy of the result at a scratch one.
    result = DashboardRunResult(
        data_through=dashboard_run.data_through, origin=dashboard_run.origin, org_id=dashboard_run.org_id,
        output_dir=tmp_path, forecast_dir=dashboard_run.forecast_dir, presets=dashboard_run.presets,
        cost_dirs=dashboard_run.cost_dirs, scopes=dashboard_run.scopes,
    )
    with structlog.testing.capture_logs() as logs:
        exported = export_forecast(result, dashboard_scopes, series_dir=org_demo / "series", now=NOW)
    assert exported.path == tmp_path / "forecast_export.json"
    assert exported.bytes == exported.path.stat().st_size == len(canonical_bytes(exported.document))
    assert json.loads(exported.path.read_text()) == exported.document
    events = [entry["event"] for entry in logs]
    assert events[0] == "dashboard.export.started" and events[-1] == "dashboard.export.completed"
    assert events.count("dashboard.export.scope_exported") == 6
    scope_events = [e for e in logs if e["event"] == "dashboard.export.scope_exported"]
    assert scope_events[1]["scope_id"] == "alpha" and scope_events[1]["preset_count"] == 7
    assert logs[-1]["bytes"] == exported.bytes and logs[-1]["path"] == str(exported.path)
