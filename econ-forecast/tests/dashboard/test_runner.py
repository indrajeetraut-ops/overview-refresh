"""Runner: origin rule, history floor, scope list and written forecast config, output
directory rules, error mapping, and one real end-to-end run on a small Layer 1 dataset."""

from datetime import date, timedelta
import json
import os
from pathlib import Path
import shutil

import pytest
import yaml

from forecast.cost import load_cost_run
from forecast.cost.errors import ExecutionError as CostExecutionError
from forecast.cost.errors import InputError as CostInputError
from forecast.dashboard import (
    DEFAULT_PRESETS_PATH,
    ExecutionError,
    InputError,
    RunScope,
    load_presets,
    resolve_origin,
    run_dashboard_forecast,
    series_scopes,
)
from forecast.dashboard import runner as runner_module
from forecast.forecasting.config import load_config
from forecast.forecasting import load_run
from forecast.forecasting.errors import ExecutionError as ForecastExecutionError
from forecast.forecasting.errors import InputError as ForecastInputError
from forecast.forecasting.runner import ForecastResult

FORECAST_TEMPLATE = runner_module.DEFAULT_FORECAST_TEMPLATE


# --- resolve_origin ---------------------------------------------------------


@pytest.mark.parametrize(
    "data_through, origin",
    [
        (date(2026, 10, 8), date(2026, 9, 30)),  # mid-month
        (date(2026, 10, 1), date(2026, 9, 30)),  # first of month
        (date(2026, 10, 31), date(2026, 10, 31)),  # month-end
        (date(2026, 1, 15), date(2025, 12, 31)),  # January
        (date(2026, 2, 28), date(2026, 2, 28)),  # February, non-leap month-end
        (date(2028, 2, 28), date(2028, 1, 31)),  # February, leap year, not month-end
        (date(2028, 2, 29), date(2028, 2, 29)),  # February, leap month-end
        (date(2026, 12, 31), date(2026, 12, 31)),  # year-end
    ],
)
def test_resolve_origin(data_through, origin):
    assert resolve_origin(data_through) == origin


# --- a stand-in series directory (manifest + config snapshot only) ----------


def _fake_series(root: Path, *, start: date, end: date, status="complete", operation="series") -> Path:
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "status": status,
                "operation": operation,
                "org_id": "acme.dev",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
        )
    )
    config = {
        "team_profiles": {"Development": {}, "Platform": {}},
        "projects": [{"name": "zeta_service"}, {"name": "alpha_service"}, {"name": "mid_service"}],
    }
    (root / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    return root


class _Stop(Exception):
    """Raised by the stub forecast so a test can inspect what was written before it."""


@pytest.fixture
def stub_forecast(monkeypatch):
    calls = []

    def stub(config_path, request, output_dir, **kwargs):
        calls.append((Path(config_path), request, Path(output_dir)))
        raise _Stop

    monkeypatch.setattr(runner_module, "run_forecast", stub)
    return calls


def _run(series, out, **kwargs):
    return run_dashboard_forecast(series, DEFAULT_PRESETS_PATH, out, **kwargs)


# --- manifest rules ---------------------------------------------------------


def test_missing_manifest_is_an_input_error(tmp_path):
    (tmp_path / "series").mkdir()
    with pytest.raises(InputError, match="manifest"):
        _run(tmp_path / "series", tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("kwargs, fragment", [({"status": "partial"}, "not complete"), ({"operation": "daily"}, "operation 'series'")])
def test_unusable_manifest_is_an_input_error(tmp_path, kwargs, fragment):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8), **kwargs)
    with pytest.raises(InputError, match=fragment):
        _run(series, tmp_path / "out")
    assert not (tmp_path / "out").exists()


# --- history floor ----------------------------------------------------------


