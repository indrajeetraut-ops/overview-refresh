"""Acceptance checks derived from the frozen specification, not implementation tests."""
from datetime import date
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from forecast.forecasting.errors import InputError
from forecast.forecasting.request import ForecastRequest, resolve_window

@pytest.mark.parametrize(("forecast_request", "start", "end", "internal"), [
    (ForecastRequest(date(2026, 9, 12), "week", 1, 1), date(2026, 9, 14), date(2026, 9, 20), 8),
    (ForecastRequest(date(2026, 9, 12), "week", 3, 1), date(2026, 9, 28), date(2026, 10, 4), 22),
    (ForecastRequest(date(2027, 12, 15), "month", 2, 2), date(2028, 2, 1), date(2028, 3, 31), 107),
])
def test_calendar_oracles(forecast_request, start, end, internal):
    window = resolve_window(forecast_request)
    assert (window.requested_dates[0], window.requested_dates[-1]) == (start, end)
    assert len(window.internal_dates) == internal
    assert (window.internal_dates[0] - forecast_request.origin).days == 1


@pytest.mark.parametrize("field,value", [("offset", 0), ("offset", True), ("periods", 0), ("periods", 1.0)])
def test_request_rejects_non_strict_positive_integers(field, value):
    args = {"origin": date(2026, 1, 1), "unit": "month", "offset": 1, "periods": 1, field: value}
    with pytest.raises(InputError):
        ForecastRequest(**args)


@pytest.mark.parametrize(("section", "field", "value"), [
    ("history", "lookback_days", True), ("history", "minimum_days", 13),
    ("history", "minimum_active_days", 0), ("allocation", "lookback_days", False),
    ("uncertainty", "paths", 0), ("uncertainty", "paths", 10001),
    ("uncertainty", "seed", -1),
    ("uncertainty", "max_negative_correction_ratio", float("nan")),
    ("uncertainty", "max_negative_correction_ratio", float("inf")),
    ("uncertainty", "max_negative_correction_ratio", -0.1),
    ("uncertainty", "max_negative_correction_ratio", 1.1),
    ("uncertainty", "max_negative_correction_ratio", True),
    ("model", "seasonal", "weekly"), ("model", "trend", "damped"),
    ("runtime", "workers", True), ("runtime", "workers", 5),
])
def test_config_type_range_and_nonfinite_rejection(
        tmp_path, neutral_dataset, forecast_config_factory, section, field, value):
    from forecast.forecasting.config import load_config
    path = forecast_config_factory(tmp_path / "bad.yaml", neutral_dataset, **{section: {field: value}})
    with pytest.raises(InputError):
        load_config(path)


def test_config_relative_dataset_path_and_unknown_key(
        tmp_path, neutral_dataset, forecast_config_factory):
    from forecast.forecasting.config import load_config
    path = forecast_config_factory(tmp_path / "valid.yaml", Path("daily"))
    assert load_config(path).input.dataset_dir == neutral_dataset.resolve()
    raw = path.read_text() + "unknown_release_key: true\n"
    path.write_text(raw)
    with pytest.raises(InputError):
        load_config(path)


