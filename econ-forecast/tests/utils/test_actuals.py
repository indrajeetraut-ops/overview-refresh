"""Actuals-overlay sourcing: reloads the exact input a real forecast run consumed.

These tests run the real synthetic-dataset -> forecast pipeline (not a hand-built
fixture) because the load-bearing claim -- "the overlay is exactly what the
forecaster saw" -- can only be proven against the real `build_histories` call the
forecaster made, not a re-derivation. Most fixtures build the run and its dataset
fresh under the same `tmp_path` (a fixture pairing a run with a dataset is only
valid when both are created together in the same test); one test below moves a
whole workspace (run + dataset, same relative position) to prove actuals sourcing
survives that move (design §14.3).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import yaml

from forecast.forecasting.artifacts import load_run
from forecast.forecasting.dataset import load_dataset
from forecast.forecasting.errors import InputError
from forecast.forecasting.history import build_histories
from forecast.forecasting.request import ForecastRequest
from forecast.forecasting.runner import run_forecast
from forecast.utils.actuals import load_actuals

_fixture_path = Path(__file__).parents[1] / "acceptance" / "conftest.py"
_spec = importlib.util.spec_from_file_location("_plot_actuals_fixtures", _fixture_path)
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)


@pytest.fixture
def paired_run(tmp_path):
    """A real forecast run plus the dataset it was built from, under one tmp_path."""
    dataset = _fixtures.make_daily_dataset(
        tmp_path / "daily", days=60,
        token_fn=lambda day_index, model_index, user_index: (100 + day_index % 7, 300, 50),
    )
    config = _fixtures.write_forecast_config(
        tmp_path / "forecast.yaml", dataset,
        scopes=[{"type": "organization"}, {"type": "team", "name": "Team A"}],
    )
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), tmp_path / "out")
    return load_run(tmp_path / "out"), dataset


def _independent_histories(dataset_dir, saved_run):
    resolved = yaml.safe_load((saved_run.root / "resolved_config.yaml").read_text())
    request_doc = json.loads((saved_run.root / "request.json").read_text())
    data = load_dataset(dataset_dir)
    origin = date.fromisoformat(request_doc["origin"])
    scope_meta = data.scopes[("org-a", "organization", "org-a")]
    histories, _ = build_histories(
        data, [scope_meta], origin,
        lookback=resolved["history"]["lookback_days"],
        minimum=resolved["history"]["minimum_days"],
        active_days=resolved["history"]["minimum_active_days"],
        allocation_days=resolved["allocation"]["lookback_days"],
    )
    return {history.model: history for history in histories}


def test_auto_actuals_match_an_independent_build_histories_call(paired_run):
    saved_run, dataset = paired_run
    source, lookup = load_actuals(saved_run)
    assert source.source == "auto"
    assert source.hash_mismatch is False
    assert source.note is None

    expected = _independent_histories(dataset, saved_run)
    got = lookup[("organization", "org-a", "model-a")]
    reference = expected["model-a"]
    assert got.dates == reference.dates
    np.testing.assert_array_equal(got.values, reference.values)
    # Not a coincidence: the forecast run's own saved paths were sampled from
    # exactly this history, so its final value anchors the forecast origin.
    assert got.dates[-1].isoformat() == source.origin


def test_dataset_override_bypasses_the_recorded_location(paired_run, tmp_path):
    saved_run, dataset = paired_run
    moved = tmp_path / "moved-dataset"
    dataset.rename(moved)
    unavailable_source, empty_lookup = load_actuals(saved_run)
    assert unavailable_source.source == "unavailable"
    assert empty_lookup == {}

    override_source, lookup = load_actuals(saved_run, dataset_override=moved)
    assert override_source.source == "override"
    assert override_source.hash_mismatch is False
    assert ("organization", "org-a", "model-a") in lookup


def test_hash_mismatch_is_noted_in_auto_mode_only(paired_run):
    saved_run, dataset = paired_run
    # Corrupt one existing row's token amount in place: still a structurally
    # valid dataset (same row count/identities), but different bytes than what
    # the forecast run's manifest recorded -- drift after the forecast ran.
    usage_path = dataset / "usage.jsonl"
    manifest_path = dataset / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    lines = usage_path.read_bytes().splitlines(keepends=True)
    first_row = json.loads(lines[0])
    first_row["tokens"]["output"] += 1
    lines[0] = (json.dumps(first_row, sort_keys=True, separators=(",", ":")) + "\n").encode()
    payload = b"".join(lines)
    usage_path.write_bytes(payload)
    manifest["files"]["usage.jsonl"] = {
        "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
        "rows": manifest["files"]["usage.jsonl"]["rows"],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    source, lookup = load_actuals(saved_run)
    assert source.source == "auto"
    assert source.hash_mismatch is True
    assert "hash mismatch" in source.note
    assert lookup  # still plotted, just flagged

    override_source, _ = load_actuals(saved_run, dataset_override=dataset)
    assert override_source.source == "override"
    assert override_source.hash_mismatch is False


def test_missing_dataset_directory_is_unavailable_not_raised(paired_run, tmp_path):
    saved_run, dataset = paired_run
    import shutil
    shutil.rmtree(dataset)
    source, lookup = load_actuals(saved_run)
    assert source.source == "unavailable"
    assert lookup == {}
    assert "unavailable" in source.note


def test_run_without_recorded_input_is_unavailable(tmp_path):
    cli_fixture_path = Path(__file__).with_name("test_plot_forecast_cli.py")
    cli_spec = importlib.util.spec_from_file_location("_plot_cli_fixtures", cli_fixture_path)
    cli_fixtures = importlib.util.module_from_spec(cli_spec)
    cli_spec.loader.exec_module(cli_fixtures)

    run_dir = cli_fixtures.make_run(tmp_path / "hand-built")
    saved_run = load_run(run_dir)
    source, lookup = load_actuals(saved_run)
    assert source.source == "unavailable"
    assert lookup == {}


def test_moved_workspace_actuals_still_resolve_via_saved_run_dataset(tmp_path):
    """T1 accept (design §14.3): the actuals dataset path comes from
    `SavedRun.dataset["path"]` (already resolved by `load_run` against the run
    root), not from an independent, cwd-relative read of `resolved_config.yaml`'s
    `input.dataset_dir` -- so a workspace and its dataset that move together (same
    relative position) keep loading."""
    (tmp_path / "workspace" / "dataset").mkdir(parents=True)
    dataset = _fixtures.make_daily_dataset(
        tmp_path / "workspace" / "dataset" / "daily", days=60,
        token_fn=lambda day_index, model_index, user_index: (100 + day_index % 7, 300, 50),
    )
    config = _fixtures.write_forecast_config(
        tmp_path / "workspace" / "forecast.yaml", dataset,
        scopes=[{"type": "organization"}],
    )
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"),
                 tmp_path / "workspace" / "forecast" / "run")
    moved = tmp_path / "elsewhere"
    shutil.move(str(tmp_path / "workspace"), str(moved))
    saved_run = load_run(moved / "forecast" / "run")
    source, lookup = load_actuals(saved_run)
    assert source.source == "auto"
    assert source.dataset_dir == str((moved / "dataset" / "daily").resolve())
    assert ("organization", "org-a", "model-a") in lookup