def test_fewer_than_28_days_before_the_origin_is_an_input_error(tmp_path, stub_forecast):
    # data through 2026-10-08 -> origin 2026-09-30; starting 09-04 leaves 27 days.
    series = _fake_series(tmp_path / "series", start=date(2026, 9, 4), end=date(2026, 10, 8))
    with pytest.raises(InputError) as caught:
        _run(series, tmp_path / "out")
    assert "27" in str(caught.value) and "28" in str(caught.value)
    assert not (tmp_path / "out").exists()
    assert stub_forecast == []


def test_exactly_28_days_before_the_origin_is_enough(tmp_path, stub_forecast):
    series = _fake_series(tmp_path / "series", start=date(2026, 9, 3), end=date(2026, 10, 8))
    with pytest.raises(_Stop):  # past the floor; the stub forecast then stops the run
        _run(series, tmp_path / "out")
    assert len(stub_forecast) == 1


def test_data_starting_after_the_origin_is_an_input_error(tmp_path):
    series = _fake_series(tmp_path / "series", start=date(2026, 10, 5), end=date(2026, 10, 8))
    with pytest.raises(InputError, match="28"):
        _run(series, tmp_path / "out")


def test_series_scopes_lists_the_scopes_a_run_will_forecast(tmp_path):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    assert series_scopes(series) == (
        RunScope("organization", "acme.dev"),
        RunScope("team", "Development"),
        RunScope("team", "Platform"),
        RunScope("project", "zeta_service"),
        RunScope("project", "alpha_service"),
        RunScope("project", "mid_service"),
    )
    with pytest.raises(InputError, match="manifest"):
        series_scopes(tmp_path / "nowhere")


# --- scope list and written forecast.yaml -----------------------------------


def test_forecast_yaml_has_every_scope_and_keeps_the_template(tmp_path, stub_forecast):
    template = yaml.safe_load(FORECAST_TEMPLATE.read_text())
    template["uncertainty"]["paths"] = 123
    template["history"]["lookback_days"] = 60
    custom = tmp_path / "template.yaml"
    custom.write_text(yaml.safe_dump(template))
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    with pytest.raises(_Stop):
        _run(series, tmp_path / "out", forecast_template=custom)

    written = yaml.safe_load((tmp_path / "out" / "forecast.yaml").read_text())
    assert written["scopes"] == [
        {"type": "organization"},
        {"type": "team", "name": "Development"},
        {"type": "team", "name": "Platform"},
        {"type": "project", "name": "zeta_service"},
        {"type": "project", "name": "alpha_service"},
        {"type": "project", "name": "mid_service"},
    ]
    assert written["input"]["dataset_dir"] == str(series.resolve())
    assert written["default_org_id"] == "acme.dev"
    for key in ("input", "default_org_id", "scopes"):
        written.pop(key), template.pop(key)
    assert written == template  # every other key as in the template, including the edited ones
    assert written["uncertainty"]["paths"] == 123

    config_path, request, forecast_dir = stub_forecast[0]
    assert config_path == (tmp_path / "out" / "forecast.yaml").resolve()
    assert forecast_dir == (tmp_path / "out" / "forecast").resolve()
    assert (request.origin, request.unit, request.offset, request.periods) == (date(2026, 9, 30), "month", 1, 2)


def test_default_template_is_found_from_the_package_not_the_cwd(tmp_path, stub_forecast, monkeypatch):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(_Stop):
        _run(series, tmp_path / "out")
    written = yaml.safe_load((tmp_path / "out" / "forecast.yaml").read_text())
    assert written["model"] == yaml.safe_load(FORECAST_TEMPLATE.read_text())["model"]


# --- output directory rules -------------------------------------------------


def test_existing_empty_output_dir_is_fine(tmp_path, stub_forecast):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    (tmp_path / "out").mkdir()
    with pytest.raises(_Stop):
        _run(series, tmp_path / "out")
    assert len(stub_forecast) == 1


def test_non_empty_output_dir_is_refused_without_force(tmp_path, stub_forecast):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    out = tmp_path / "out"
    out.mkdir()
    (out / "something.txt").write_text("x")
    with pytest.raises(InputError, match="not empty"):
        _run(series, out)
    assert [p.name for p in out.iterdir()] == ["something.txt"]
    assert stub_forecast == []


