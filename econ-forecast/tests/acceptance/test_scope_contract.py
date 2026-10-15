"""Independent end-to-end oracles for scope identity, attribution, and readiness."""

from __future__ import annotations

from datetime import date
import hashlib
import json

import numpy as np
import pytest

from forecast.forecasting.artifacts import load_run
from forecast.forecasting.errors import InputError
from forecast.forecasting.request import ForecastRequest
from forecast.forecasting.runner import _scope_status, run_forecast


def _rewrite_daily(root, usage, coverage, metadata):
    def write_json(path, value):
        payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        path.write_bytes(payload)
        return {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}

    def write_jsonl(path, rows):
        payload = b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for row in rows
        )
        path.write_bytes(payload)
        return {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload), "rows": len(rows)}

    files = {
        "usage.jsonl": write_jsonl(root / "usage.jsonl", usage),
        "coverage.jsonl": write_jsonl(root / "coverage.jsonl", coverage),
        "metadata.json": write_json(root / "metadata.json", metadata),
    }
    write_json(root / "manifest.json", {
        "schema_version": 2, "operation": "daily", "status": "complete", "files": files
    })


def _read_daily(root):
    usage = [json.loads(line) for line in (root / "usage.jsonl").read_text().splitlines()]
    coverage = [json.loads(line) for line in (root / "coverage.jsonl").read_text().splitlines()]
    metadata = json.loads((root / "metadata.json").read_text())
    return usage, coverage, metadata