def test_run_forecast_hand_oracle_and_artifact_conservation(
        tmp_path, daily_dataset_factory, forecast_config_factory):
    from forecast.forecasting import artifacts, runner
    # Exactly-constant history: the analytic constant-ETS branch (design §3, numerical
    # preflight) fits with zero residuals, so every future day/sample reproduces the
    # historical constant exactly -- no fallback/optimizer, no sampling variance.
    dataset = daily_dataset_factory(tmp_path / "daily", days=60,
                                    token_fn=lambda d, m, u: (100, 300, 100))
    config = forecast_config_factory(tmp_path / "forecast.yaml", dataset,
                                       uncertainty={"paths": 16, "seed": 7})
    request = ForecastRequest(date(2026, 8, 29), "month")
    output = tmp_path / "forecast result"
    result = runner.run_forecast(config, request, output)
    assert result.status == "complete" and result.exit_code == 0
    saved = artifacts.load_run(output)
    assert saved.index["dates"][0] == "2026-09-01"
    assert saved.index["dates"][-1] == "2026-09-30"
    entry = saved.index["series"][0]
    paths = saved.load_paths(entry)
    assert paths.shape == (30, 16) and paths.dtype == np.float64
    assert np.isfinite(paths).all() and (paths >= 0).all()
    for row_index, day in enumerate(saved.index["dates"]):
        np.testing.assert_array_equal(paths[row_index], np.full(16, 500.0))
    daily = [json.loads(line) for line in (output / "daily_expected.jsonl").read_text().splitlines()]
    by_date = {row["date"]: row for row in daily}
    for row_index, day in enumerate(saved.index["dates"]):
        assert sum(by_date[day]["tokens"].values()) == pytest.approx(paths[row_index].mean(), rel=1e-12, abs=1e-9)
        expected = paths[row_index].mean()
        assert by_date[day]["tokens"] == pytest.approx({"input":.2*expected,"cache_write_5m":0,"cache_write_1h":0,"cache_read":.6*expected,"output":.2*expected})
    allocation = json.loads((output / "allocations.json").read_text())["entries"][0]
    shares = allocation["shares"][0]
    assert shares["input"] == pytest.approx(.2)
    assert shares["cache_read"] == pytest.approx(.6)
    assert shares["output"] == pytest.approx(.2)
    assert sum(shares[k] for k in ("input", "cache_read", "output")) == pytest.approx(1)
    summaries=json.loads((output/"period_summary.json").read_text())["entries"]
    assert len(summaries)==4  # model+scope, each requested+calendar even when boundaries coincide
    for row in summaries:
        assert row["expected_tokens"]==pytest.approx(paths.sum(axis=0).mean(),rel=1e-12,abs=1e-9)
        assert [row[key] for key in ("p10", "p50", "p90")] == pytest.approx(
            np.quantile(paths.sum(axis=0), [.1, .5, .9], method="linear")
        )
    diagnostics=json.loads((output/"diagnostics.json").read_text()); manifest=json.loads((output/"manifest.json").read_text())
    assert diagnostics["runtime"]["output_bytes"]==sum(meta["bytes"] for meta in manifest["files"].values())
    report=(output/"report.md").read_text()
    assert f"output bytes: {diagnostics['runtime']['output_bytes']:,}" in report


def test_validate_only_preserves_inputs_and_writes_nothing(
        tmp_path, neutral_dataset, forecast_config_factory):
    from forecast.forecasting import runner
    config = forecast_config_factory(tmp_path / "forecast.yaml", neutral_dataset)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in neutral_dataset.iterdir()}
    output = tmp_path / "must not exist"
    result = runner.run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output,
                                 validate_only=True)
    assert result.status == "valid" and result.output_dir is None and result.report_path is None
    assert not output.exists()
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in neutral_dataset.iterdir()}


def test_public_reader_roundtrip_and_foreign_entry_rejection(tmp_path, saved_run_factory):
    from forecast.forecasting.artifacts import load_run
    run = load_run(saved_run_factory(tmp_path / "saved"))
    array = run.load_paths(run.index["series"][0])
    assert isinstance(array, np.memmap) and array.flags.writeable is False
    np.testing.assert_array_equal(array, [[100, 140, 80], [110, 90, 95], [90, 130, 85]])
    with pytest.raises(InputError):
        run.load_paths(dict(run.index["series"][0]))