def test_output_path_that_is_a_file_is_refused(tmp_path):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    (tmp_path / "out").write_text("x")
    with pytest.raises(InputError):
        _run(series, tmp_path / "out", force=True)


def test_force_removes_only_the_known_entries(tmp_path, stub_forecast):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    out = tmp_path / "out"
    for sub in ("scenarios", "forecast", "cost/baseline"):
        (out / sub).mkdir(parents=True)
        (out / sub / "old.txt").write_text("old")
    (out / "forecast.yaml").write_text("old")
    (out / "forecast_export.json").write_text("old")
    (out / "notes.txt").write_text("keep")
    (out / "other").mkdir()
    (out / "other" / "a.txt").write_text("keep")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep")
    (out / "scenarios" / "link").symlink_to(outside)

    with pytest.raises(_Stop):
        _run(series, out, force=True)

    assert (out / "notes.txt").read_text() == "keep"
    assert (out / "other" / "a.txt").read_text() == "keep"
    assert not (out / "scenarios").exists() and not (out / "forecast").exists() and not (out / "cost").exists()
    assert (out / "forecast.yaml").read_text() != "old"  # rewritten for the new run
    assert not (out / "forecast_export.json").exists()  # a stale export goes with its run
    assert (outside / "precious.txt").read_text() == "keep"


def test_force_removes_symlinked_entries_without_following_them(tmp_path, stub_forecast):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    out = tmp_path / "out"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep")
    out.mkdir()
    (out / "cost").symlink_to(outside, target_is_directory=True)
    (out / "notes.txt").write_text("keep")
    with pytest.raises(_Stop):
        _run(series, out, force=True)
    assert not os.path.lexists(out / "cost")
    assert (outside / "precious.txt").read_text() == "keep"


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("where", ["equal", "contains", "inside"])
def test_output_must_be_separate_from_the_series_dir(tmp_path, stub_forecast, where, force):
    series = _fake_series(tmp_path / "base" / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    out = {"equal": series, "contains": tmp_path / "base", "inside": series / "forecast-out"}[where]
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    with pytest.raises(InputError, match="separate"):
        _run(series, out, force=force)
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    assert stub_forecast == []


# --- unusable output locations ----------------------------------------------

needs_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="permission bits do not bind root"
)


@pytest.fixture
def fake_series(tmp_path):
    return _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))


def test_output_under_a_file_is_an_input_error(tmp_path, fake_series):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.raises(InputError, match="not a directory"):
        _run(fake_series, blocker / "out")
    with pytest.raises(InputError, match="not a directory"):
        _run(fake_series, blocker / "deeper" / "out", force=True)


@needs_non_root
def test_output_under_a_read_only_directory_is_an_execution_error(tmp_path, fake_series):
    parent = tmp_path / "readonly"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        with pytest.raises(ExecutionError, match="cannot write"):
            _run(fake_series, parent / "out")
    finally:
        parent.chmod(0o755)
    assert not (parent / "out").exists()


@needs_non_root
def test_an_unreadable_existing_output_dir_is_an_input_error(tmp_path, fake_series):
    out = tmp_path / "out"
    out.mkdir()
    out.chmod(0o000)
    try:
        with pytest.raises(InputError, match="cannot inspect"):
            _run(fake_series, out)
        with pytest.raises(InputError, match="cannot inspect"):
            _run(fake_series, out, force=True)
    finally:
        out.chmod(0o755)


@needs_non_root
def test_force_on_a_read_only_output_dir_is_an_input_error(tmp_path, fake_series):
    out = tmp_path / "out"
    (out / "forecast").mkdir(parents=True)
    (out / "forecast" / "old.txt").write_text("old")
    out.chmod(0o555)
    try:
        with pytest.raises(InputError, match="cannot clear"):
            _run(fake_series, out, force=True)
    finally:
        out.chmod(0o755)