def test_org_team_project_share_rows_without_double_counting(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    # Stored input=100 consists conceptually of 0 ordinary input + 100 cache
    # writes. The shared wire contract has already combined them, so the target
    # is 100+300+100=500, never 100+100+300+100=600.
    ordinary_input, cache_write = 0, 100
    assert ordinary_input + cache_write == 100
    dataset = daily_dataset_factory(tmp_path / "daily", token_fn=lambda *_: (100, 300, 100))
    config = forecast_config_factory(tmp_path / "config.yaml", dataset, scopes=[
        {"type": "organization"}, {"type": "team", "name": "Team A"},
        {"type": "project", "name": "Project A"},
    ], uncertainty={"paths": 1, "seed": 7})
    output = tmp_path / "output"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    saved = load_run(output)
    entries = {(e["scope_type"], e["scope_id"]): e for e in saved.index["series"]}
    assert set(entries) == {("organization", "org-a"), ("team", "team-a"),
                            ("project", "project-a")}
    arrays = [saved.load_paths(entry) for entry in entries.values()]
    for array in arrays[1:]:
        np.testing.assert_array_equal(array, arrays[0])
    # Constant history: the exact-constant analytic ETS branch reproduces it exactly.
    for amount in arrays[0][:, 0]:
        assert amount == 500.0


def test_unattributed_usage_stays_only_in_organization(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(tmp_path / "daily", token_fn=lambda *_: (12, 3, 5))
    usage, coverage, metadata = _read_daily(dataset)
    for row in usage:
        row["team_id"] = None
        row["project_id"] = None
    _rewrite_daily(dataset, usage, coverage, metadata)
    config = forecast_config_factory(tmp_path / "config.yaml", dataset, scopes=[
        {"type": "organization"}, {"type": "team", "name": "Team A"},
        {"type": "project", "name": "Project A"},
    ], uncertainty={"paths": 1})
    output = tmp_path / "output"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    saved = load_run(output)
    by_type = {e["scope_type"]: e for e in saved.index["series"]}
    for amount in saved.load_paths(by_type["organization"])[:, 0]:
        assert amount == 20.0
    for typ in ("team", "project"):
        assert by_type[typ]["status"] == "inactive"
        np.testing.assert_array_equal(saved.load_paths(by_type[typ]), np.zeros((30, 1)))


def test_two_teams_may_each_have_a_null_user_stream(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(
        tmp_path / "daily", models=(("model-a", "agent-a"), ("model-b", "agent-b")),
        token_fn=lambda _day, model, _user: ((100, 0, 0) if model == 0 else (200, 0, 0)),
    )
    usage, coverage, metadata = _read_daily(dataset)
    for row in usage + coverage:
        row["user_id"] = None
    for row in usage:
        if row["agent_type"] == "agent-b":
            row["team_id"] = "team-b"
    scopes = metadata["organizations"][0]["scopes"]
    for scope in scopes:
        for key in scope["coverage_keys"]:
            key["user_id"] = None
    team_a = next(scope for scope in scopes if scope["type"] == "team")
    team_b = json.loads(json.dumps(team_a))
    team_a["coverage_keys"] = [key for key in team_a["coverage_keys"]
                               if key["agent_type"] == "agent-a"]
    team_b.update(id="team-b", name="Team B")
    team_b["coverage_keys"] = [{"user_id": None, "agent_type": "agent-b"}]
    scopes.append(team_b)
    _rewrite_daily(dataset, usage, coverage, metadata)
    config = forecast_config_factory(
        tmp_path / "config.yaml", dataset,
        scopes=[{"type": "team", "name": "Team A"},
                {"type": "team", "name": "Team B"}],
        uncertainty={"paths": 2},
    )
    output = tmp_path / "output"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    saved = load_run(output)
    assert {(entry["scope_name"], entry["model"]) for entry in saved.index["series"]} == {
        ("Team A", "model-a"), ("Team B", "model-b")
    }
    # Constant per-model history: the exact-constant analytic ETS branch reproduces it.
    for entry in saved.index["series"]:
        baseline = 100.0 if entry["model"] == "model-a" else 200.0
        for row in saved.load_paths(entry):
            np.testing.assert_array_equal(row, np.full(2, baseline))


def test_default_org_override_and_same_team_id_do_not_mix(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(tmp_path / "daily", token_fn=lambda *_: (10, 0, 0))
    usage, coverage, metadata = _read_daily(dataset)
    org_b = json.loads(json.dumps(metadata["organizations"][0]))
    org_b["org_id"] = "org-b"; org_b["name"] = "Organization B"
    org_b["scopes"][0]["id"] = "org-b"; org_b["scopes"][0]["name"] = "Organization B"
    metadata["organizations"].append(org_b)
    usage_b = [{**row, "org_id": "org-b", "tokens": {"input": 30, "cache_write_5m": 0, "cache_write_1h": 0,
                                      "cache_read": 0, "output": 0}}
               for row in usage]
    coverage_b = [{**row, "org_id": "org-b"} for row in coverage]
    _rewrite_daily(dataset, usage + usage_b, coverage + coverage_b, metadata)
    config = forecast_config_factory(tmp_path / "config.yaml", dataset, scopes=[
        {"type": "team", "name": "Team A"},
        {"type": "team", "name": "Team A", "org_id": "org-b"},
    ], uncertainty={"paths": 1})
    output = tmp_path / "output"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    saved = load_run(output)
    entries = {e["org_id"]: e for e in saved.index["series"]}
    for org, baseline in (("org-a", 10.0), ("org-b", 30.0)):
        for amount in saved.load_paths(entries[org])[:, 0]:
            assert amount == baseline
    assert entries["org-a"]["series_id"] != entries["org-b"]["series_id"]


def test_unsupported_billing_is_rejected_not_omitted(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(tmp_path / "daily")
    usage, coverage, metadata = _read_daily(dataset)
    usage[0]["billing_mode"] = "subscription"
    _rewrite_daily(dataset, usage, coverage, metadata)
    config = forecast_config_factory(tmp_path / "config.yaml", dataset)
    with pytest.raises(InputError, match="unsupported billing"):
        run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), tmp_path / "out")


def test_catalog_model_without_scope_population_is_explicitly_not_applicable(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(
        tmp_path / "daily", models=(("model-a", "agent-a"), ("model-b", "agent-b"))
    )
    usage, coverage, metadata = _read_daily(dataset)
    team = next(s for s in metadata["organizations"][0]["scopes"] if s["type"] == "team")
    team["coverage_keys"] = [k for k in team["coverage_keys"] if k["agent_type"] == "agent-a"]
    for row in usage:
        if row["agent_type"] == "agent-b":
            row["team_id"] = None
    _rewrite_daily(dataset, usage, coverage, metadata)
    config = forecast_config_factory(
        tmp_path / "config.yaml", dataset, scopes=[{"type": "team", "name": "Team A"}],
        uncertainty={"paths": 1},
    )
    output = tmp_path / "output"
    result = run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    diagnostics = json.loads((output / "diagnostics.json").read_text())
    assert result.status == "complete"
    assert [(e["model"], e["reason"]) for e in diagnostics["not_applicable_models"]] == [
        ("model-b", "no coverage keys for model agents")
    ]
    assert [e["model"] for e in load_run(output).index["series"]] == ["model-a"]


def test_omitted_origin_uses_declared_covered_zero_and_explicit_cutoff(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(tmp_path / "daily", days=60,
        token_fn=lambda day, *_: None if day == 59 else (10000 if day > 50 else 100, 0, 0))
    config = forecast_config_factory(tmp_path / "config.yaml", dataset)
    default = run_forecast(config, ForecastRequest(None, "month"), validate_only=True)
    earlier = run_forecast(config, ForecastRequest(date(2026, 8, 20), "month"), validate_only=True)
    assert default.origin == "2026-08-29" and default.status == "valid"
    assert all(row["history_end"] == "2026-08-29" for row in default.diagnostics["series"])
    assert earlier.origin == "2026-08-20" and earlier.status == "valid"
    assert all(row["history_end"] == "2026-08-20" for row in earlier.diagnostics["series"])
    assert default.diagnostics["series"][0]["recent_total"] == 81_900.0
    assert earlier.diagnostics["series"][0]["recent_total"] == 2_800.0


def test_omitted_origin_keeps_final_outage_ineligible(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(tmp_path / "daily", days=60,
        coverage_fn=lambda day, *_: "missing" if day == 59 else "complete")
    config = forecast_config_factory(tmp_path / "config.yaml", dataset)
    result = run_forecast(config, ForecastRequest(None, "month"), validate_only=True)
    assert result.origin == "2026-08-29" and result.status == "invalid"
    assert result.rejected_scopes == 1


def test_names_resolve_saved_ids_and_reject_ambiguity_within_organization(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    dataset = daily_dataset_factory(tmp_path / "daily")
    config = forecast_config_factory(tmp_path / "config.yaml", dataset, scopes=[
        {"type": "team", "name": "Team A"}, {"type": "project", "name": "Project A"}])
    result = run_forecast(config, ForecastRequest(None, "month"), validate_only=True)
    assert result.status == "valid" and result.requested_scopes == 2
    unknown = forecast_config_factory(tmp_path / "unknown.yaml", dataset,
        scopes=[{"type": "team", "name": "missing"}])
    with pytest.raises(InputError, match="unknown team name.*missing"):
        run_forecast(unknown, ForecastRequest(None, "month"), validate_only=True)
    usage, coverage, metadata = _read_daily(dataset)
    metadata["organizations"][0]["scopes"].append({
        "type": "project", "id": "other", "name": "Project A",
        "coverage_keys": [{"user_id": "u1", "agent_type": "agent-a"}]})
    _rewrite_daily(dataset, usage, coverage, metadata)
    with pytest.raises(InputError, match="ambiguous project name.*Project A"):
        run_forecast(config, ForecastRequest(None, "month"), validate_only=True)


@pytest.mark.parametrize(("statuses", "expected"), [
    (["qualified"], ("complete", True)),
    (["qualified", "inactive"], ("conditional", True)),
    (["inactive"], ("inactive", False)),
    (["qualified", "insufficient_history"], ("incomplete", False)),
    (["qualified", "insufficient_startup"], ("incomplete", False)),
    (["qualified", "sampling_failed"], ("incomplete", False)),
])
def test_scope_status_truth_table(statuses, expected):
    assert _scope_status([{"status": status} for status in statuses]) == expected


@pytest.mark.parametrize(("case", "scope_status", "risk_ready", "run_status"), [
    ("conditional", "conditional", True, "complete"),
    ("inactive", "inactive", False, "complete"),
    ("insufficient", "incomplete", False, "partial"),
])
def test_runner_publishes_scope_readiness_from_every_component(
    case, scope_status, risk_ready, run_status,
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    def tokens(day, model, _user):
        if case == "inactive":
            return None
        if model == 0:
            return (100, 0, 0)
        if case == "conditional":
            return None
        return (10, 0, 0) if day in (0, 5, 10, 15) else None

    dataset = daily_dataset_factory(
        tmp_path / "daily", models=(("model-a", "agent-a"), ("model-b", "agent-b")),
        token_fn=tokens,
    )
    config = forecast_config_factory(tmp_path / "config.yaml", dataset,
                                     uncertainty={"paths": 1})
    output = tmp_path / "output"
    result = run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    saved = load_run(output)
    assert result.status == run_status
    assert [(s["status"], s["risk_ready"]) for s in saved.index["scopes"]] == [
        (scope_status, risk_ready)
    ]
    scope_summaries = [row for row in json.loads(
        (output / "period_summary.json").read_text())["entries"] if row["model"] is None]
    assert bool(scope_summaries) is (scope_status != "incomplete")


def test_scope_period_quantiles_sum_matching_sample_columns(
    tmp_path, daily_dataset_factory, forecast_config_factory
):
    # Constant per-model history (100 / 200): the exact-constant analytic ETS branch
    # reproduces each model's historical constant exactly on every day/sample, so the
    # per-day model-a + model-b == 300 identity holds under the forecast too (it would
    # not hold in general for independently-fit ETS series with joint-sampled but
    # otherwise-independent residuals).
    dataset = daily_dataset_factory(
        tmp_path / "daily", models=(("model-a", "agent-a"), ("model-b", "agent-b")),
        token_fn=lambda _day, model, _user: ((100, 0, 0) if model == 0 else (200, 0, 0)),
    )
    config = forecast_config_factory(tmp_path / "config.yaml", dataset,
                                     uncertainty={"paths": 32, "seed": 11})
    output = tmp_path / "output"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    summaries = json.loads((output / "period_summary.json").read_text())["entries"]
    requested = [row for row in summaries if row["period_type"] == "requested"]
    saved = load_run(output)
    arrays = {entry["model"]: saved.load_paths(entry) for entry in saved.index["series"]}
    np.testing.assert_array_equal(arrays["model-a"] + arrays["model-b"],
                                  np.full((30, 32), 300.0))
    assert {row["model"] for row in requested} == {"model-a", "model-b", None}
    for row in requested:
        if row["model"] is None:
            assert row["expected_tokens"] == 9000.0
            assert [row[key] for key in ("p10", "p50", "p90")] == [9000.0] * 3
        else:
            totals = arrays[row["model"]].sum(axis=0)
            assert row["expected_tokens"] == pytest.approx(totals.mean())
            assert [row[key] for key in ("p10", "p50", "p90")] == pytest.approx(
                np.quantile(totals, [.1, .5, .9], method="linear")
            )