@pytest.mark.parametrize("mutation", ["checksum", "traversal", "nonfinite"])
def test_public_reader_rejects_corrupt_or_unsafe_runs(tmp_path, saved_run_factory, mutation):
    from forecast.forecasting.artifacts import load_run
    root = saved_run_factory(tmp_path / "saved")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if mutation == "checksum":
        (root / "report.md").write_text("changed")
    elif mutation == "traversal":
        old = next(k for k in manifest["files"] if k.endswith(".npy"))
        manifest["files"]["../outside.npy"] = manifest["files"].pop(old)
        manifest_path.write_text(json.dumps(manifest))
    else:
        entry_path = root / manifest_path.parent.joinpath("paths/index.json").relative_to(root)
        index = json.loads(entry_path.read_text())
        arr_path = root / index["series"][0]["path"]
        np.save(arr_path, np.array([[np.nan, 1, 2], [3, 4, 5], [6, 7, 8]], dtype="float64"))
        payload = arr_path.read_bytes(); manifest["files"][index["series"][0]["path"]] = {
            "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(InputError):
        run = load_run(root)
        run.load_paths(run.index["series"][0])


@pytest.mark.parametrize("mutation", ["lying_scope", "bool_version", "bool_samples", "unhashable_identity"])
def test_public_reader_rejects_semantically_misleading_index(
        tmp_path,saved_run_factory,mutation):
    from forecast.forecasting.artifacts import load_run
    root=saved_run_factory(tmp_path/"saved"); index_path=root/"paths/index.json"
    index=json.loads(index_path.read_text())
    if mutation=="lying_scope":
        series=index["series"][0]; series.update(status="sampling_failed",path=None,shape=None,
                                                  reasons=["injected failure"])
        index["scopes"][0].update(status="complete",risk_ready=True)
    elif mutation=="bool_version": index["schema_version"]=True
    elif mutation=="bool_samples": index["sample_ids"]=[False,True,2]
    else: index["series"][0]["scope_id"]=["unhashable"]
    index_path.write_text(json.dumps(index))
    _refresh_manifest_file(root,"paths/index.json")
    with pytest.raises(InputError): load_run(root)


def _refresh_manifest_file(root, name):
    path = root / name
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    payload = path.read_bytes()
    manifest["files"][name].update(sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload))
    if name.endswith(".jsonl"):
        manifest["files"][name]["rows"] = len(path.read_text().splitlines())
    manifest_path.write_text(json.dumps(manifest))


@pytest.mark.parametrize("missing", ["start_date", "end_date", "timezone"])
def test_neutral_metadata_missing_required_fields_is_input_error(
        tmp_path, daily_dataset_factory, missing):
    from forecast.forecasting.dataset import load_dataset
    root = daily_dataset_factory(tmp_path / "daily")
    metadata = json.loads((root / "metadata.json").read_text())
    del metadata["organizations"][0][missing]
    (root / "metadata.json").write_text(json.dumps(metadata))
    _refresh_manifest_file(root, "metadata.json")
    with pytest.raises(InputError):
        load_dataset(root)


def test_impossible_team_attribution_is_rejected(
        tmp_path, daily_dataset_factory):
    from forecast.forecasting.dataset import load_dataset
    from forecast.forecasting.history import build_histories
    root = daily_dataset_factory(tmp_path / "daily")
    usage_path = root / "usage.jsonl"
    rows = [json.loads(x) for x in usage_path.read_text().splitlines()]
    rows[0]["team_id"] = "team-other"
    metadata = json.loads((root / "metadata.json").read_text())
    metadata["organizations"][0]["scopes"].append({"type": "team", "id": "team-other",
        "name": "Other", "coverage_keys": [{"user_id": "different-user", "agent_type": "agent-a"}]})
    # Keep the other Team's population a valid Organization subset while making u1 attribution impossible.
    metadata["organizations"][0]["scopes"][0]["coverage_keys"].append(
        {"user_id": "different-user", "agent_type": "agent-a"})
    usage_path.write_bytes(b"".join((json.dumps(x, sort_keys=True, separators=(",", ":")) + "\n").encode() for x in rows))
    (root / "metadata.json").write_text(json.dumps(metadata))
    _refresh_manifest_file(root, "usage.jsonl"); _refresh_manifest_file(root, "metadata.json")
    with pytest.raises(InputError):
        data = load_dataset(root)
        selected = [data.scopes[("org-a", "organization", None)]]
        build_histories(data, selected, date(2026, 8, 29), lookback=90, minimum=14,
                        active_days=4, allocation_days=28)