def _is_case_insensitive(directory: Path) -> bool:
    probe = directory / "CaseProbe"
    probe.mkdir()
    try:
        return (directory / "caseprobe").exists()
    finally:
        probe.rmdir()


@pytest.mark.parametrize("where", ["equal", "inside"])
def test_output_is_compared_to_the_series_dir_by_identity(tmp_path, stub_forecast, where):
    if not _is_case_insensitive(tmp_path):
        pytest.skip("filesystem is case-sensitive; a differently cased path is a different directory")
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    alias = tmp_path / "SERIES"
    assert str(alias) != str(series) and alias.samefile(series)
    out = alias if where == "equal" else alias / "out"
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    with pytest.raises(InputError, match="separate"):
        _run(series, out, force=True)
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    assert stub_forecast == []


def test_a_bad_template_never_costs_the_previous_run(tmp_path, stub_forecast):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    out = tmp_path / "out"
    for sub in ("scenarios", "forecast", "cost/baseline"):
        (out / sub).mkdir(parents=True)
        (out / sub / "old.txt").write_text("old")
    (out / "forecast.yaml").write_text("old")
    with pytest.raises(InputError, match="forecast template"):
        _run(series, out, force=True, forecast_template=tmp_path / "missing.yaml")
    assert stub_forecast == []
    assert (out / "forecast.yaml").read_text() == "old"
    for sub in ("scenarios", "forecast", "cost/baseline"):
        assert (out / sub / "old.txt").read_text() == "old"


def test_the_previous_runs_own_forecast_yaml_can_be_the_template(tmp_path, stub_forecast):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    out = tmp_path / "out"
    out.mkdir()
    shutil.copy(FORECAST_TEMPLATE, out / "forecast.yaml")
    with pytest.raises(_Stop):
        _run(series, out, force=True, forecast_template=out / "forecast.yaml")
    assert yaml.safe_load((out / "forecast.yaml").read_text())["default_org_id"] == "acme.dev"


# --- stage errors -----------------------------------------------------------


def _partial(rejected=1):
    return ForecastResult("partial", 2, 3, 3 - rejected, rejected, Path("/nowhere"), None, {}, "2026-09-30")


def test_forecast_errors_keep_their_category_and_name_the_stage(tmp_path, monkeypatch):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    monkeypatch.setattr(runner_module, "run_forecast", lambda *a, **k: (_ for _ in ()).throw(ForecastInputError("bad scope")))
    with pytest.raises(InputError, match="bad scope") as caught:
        _run(series, tmp_path / "out1")
    assert caught.value.stage == "forecast"
    monkeypatch.setattr(runner_module, "run_forecast", lambda *a, **k: (_ for _ in ()).throw(ForecastExecutionError("disk full")))
    with pytest.raises(ExecutionError, match="disk full") as caught:
        _run(series, tmp_path / "out2")
    assert caught.value.stage == "forecast"


def test_a_filesystem_error_inside_a_stage_is_an_execution_error(tmp_path, monkeypatch):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    monkeypatch.setattr(runner_module, "run_forecast", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "Permission denied")))
    with pytest.raises(ExecutionError, match="Permission denied") as caught:
        _run(series, tmp_path / "out1")
    assert caught.value.stage == "forecast"
    monkeypatch.setattr(runner_module, "run_forecast", lambda *a, **k: ForecastResult("complete", 0, 6, 6, 0, None, None, {}, "2026-09-30"))
    monkeypatch.setattr(runner_module, "run_cost", lambda *a, **k: (_ for _ in ()).throw(OSError(28, "No space left on device")))
    with pytest.raises(ExecutionError, match="'baseline'.*No space left") as caught:
        _run(series, tmp_path / "out2")
    assert caught.value.stage == "cost"


