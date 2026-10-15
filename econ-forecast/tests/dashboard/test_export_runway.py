"""Export against the raw run: per-date cumulative grids (runway, plan §3.8) and tokens per
model checked on a real run with 1000 sample paths, using figures the test works out itself
from the cost run's `.npy` files and `daily_expected.jsonl`."""

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from forecast.dashboard import DEFAULT_PRESETS_PATH, build_export, map_scopes, run_dashboard_forecast
from forecast.forecasting import load_run

NOW = datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc)
GRID = list(range(0, 101, 5))
RUN_TYPE = {"org": "organization", "team": "team", "repo": "project"}
MODEL_TRANSFER_IDS = ["opus-to-sonnet-30", "opus-to-sonnet-60", "sonnet-to-haiku-30"]


@pytest.fixture(scope="module")
def run_1000(series_dir, fast_template, pinned_catalog, org_demo, tmp_path_factory):
    template = yaml.safe_load(fast_template.read_text())
    template["uncertainty"]["paths"] = 1000
    path = tmp_path_factory.mktemp("template-1000") / "forecast.yaml"
    path.write_text(yaml.safe_dump(template))
    result = run_dashboard_forecast(
        series_dir, DEFAULT_PRESETS_PATH, tmp_path_factory.mktemp("run-1000") / "run",
        forecast_template=path, catalog=pinned_catalog,
    )
    scopes = map_scopes(org_demo, result.scopes, org_id=result.org_id)
    return result, build_export(result, scopes, series_dir=org_demo / "series", now=NOW)


# --- reading the raw run, independently of the builder ---------------------------------------


def raw_index(result, preset_id):
    return json.loads((result.cost_dirs[preset_id] / "paths" / "index.json").read_text())


def raw_series_paths(result, preset_id, scope_type, scope_name):
    """{model: [date, sample] array} read straight from the `.npy` files."""
    index = raw_index(result, preset_id)
    return {
        e["model"]: np.load(result.cost_dirs[preset_id] / e["path"])
        for e in index["series"]
        if e["scope_type"] == scope_type and e["scope_name"] == scope_name and e["path"]
    }


def raw_rates(result, preset_id):
    """{(scope_type, scope_name, model): rate per date} from `daily_expected.jsonl`."""
    index = raw_index(result, preset_id)
    position = {d: i for i, d in enumerate(index["dates"])}
    usd, tokens = defaultdict(lambda: np.zeros(len(position))), defaultdict(lambda: np.zeros(len(position)))
    for line in (result.cost_dirs[preset_id] / "daily_expected.jsonl").read_text().splitlines():
        row = json.loads(line)
        key = (row["scope_type"], row["scope_name"], row["model"])
        usd[key][position[row["date"]]] += sum(row["usd"].values())
        tokens[key][position[row["date"]]] += sum(row["tokens"].values())
    return {key: usd[key] / tokens[key] for key in tokens}


def spans(doc):
    dates = doc["dates"]
    return [(dates.index(p["from"]), dates.index(p["end"])) for p in doc["periods"]]


def each_scope_and_preset(result, doc):
    for scope, run_scope in zip(doc["scopes"], result.scopes):
        for preset_id, entry in scope["series"].items():
            yield scope, run_scope, preset_id, entry


# --- the grid ------------------------------------------------------------------------------


def test_grids_never_decrease_across_quantiles_or_across_dates(run_1000):
    _, doc = run_1000
    checked = 0
    for scope, _, preset_id, entry in each_scope_and_preset(*run_1000):
        for period in entry["periods"]:
            rows = np.array(period["cumulative"]["quantiles"])
            assert rows.shape[1] == 21
            assert np.all(np.diff(rows, axis=1) >= 0), (scope["scope_id"], preset_id)
            assert np.all(np.diff(rows, axis=0) >= 0), (scope["scope_id"], preset_id)
            assert np.all(np.diff(period["cumulative"]["expected"]) >= 0)
            checked += rows.size
    assert checked == 6 * 7 * 21 * (16 + 30)


def test_grid_values_equal_the_quantiles_of_the_summed_raw_paths(run_1000):
    result, doc = run_1000
    grid = np.array(GRID) / 100
    for scope, run_scope, preset_id, entry in each_scope_and_preset(*run_1000):
        paths = raw_series_paths(result, preset_id, run_scope.scope_type, run_scope.name)
        usd = sum(paths.values())
        assert np.array_equal(
            np.rint(np.quantile(usd, [0.1, 0.9], axis=1) * 1e6).astype(int)[0], entry["daily"]["low"]
        )
        for (lo, hi), period in zip(spans(doc), entry["periods"]):
            cumulative = np.cumsum(usd[lo : hi + 1], axis=0)
            expected = np.rint(np.quantile(cumulative, grid, axis=1) * 1e6).astype(int).T
            assert np.array_equal(expected, np.array(period["cumulative"]["quantiles"]))
            assert period["total"]["quantiles"] == expected[-1].tolist()
            assert period["total"]["expected"] == int(np.rint(cumulative[-1].mean() * 1e6))


def p_within(values, threshold):
    """P(total <= threshold) from a quantile grid: linear between grid points, 0 below, 1 above."""
    return float(np.interp(threshold, values, np.array(GRID) / 100, left=0.0, right=1.0))


def test_the_plan_worked_example_of_the_budget_probability():
    values = [1000 + 50 * k for k in range(21)]  # 1000, 1050, ... 2000
    spent = 200
    assert p_within(values, 1700 - spent) == pytest.approx(0.50)
    assert p_within(values, 1525) == pytest.approx(0.525)
    assert p_within(values, 900) == 0.0
    assert p_within(values, 2100) == 1.0


