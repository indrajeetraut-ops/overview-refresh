"""Integration oracles for replay, one-path shape, history states, and cleanup."""

from datetime import date
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import numpy as np
import pytest
import yaml
from forecast.forecasting.artifacts import load_run
from forecast.forecasting.dataset import load_dataset
from forecast.forecasting.errors import ExecutionError, InputError
from forecast.forecasting.request import ForecastRequest
from forecast.forecasting.runner import run_forecast

_fixture_path = Path(__file__).parents[1] / "acceptance" / "conftest.py"
_spec = importlib.util.spec_from_file_location(
    "_forecast_acceptance_fixtures", _fixture_path
)
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)


@pytest.fixture
def daily_dataset_factory():
    return _fixtures.make_daily_dataset


@pytest.fixture
def forecast_config_factory():
    return _fixtures.write_forecast_config


@pytest.fixture
def saved_run_factory():
    return _fixtures.make_saved_run


def _arrays(root):
    run = load_run(root)
    return {
        (e["scope_type"], e["scope_id"], e["model"]): np.array(run.load_paths(e))
        for e in run.index["series"]
        if e["path"]
    }


def test_scope_order_and_worker_count_preserve_actual_saved_arrays(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(
        tmp_path / "daily", token_fn=lambda d, m, u: (100 + d % 7, 300, 50)
    )
    scopes = [{"type": "organization"}, {"type": "team", "name": "Team A"}]
    one = forecast_config_factory(
        tmp_path / "one.yaml",
        dataset,
        scopes=scopes,
        runtime={"workers": 1},
        uncertainty={"paths": 12, "seed": 19},
    )
    two = forecast_config_factory(
        tmp_path / "two.yaml",
        dataset,
        scopes=list(reversed(scopes)),
        runtime={"workers": 2},
        uncertainty={"paths": 12, "seed": 19},
    )
    run_forecast(one, ForecastRequest(date(2026, 8, 29), "month"), tmp_path / "out-one")
    run_forecast(two, ForecastRequest(date(2026, 8, 29), "month"), tmp_path / "out-two")
    left, right = _arrays(tmp_path / "out-one"), _arrays(tmp_path / "out-two")
    assert left.keys() == right.keys()
    for key in left:
        np.testing.assert_allclose(left[key], right[key], rtol=1e-10, atol=1e-7)


@pytest.mark.parametrize(
    "token_fn", [lambda d, m, u: (500, 0, 0), lambda d, m, u: (500 + d % 3, 0, 0)]
)
def test_end_to_end_single_path_axis_for_constant_and_varying(
    token_fn, tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(tmp_path / "daily", token_fn=token_fn)
    config = forecast_config_factory(
        tmp_path / "one-path.yaml",
        dataset,
        uncertainty={"paths": 1, "seed": 3},
    )
    output = tmp_path / "output"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    saved = load_run(output)
    entry = saved.index["series"][0]
    assert entry["shape"] == [30, 1] and saved.load_paths(entry).shape == (30, 1)


def test_late_report_failure_removes_owned_staging_directory(
    monkeypatch, tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(
        tmp_path / "daily", token_fn=lambda d, m, u: (100, 200, 50)
    )
    config = forecast_config_factory(
        tmp_path / "config.yaml", dataset, uncertainty={"paths": 2, "seed": 1}
    )
    import forecast.forecasting.presentation as presentation

    monkeypatch.setattr(
        presentation,
        "write_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("late write")),
    )
    output = tmp_path / "output"
    with pytest.raises(ExecutionError):
        run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    assert not output.exists()


@pytest.mark.parametrize(
    ("token_fn", "coverage_fn", "expected"),
    [
        (lambda d, m, u: None, lambda d, m, u: "complete", "inactive"),
        (
            lambda d, m, u: (1, 0, 0) if d in (0, 2, 4) else None,
            lambda d, m, u: "complete",
            "insufficient_startup",
        ),
        (
            lambda d, m, u: (10, 0, 0),
            lambda d, m, u: "partial" if d == 59 else "complete",
            "insufficient_history",
        ),
    ],
)
def test_history_boundary_statuses(
    token_fn,
    coverage_fn,
    expected,
    tmp_path,
    daily_dataset_factory,
    forecast_config_factory,
):
    dataset = daily_dataset_factory(
        tmp_path / "daily", token_fn=token_fn, coverage_fn=coverage_fn
    )
    config = forecast_config_factory(tmp_path / "config.yaml", dataset)
    output = tmp_path / "output"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    assert load_run(output).index["series"][0]["status"] == expected


def _rewrite_and_rehash(path: Path, manifest_path: Path, manifest_key: str, value: dict) -> None:
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(payload)
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["files"][manifest_key]
    entry["sha256"] = hashlib.sha256(payload).hexdigest()
    entry["bytes"] = len(payload)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def test_run_records_dataset_hash_and_cost_facts(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    """T2 accept: a new run records the dataset manifest hash and `SavedRun` returns
    the four facts cost design §3.1 needs."""
    dataset = daily_dataset_factory(tmp_path / "daily")
    config = forecast_config_factory(tmp_path / "config.yaml", dataset)
    output = tmp_path / "output"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    saved = load_run(output)
    expected_hash = hashlib.sha256((dataset / "manifest.json").read_bytes()).hexdigest()
    assert saved.dataset == {"path": str(dataset), "manifest_sha256": expected_hash}
    assert saved.history_lookback_days == 90
    assert saved.origin == date(2026, 8, 29)
    assert saved.periods is not None
    assert {p["type"] for p in saved.periods} == {"requested", "calendar"}
    requested = next(p for p in saved.periods if p["type"] == "requested")
    assert requested["start"] <= requested["end"]


def test_load_run_tolerates_missing_run_facts(tmp_path, saved_run_factory):
    """A run saved before T2 (bare `request.json`) still loads; the four facts are
    `None` rather than raising -- only a consumer that requires them should raise."""
    root = saved_run_factory(tmp_path / "run")
    saved = load_run(root)
    assert (saved.origin, saved.periods, saved.dataset, saved.history_lookback_days) == (
        None,
        None,
        None,
        None,
    )


def test_load_run_rejects_malformed_dataset_fact(tmp_path, saved_run_factory):
    root = saved_run_factory(tmp_path / "run")
    request_doc = json.loads((root / "request.json").read_text())
    request_doc["dataset"] = {"path": "x"}  # missing manifest_sha256
    _rewrite_and_rehash(root / "request.json", root / "manifest.json", "request.json", request_doc)
    with pytest.raises(InputError):
        load_run(root)


def test_load_run_tolerates_extra_index_field(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    """T2 accept: the reader accepts a `paths/index.json` with an extra field."""
    dataset = daily_dataset_factory(tmp_path / "daily")
    config = forecast_config_factory(tmp_path / "config.yaml", dataset)
    output = tmp_path / "output"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    index = json.loads((output / "paths/index.json").read_text())
    index["future_field"] = "ignored"
    _rewrite_and_rehash(
        output / "paths/index.json", output / "manifest.json", "paths/index.json", index
    )
    saved = load_run(output)
    assert saved.index["future_field"] == "ignored"


def test_charts_are_not_written_and_manifest_omits_them(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    """T1 accept: a new run has no charts/ and its manifest lists none."""
    dataset = daily_dataset_factory(tmp_path / "daily")
    config = forecast_config_factory(tmp_path / "config.yaml", dataset)
    output = tmp_path / "output"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    assert not (output / "charts").exists()
    manifest = json.loads((output / "manifest.json").read_text())
    assert not any(name == "charts" or name.startswith("charts/") for name in manifest["files"])


def test_dataset_links_are_recorded_relative_to_the_run(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    """T1 accept: both recorded dataset paths are relative to the run directory."""
    dataset = daily_dataset_factory(tmp_path / "daily")
    config = forecast_config_factory(tmp_path / "config.yaml", dataset)
    output = tmp_path / "output"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    request_doc = json.loads((output / "request.json").read_text())
    resolved = yaml.safe_load((output / "resolved_config.yaml").read_text())
    assert not Path(request_doc["dataset"]["path"]).is_absolute()
    assert not Path(resolved["input"]["dataset_dir"]).is_absolute()
    assert (output / request_doc["dataset"]["path"]).resolve() == dataset.resolve()
    assert (output / resolved["input"]["dataset_dir"]).resolve() == dataset.resolve()


def test_load_run_resolves_dataset_path_after_moving_workspace_and_dataset_together(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    """T1 accept: moving a run + its dataset together (same relative position) keeps
    `load_run(...).dataset["path"]` resolvable, and `load_dataset` reads it (design
    §14.3)."""
    (tmp_path / "workspace" / "dataset").mkdir(parents=True)
    dataset = daily_dataset_factory(tmp_path / "workspace" / "dataset" / "synth-x")
    config = forecast_config_factory(tmp_path / "workspace" / "forecast.yaml", dataset)
    output = tmp_path / "workspace" / "forecast" / "run"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    moved_root = tmp_path / "elsewhere"
    shutil.move(str(tmp_path / "workspace"), str(moved_root))
    saved = load_run(moved_root / "forecast" / "run")
    assert Path(saved.dataset["path"]) == (moved_root / "dataset" / "synth-x").resolve()
    load_dataset(Path(saved.dataset["path"]))  # does not raise


def test_load_run_still_loads_an_old_run_with_an_absolute_dataset_path(
    tmp_path, saved_run_factory, daily_dataset_factory
):
    """T1 accept: an old run recorded with an absolute dataset path still loads."""
    dataset = daily_dataset_factory(tmp_path / "daily")
    root = saved_run_factory(tmp_path / "run")
    request_doc = json.loads((root / "request.json").read_text())
    request_doc["dataset"] = {
        "path": str(dataset),
        "manifest_sha256": hashlib.sha256((dataset / "manifest.json").read_bytes()).hexdigest(),
    }
    _rewrite_and_rehash(root / "request.json", root / "manifest.json", "request.json", request_doc)
    saved = load_run(root)
    assert saved.dataset["path"] == str(dataset.resolve())