def test_a_partial_forecast_is_an_execution_error(tmp_path, monkeypatch):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    monkeypatch.setattr(runner_module, "run_forecast", lambda *a, **k: _partial())
    with pytest.raises(ExecutionError, match="1 rejected") as caught:
        _run(series, tmp_path / "out")
    assert caught.value.stage == "forecast"


def test_cost_errors_name_the_preset_and_stage(tmp_path, monkeypatch):
    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    monkeypatch.setattr(runner_module, "run_forecast", lambda *a, **k: ForecastResult("complete", 0, 6, 6, 0, None, None, {}, "2026-09-30"))
    monkeypatch.setattr(runner_module, "run_cost", lambda *a, **k: (_ for _ in ()).throw(CostInputError("model_transfer.from is not a series")))
    with pytest.raises(InputError, match="'baseline'.*model_transfer.from") as caught:
        _run(series, tmp_path / "out")
    assert caught.value.stage == "cost"
    monkeypatch.setattr(runner_module, "run_cost", lambda *a, **k: (_ for _ in ()).throw(CostExecutionError("pricing unavailable")))
    with pytest.raises(ExecutionError, match="pricing unavailable") as caught:
        _run(series, tmp_path / "out", force=True)
    assert caught.value.stage == "cost"


@pytest.mark.parametrize(
    "result, fragment",
    [
        ({"status": "incomplete", "exit_code": 2, "scopes": [], "unpriced_models": []}, "incomplete"),
        ({"status": "complete", "exit_code": 0, "scopes": [], "unpriced_models": ["m-x"]}, "m-x"),
        (
            {
                "status": "complete",
                "exit_code": 0,
                "scopes": [{"scope_type": "team", "scope_name": "QA", "status": "conditional"}],
                "unpriced_models": [],
            },
            "QA",
        ),
    ],
)
def test_a_cost_run_that_is_not_fully_complete_is_an_execution_error(tmp_path, monkeypatch, result, fragment):
    from forecast.cost.runner import CostResult

    series = _fake_series(tmp_path / "series", start=date(2026, 6, 1), end=date(2026, 10, 8))
    monkeypatch.setattr(runner_module, "run_forecast", lambda *a, **k: ForecastResult("complete", 0, 6, 6, 0, None, None, {}, "2026-09-30"))
    monkeypatch.setattr(
        runner_module, "run_cost", lambda *a, **k: CostResult(scenario="baseline", output_dir=None, **result)
    )
    with pytest.raises(ExecutionError, match=fragment) as caught:
        _run(series, tmp_path / "out")
    assert caught.value.stage == "cost" and "'baseline'" in str(caught.value)


# --- one real end-to-end run ------------------------------------------------

def test_end_to_end_result(dashboard_run, series_dir):
    result = dashboard_run
    assert result.data_through == date(2026, 10, 15)
    assert result.origin == date(2026, 9, 30)
    assert result.org_id == "fixture.dev"
    assert result.forecast_dir == result.output_dir / "forecast"
    assert [p.id for p in result.presets] == [p.id for p in load_presets(DEFAULT_PRESETS_PATH)]
    assert [(s.scope_type, s.name) for s in result.scopes] == [
        ("organization", "fixture.dev"),
        ("team", "Alpha"),
        ("team", "Beta"),
        ("project", "checkout_flow"),
        ("project", "search_index"),
        ("project", "billing_sync"),
    ]
    assert list(result.cost_dirs) == [p.id for p in result.presets]
    assert sorted(p.name for p in result.output_dir.iterdir()) == ["cost", "forecast", "forecast.yaml", "scenarios"]
    config = load_config(result.output_dir / "forecast.yaml")
    assert config.input.dataset_dir == series_dir.resolve() and config.uncertainty.paths == 100