def test_future_rows_cannot_change_paths_or_allocation(
        tmp_path, daily_dataset_factory, forecast_config_factory):
    from forecast.forecasting.runner import run_forecast
    from forecast.forecasting.artifacts import load_run
    origin = date(2026, 8, 29)
    common = lambda d, m, u: (100 + d % 7, 300, 50)
    changed = lambda d, m, u: common(d, m, u) if d < 60 else (9_000_000 + d, 1, 1)
    first = daily_dataset_factory(tmp_path / "first", days=70, token_fn=common)
    second = daily_dataset_factory(tmp_path / "second", days=70, token_fn=changed)
    cfg1 = forecast_config_factory(tmp_path / "first.yaml", first, uncertainty={"paths": 16, "seed": 11})
    cfg2 = forecast_config_factory(tmp_path / "second.yaml", second, uncertainty={"paths": 16, "seed": 11})
    out1, out2 = tmp_path / "out1", tmp_path / "out2"
    run_forecast(cfg1, ForecastRequest(origin, "month"), out1)
    run_forecast(cfg2, ForecastRequest(origin, "month"), out2)
    saved1, saved2 = load_run(out1), load_run(out2)
    np.testing.assert_allclose(saved1.load_paths(saved1.index["series"][0]),
                               saved2.load_paths(saved2.index["series"][0]), rtol=1e-10, atol=1e-7)
    assert (out1 / "allocations.json").read_bytes() == (out2 / "allocations.json").read_bytes()


@pytest.mark.parametrize(("token_fn", "coverage_fn", "series_status", "scope_status"), [
    (lambda d, m, u: None, lambda d, m, u: "complete", "inactive", "inactive"),
    (lambda d, m, u: (1, 0, 0) if d in (0, 2, 4) else None,
     lambda d, m, u: "complete", "insufficient_startup", "incomplete"),
    (lambda d, m, u: (10, 20, 5), lambda d, m, u: "partial" if d == 59 else "complete",
     "insufficient_history", "incomplete"),
])
def test_history_status_oracles(tmp_path, daily_dataset_factory, forecast_config_factory,
                                token_fn, coverage_fn, series_status, scope_status):
    from forecast.forecasting.runner import run_forecast
    root = daily_dataset_factory(tmp_path / "daily", token_fn=token_fn, coverage_fn=coverage_fn)
    config = forecast_config_factory(tmp_path / "forecast.yaml", root)
    output = tmp_path / "output"
    result = run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    index = json.loads((output / "paths/index.json").read_text())
    assert index["series"][0]["status"] == series_status
    assert index["scopes"][0]["status"] == scope_status
    assert result.exit_code == (2 if scope_status == "incomplete" else 0)


def test_future_startup_evidence_is_ignored_at_cutoff(
        tmp_path, daily_dataset_factory, forecast_config_factory):
    from forecast.forecasting.runner import run_forecast
    evidence=[{"scope_type":"organization","scope_id":"org-a","model":"model-a",
               "positive_streak_start":"2026-09-01","positive_streak_end":"2026-09-04",
               "source":"future preparation record"}]
    root=daily_dataset_factory(tmp_path/"daily",days=70,startup_evidence=evidence)
    config=forecast_config_factory(tmp_path/"forecast.yaml",root)
    result=run_forecast(config,ForecastRequest(date(2026,8,29),"month"),tmp_path/"output")
    assert result.status=="complete"


@pytest.mark.parametrize("token_fn", [
    lambda d,m,u:(100,300,100),
    lambda d,m,u:(100+d%7,300+(d*d)%11,50),
])
def test_single_path_preserves_two_dimensional_saved_axis(
        tmp_path,daily_dataset_factory,forecast_config_factory,token_fn):
    from forecast.forecasting.runner import run_forecast
    from forecast.forecasting.artifacts import load_run
    root=daily_dataset_factory(tmp_path/"daily",token_fn=token_fn)
    config=forecast_config_factory(tmp_path/"forecast.yaml",root,uncertainty={"paths":1,"seed":9})
    output=tmp_path/"output"
    assert run_forecast(config,ForecastRequest(date(2026,8,29),"month"),output).exit_code==0
    saved=load_run(output); entry=saved.index["series"][0]; values=saved.load_paths(entry)
    assert entry["shape"]==[30,1] and values.shape==(30,1) and saved.index["sample_ids"]==[0]