def test_probability_of_running_out_by_each_date_matches_the_paths(run_1000):
    result, doc = run_1000
    worst = 0.0
    cases = 0
    for scope, run_scope, preset_id, entry in each_scope_and_preset(*run_1000):
        usd = sum(raw_series_paths(result, preset_id, run_scope.scope_type, run_scope.name).values())
        for (lo, hi), period in zip(spans(doc), entry["periods"]):
            cumulative = np.cumsum(usd[lo : hi + 1], axis=0) * 1e6  # micro-dollars, [date, sample]
            total = cumulative[-1]
            # What is left to spend: below the 10th percentile of the total, near its median, above its 90th.
            for left in (np.quantile(total, 0.05), np.quantile(total, 0.5), np.quantile(total, 0.95)):
                for day, row in enumerate(period["cumulative"]["quantiles"]):
                    empirical = float(np.mean(cumulative[day] > left))
                    from_grid = 1.0 - p_within(row, left)
                    worst = max(worst, abs(empirical - from_grid))
                    cases += 1
    assert cases == 6 * 7 * 3 * (16 + 30)
    assert worst <= 0.05, worst


# --- tokens per model --------------------------------------------------------------------------


def test_tokens_by_model_equal_the_usd_paths_divided_by_the_daily_rate(run_1000):
    result, doc = run_1000
    rates = {preset_id: raw_rates(result, preset_id) for preset_id in [p.id for p in result.presets]}
    for scope, run_scope, preset_id, entry in each_scope_and_preset(*run_1000):
        paths = raw_series_paths(result, preset_id, run_scope.scope_type, run_scope.name)
        for (lo, hi), period in zip(spans(doc), entry["periods"]):
            assert [m["model"] for m in period["tokens_by_model"]] == sorted(paths)
            for item in period["tokens_by_model"]:
                rate = rates[preset_id][(run_scope.scope_type, run_scope.name, item["model"])]
                per_sample = (paths[item["model"]][lo : hi + 1] / rate[lo : hi + 1, None]).sum(axis=0)
                assert item["expected"] == pytest.approx(per_sample.mean(), abs=1)
                assert item["p90"] == pytest.approx(np.quantile(per_sample, 0.9), abs=1)
                assert item["expected"] > 0


def test_baseline_tokens_equal_the_forecast_runs_own_token_paths(run_1000):
    result, doc = run_1000
    forecast = load_run(result.forecast_dir)
    entries = {(e["scope_type"], e["scope_name"], e["model"]): e for e in forecast.index["series"]}
    for scope, run_scope, preset_id, entry in each_scope_and_preset(*run_1000):
        if preset_id != "baseline":
            continue
        for (lo, hi), period in zip(spans(doc), entry["periods"]):
            for item in period["tokens_by_model"]:
                tokens = np.asarray(forecast.load_paths(entries[(run_scope.scope_type, run_scope.name, item["model"])]))
                per_sample = tokens[lo : hi + 1].sum(axis=0)
                assert item["expected"] == pytest.approx(per_sample.mean(), abs=1)
                assert item["p90"] == pytest.approx(np.quantile(per_sample, 0.9), abs=1)


def _token_sum(period):
    return sum(m["expected"] for m in period["tokens_by_model"]), len(period["tokens_by_model"])


@pytest.mark.parametrize("preset_id", MODEL_TRANSFER_IDS)
def test_a_constant_token_model_move_keeps_the_token_total_of_baseline(run_1000, preset_id):
    _, doc = run_1000
    for scope in doc["scopes"]:
        for index in (0, 1):
            base, base_models = _token_sum(scope["series"]["baseline"]["periods"][index])
            moved, moved_models = _token_sum(scope["series"][preset_id]["periods"][index])
            # Each model's expected is rounded to a whole token: half a token per model at most.
            tolerance = 0.5 * (base_models + moved_models) + 1e-3
            assert abs(moved - base) <= tolerance, (scope["scope_id"], index, moved - base)


def test_growth_scales_the_token_total_of_a_future_period(run_1000):
    _, doc = run_1000
    for scope in doc["scopes"]:
        base, _ = _token_sum(scope["series"]["baseline"]["periods"][1])
        for preset_id, factor in (("usage-up-20", 1.2), ("usage-down-15", 0.85)):
            scaled, models = _token_sum(scope["series"][preset_id]["periods"][1])
            # Rounding to whole tokens: half a token per model in the scaled total, and the
            # baseline's half token per model is scaled by the factor too.
            tolerance = 0.5 * models + factor * 0.5 * models + 1e-3
            assert abs(scaled - factor * base) <= tolerance, (scope["scope_id"], preset_id)


# --- how the presets order -----------------------------------------------------------------------


def test_presets_order_the_totals_the_expected_way(run_1000):
    _, doc = run_1000
    for scope in doc["scopes"]:
        for index in (0, 1):
            total = lambda preset_id: scope["series"][preset_id]["periods"][index]["total"]["expected"]  # noqa: E731
            where = (scope["scope_id"], index)
            assert total("usage-up-20") > total("baseline") > total("opus-to-sonnet-30"), where
            assert total("opus-to-sonnet-30") > total("opus-to-sonnet-60"), where
            assert total("baseline") > total("usage-down-15"), where
            assert total("baseline") > total("sonnet-to-haiku-30"), where
            assert total("usage-up-20") > total("usage-up-20-opus-to-sonnet-50"), where