def test_end_to_end_forecast_is_complete(dashboard_run):
    request = json.loads((dashboard_run.forecast_dir / "request.json").read_text())
    assert request["origin"] == "2026-09-30"
    assert request["requested_start"] == "2026-10-01" and request["requested_end"] == "2026-11-30"
    manifest = json.loads((dashboard_run.forecast_dir / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert manifest["counts"] == {"requested_scopes": 6, "completed_scopes": 6, "rejected_scopes": 0}


def test_end_to_end_every_preset_has_a_complete_cost_run(dashboard_run):
    for preset in dashboard_run.presets:
        cost_dir = dashboard_run.cost_dirs[preset.id]
        assert cost_dir == dashboard_run.output_dir / "cost" / preset.id and cost_dir.is_dir()
        run = load_cost_run(cost_dir)
        assert run.manifest["status"] == "complete"
        assert run.request["scenario"] == preset.id
        assert len(run.index["scopes"]) == 6
        assert {s["status"] for s in run.index["scopes"]} == {"complete"}
    assert len(list((dashboard_run.output_dir / "cost").iterdir())) == 7


def test_end_to_end_scenario_files_take_effect_the_day_after_the_data_ends(dashboard_run):
    scenarios = dashboard_run.output_dir / "scenarios"
    assert sorted(p.name for p in scenarios.iterdir()) == sorted(
        f"{p.id}.yaml" for p in dashboard_run.presets if not p.is_baseline
    )
    assert len(list(scenarios.iterdir())) == 6
    expected = dashboard_run.data_through + timedelta(days=1)
    for path in scenarios.iterdir():
        doc = yaml.safe_load(path.read_text())
        assert doc["name"] == path.stem and doc["scope"] == {"type": "organization"}
        for change in doc["changes"]:
            (body,) = change.values()
            assert body["effective_from"] == expected == date(2026, 10, 16)
    assert not (scenarios / "baseline.yaml").exists()


def _org_total(result, preset_id, period_start):
    run = load_cost_run(result.cost_dirs[preset_id])
    (entry,) = [
        e for e in run.period_summary["entries"]
        if e["scope_type"] == "organization" and e["model"] is None
        and e["period_type"] == "calendar" and e["start"] == period_start
    ]
    return entry["expected"]


def _org_model_usd(result, preset_id, model, month_prefix):
    """Expected USD of one model at organization scope over one month, from the cost run's
    daily rows (so it is independent of the period summary)."""
    run = load_cost_run(result.cost_dirs[preset_id])
    return sum(
        sum(row["usd"].values())
        for row in run.daily_expected
        if row["scope_type"] == "organization" and row["model"] == model
        and row["date"].startswith(month_prefix)
    )


def test_end_to_end_scenarios_move_the_org_total_the_expected_way(dashboard_run):
    total = lambda preset, start="2026-10-01": _org_total(dashboard_run, preset, start)  # noqa: E731
    baseline = total("baseline")
    assert total("usage-up-20") > baseline > total("usage-down-15")
    assert total("opus-to-sonnet-60") < total("opus-to-sonnet-30") < baseline
    assert total("sonnet-to-haiku-30") < baseline
    # Growth on top of a saving still lands below plain growth.
    assert total("usage-up-20-opus-to-sonnet-50") < total("usage-up-20")


def test_end_to_end_november_figures_follow_from_the_prices(dashboard_run):
    """November is entirely after `effective_from`, so each preset's effect can be worked
    out from the baseline. Growth scales every token (exact). A transfer re-prices the
    moved share (sonnet is opus / 5 and haiku is sonnet / 3 at every token type); the
    moved tokens also take the destination model's own token-type mix, so the saving is
    checked to 2% of the saving rather than exactly (measured: 0.3%)."""
    total = lambda preset: _org_total(dashboard_run, preset, "2026-11-01")  # noqa: E731
    baseline = total("baseline")
    assert total("usage-up-20") == pytest.approx(1.2 * baseline, rel=1e-6)
    assert total("usage-down-15") == pytest.approx(0.85 * baseline, rel=1e-6)
    opus = _org_model_usd(dashboard_run, "baseline", "claude-opus-5", "2026-11")
    sonnet = _org_model_usd(dashboard_run, "baseline", "claude-sonnet-5", "2026-11")
    assert opus > 0 and sonnet > 0
    approx_saving = lambda expected: pytest.approx(expected, rel=2e-2)  # noqa: E731
    assert baseline - total("opus-to-sonnet-30") == approx_saving(0.3 * opus * 4 / 5)
    assert baseline - total("opus-to-sonnet-60") == approx_saving(0.6 * opus * 4 / 5)
    assert baseline - total("sonnet-to-haiku-30") == approx_saving(0.3 * sonnet * 2 / 3)
    # Growth first, then half of the grown Opus tokens move: 1.2 x the 50% saving.
    assert total("usage-up-20") - total("usage-up-20-opus-to-sonnet-50") == approx_saving(1.2 * 0.5 * opus * 4 / 5)


def test_end_to_end_rerun_needs_force(dashboard_run, series_dir, fast_template, pinned_catalog):
    with pytest.raises(InputError, match="not empty"):
        run_dashboard_forecast(
            series_dir, DEFAULT_PRESETS_PATH, dashboard_run.output_dir,
            forecast_template=fast_template, catalog=pinned_catalog,
        )


# --- a partial forecast names its rejected scopes ------------------------------


def test_a_partial_forecast_names_the_rejected_scopes_and_their_reasons(
    series_dir, fast_template, pinned_catalog, tmp_path
):
    # 60 covered days up to the origin cannot satisfy an 80-day minimum, so every scope is
    # rejected; the forecast run itself is real and `load_run` supplies the names.
    template = yaml.safe_load(fast_template.read_text())
    template["history"]["minimum_days"] = 80
    strict = tmp_path / "strict.yaml"
    strict.write_text(yaml.safe_dump(template))
    out = tmp_path / "out"
    with pytest.raises(ExecutionError) as caught:
        run_dashboard_forecast(
            series_dir, DEFAULT_PRESETS_PATH, out, forecast_template=strict, catalog=pinned_catalog
        )
    error = caught.value
    assert error.stage == "forecast"
    message = str(error)
    assert "6 rejected" in message
    for name in ("'fixture.dev'", "'Alpha'", "'Beta'", "'checkout_flow'", "'search_index'", "'billing_sync'"):
        assert name in message
    saved = load_run(out / "forecast")
    reasons = {r for scope in saved.index["scopes"] for r in scope["reasons"]}
    assert reasons
    for reason in reasons:
        assert reason in message
    # Each scope has three series that repeat one reason; the message states it once per scope.
    scope = saved.index["scopes"][0]
    assert len(scope["reasons"]) > len(set(scope["reasons"]))
    for reason in reasons:
        assert message.count(reason) == len([s for s in saved.index["scopes"] if reason in s["reasons"]])
    assert not (out / "cost").exists()


# --- data ending on a month end ------------------------------------------------


def test_end_to_end_with_data_ending_on_a_month_end(month_end_series_dir, fast_template, pinned_catalog, tmp_path):
    result = run_dashboard_forecast(
        month_end_series_dir, DEFAULT_PRESETS_PATH, tmp_path / "run",
        forecast_template=fast_template, catalog=pinned_catalog,
    )
    assert result.data_through == result.origin == date(2026, 9, 30)
    request = json.loads((result.forecast_dir / "request.json").read_text())
    assert request["origin"] == "2026-09-30" and request["requested_start"] == "2026-10-01"
    first_forecast_date = date.fromisoformat(request["requested_start"])
    for preset in result.presets:
        run = load_cost_run(result.cost_dirs[preset.id])
        assert {s["status"] for s in run.index["scopes"]} == {"complete"}
        assert len(run.index["scopes"]) == 6
    scenarios = sorted((result.output_dir / "scenarios").iterdir())
    assert len(scenarios) == 6
    for path in scenarios:
        for change in yaml.safe_load(path.read_text())["changes"]:
            (body,) = change.values()
            assert body["effective_from"] == first_forecast_date == result.data_through + timedelta(days=1)