def test_nonzero_usage_on_outage_prevents_false_all_inactive(
        tmp_path, daily_dataset_factory, forecast_config_factory):
    from forecast.forecasting.runner import run_forecast
    root=daily_dataset_factory(tmp_path/"daily",
        token_fn=lambda d,m,u:(10,0,0) if d==0 else None,
        coverage_fn=lambda d,m,u:"partial" if d==0 else "complete")
    config=forecast_config_factory(tmp_path/"forecast.yaml",root)
    output=tmp_path/"output"; result=run_forecast(config,ForecastRequest(date(2026,8,29),"month"),output)
    index=json.loads((output/"paths/index.json").read_text())
    assert result.status=="partial" and index["series"][0]["status"]=="insufficient_startup"


def test_late_artifact_write_failure_leaves_no_published_run(
        tmp_path,daily_dataset_factory,forecast_config_factory,monkeypatch):
    import forecast.forecasting.runner as runner
    from forecast.forecasting.errors import ExecutionError
    root=daily_dataset_factory(tmp_path/"daily",token_fn=lambda d,m,u:(100,300,100))
    config=forecast_config_factory(tmp_path/"forecast.yaml",root,uncertainty={"paths":4,"seed":3})
    output=tmp_path/"output"; original=runner._json_write
    def fail_late(path,value):
        if path.name=="diagnostics.json":
            raise OSError("injected final diagnostics failure")
        return original(path,value)
    monkeypatch.setattr(runner,"_json_write",fail_late)
    with pytest.raises(ExecutionError):
        runner.run_forecast(config,ForecastRequest(date(2026,8,29),"month"),output)
    assert not output.exists()


def test_public_reader_rejects_a_version_1_forecast_manifest_with_the_rerun_message(
        tmp_path, saved_run_factory):
    from forecast.forecasting.artifacts import load_run
    root = saved_run_factory(tmp_path / "saved")
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["schema_version"] = 1
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(InputError) as caught:
        load_run(root)
    assert str(caught.value) == "forecast run uses schema 1; rerun the forecast"


def test_forecast_run_splits_a_five_type_history_and_writes_version_2(
        tmp_path, daily_dataset_factory, forecast_config_factory):
    from forecast.forecasting import artifacts, runner
    # (input, cache_write_5m, cache_write_1h, cache_read, output), constant history.
    dataset = daily_dataset_factory(tmp_path / "daily", days=60,
                                    token_fn=lambda d, m, u: (10, 20, 30, 300, 100))
    config = forecast_config_factory(tmp_path / "forecast.yaml", dataset,
                                     uncertainty={"paths": 16, "seed": 7})
    output = tmp_path / "forecast result"
    result = runner.run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    assert result.status == "complete"
    manifest = json.loads((output / "manifest.json").read_text())
    allocations = json.loads((output / "allocations.json").read_text())
    summary = json.loads((output / "period_summary.json").read_text())
    assert (manifest["schema_version"], allocations["schema_version"]) == (2, 2)
    assert summary["schema_version"] == 1  # carries no per-type values
    shares = allocations["entries"][0]["shares"][0]
    for kind, share in (("input", 10 / 460), ("cache_write_5m", 20 / 460),
                        ("cache_write_1h", 30 / 460), ("cache_read", 300 / 460),
                        ("output", 100 / 460)):
        assert shares[kind] == pytest.approx(share)
    saved = artifacts.load_run(output)
    paths = saved.load_paths(saved.index["series"][0])
    daily = [json.loads(line) for line in (output / "daily_expected.jsonl").read_text().splitlines()]
    assert {row["schema_version"] for row in daily} == {2}
    by_date = {row["date"]: row for row in daily}
    for row_index, day in enumerate(saved.index["dates"]):
        row = by_date[day]
        assert set(row["tokens"]) == {"input", "cache_write_5m", "cache_write_1h", "cache_read", "output"}
        assert row["total_tokens"] == pytest.approx(paths[row_index].mean())
        assert row["tokens"]["cache_write_1h"] == pytest.approx(30 / 460 * paths[row_index].mean())
