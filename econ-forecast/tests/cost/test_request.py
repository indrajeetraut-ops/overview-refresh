"""T4 accept: forecast-run/dataset resolution, and one failing case per §6.4 rule."""

from datetime import date
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from common.model_pricing.catalog import CatalogVersion, RateRow, RegistryEntry, load_catalog
from common.model_pricing.decimals import per_million_to_per_token as pm

from forecast.cost.errors import InputError
from forecast.cost.request import CostRequest, load_request
from forecast.forecasting.request import ForecastRequest
from forecast.forecasting.runner import run_forecast

_fixture_path = Path(__file__).parents[1] / "acceptance" / "conftest.py"
_spec = importlib.util.spec_from_file_location("_cost_request_test_fixtures", _fixture_path)
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


def _write_scenario(path: Path, **content) -> Path:
    path.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    return path


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


@pytest.fixture
def fixture_catalog(tmp_path):
    """A small, pinned `model_pricing` catalog matching this file's synthetic
    "model-a"/"model-b" series (§6.4's `to`-matched check needs a real catalog, but
    tests must never depend on the live one at `config/model_pricing/catalog`)."""
    catalog_root = tmp_path / "catalog"
    config_root = tmp_path / "catalog-config"
    catalog_root.mkdir(parents=True, exist_ok=True)
    config_root.mkdir(parents=True, exist_ok=True)
    rates = [
        RateRow(
            channel="anthropic", model_id=model_id, start_date=date(2020, 1, 1),
            input=pm(3), output=pm(15), cache_read=pm(0.3),
            source="test", source_key=model_id,
        )
        for model_id in ("model-a", "model-b")
    ]
    models = {
        model_id: RegistryEntry(vendor="anthropic", names_seen=[model_id])
        for model_id in ("model-a", "model-b")
    }
    stub = CatalogVersion(version="pending", built_on=date(2026, 1, 1), models=models, rates=rates)
    version = f"2026-01-01-{stub.compute_hash8()}"
    cv = stub.model_copy(update={"version": version})
    (catalog_root / f"{version}.json").write_text(cv.model_dump_json())
    (catalog_root / "index.json").write_text(json.dumps({"versions": [version]}))
    return load_catalog(root=catalog_root, config_root=config_root)


@pytest.fixture
def forecast_run(tmp_path, daily_dataset_factory, forecast_config_factory):
    dataset = daily_dataset_factory(
        tmp_path / "daily",
        models=(("model-a", "agent-a"), ("model-b", "agent-b")),
        users=("u1", "u2"),
    )
    config = forecast_config_factory(
        tmp_path / "config.yaml",
        dataset,
        scopes=[{"type": "organization"}, {"type": "team", "name": "Team A"}],
    )
    output = tmp_path / "forecast-out"
    run_forecast(config, ForecastRequest(date(2026, 8, 29), "month"), output)
    return output, dataset


def test_baseline_resolves(forecast_run, fixture_catalog):
    output, _ = forecast_run
    resolved = load_request(output, CostRequest(catalog=fixture_catalog))
    assert resolved.org_id == "org-a"
    assert resolved.scenario is None
    assert resolved.scenario_name == "baseline"
    assert resolved.samples > 0
    assert resolved.history_lookback_days == 90
    assert {p["type"] for p in resolved.periods} == {"requested", "calendar"}
    assert resolved.dataset.start <= resolved.dataset.end
    # One rate sheet, covering every forecast model (§3.1/fix 1).
    assert {m.usage_model_id for m in resolved.rate_sheet.models} == {"model-a", "model-b"}
    assert all(m.status == "matched" for m in resolved.rate_sheet.models)


def test_scenario_resolves(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    scenario_path = _write_scenario(
        tmp_path / "team-a-shift.yaml",
        schema="scenario/1.0",
        name="team-a-shift",
        scope={"type": "team", "name": "Team A"},
        changes=[{"model_transfer": {"from": "model-a", "to": "model-b", "fraction": 0.3}}],
    )
    resolved = load_request(output, CostRequest(scenario=scenario_path, catalog=fixture_catalog))
    assert resolved.scenario is not None
    assert resolved.scenario_name == "team-a-shift"


def test_pre_t2_run_raises_input_error(tmp_path, saved_run_factory, fixture_catalog):
    root = saved_run_factory(tmp_path / "old-run")
    with pytest.raises(InputError):
        load_request(root, CostRequest(catalog=fixture_catalog))


def test_dataset_hash_mismatch_raises_input_error(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    request_doc = json.loads((output / "request.json").read_text())
    request_doc["dataset"]["manifest_sha256"] = "0" * 64
    _rewrite_and_rehash(output / "request.json", output / "manifest.json", "request.json", request_doc)
    with pytest.raises(InputError):
        load_request(output, CostRequest(catalog=fixture_catalog))


def test_scenario_name_must_match_file_name(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    scenario_path = _write_scenario(
        tmp_path / "actual-name.yaml",
        schema="scenario/1.0",
        name="different-name",
        scope={"type": "organization"},
        changes=[{"growth": {"factor": 1.1}}],
    )
    with pytest.raises(InputError):
        load_request(output, CostRequest(scenario=scenario_path, catalog=fixture_catalog))


def test_scenario_name_baseline_is_reserved(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    scenario_path = _write_scenario(
        tmp_path / "baseline.yaml",
        schema="scenario/1.0",
        name="baseline",
        scope={"type": "organization"},
        changes=[{"growth": {"factor": 1.1}}],
    )
    with pytest.raises(InputError):
        load_request(output, CostRequest(scenario=scenario_path, catalog=fixture_catalog))


def test_scenario_scope_must_exist_in_forecast_run(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    scenario_path = _write_scenario(
        tmp_path / "ghost-team.yaml",
        schema="scenario/1.0",
        name="ghost-team",
        scope={"type": "team", "name": "Team Ghost"},
        changes=[{"growth": {"factor": 1.1}}],
    )
    with pytest.raises(InputError):
        load_request(output, CostRequest(scenario=scenario_path, catalog=fixture_catalog))


def test_model_transfer_from_must_be_a_series_in_scope(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    scenario_path = _write_scenario(
        tmp_path / "bad-model.yaml",
        schema="scenario/1.0",
        name="bad-model",
        scope={"type": "team", "name": "Team A"},
        changes=[{"model_transfer": {"from": "model-z", "to": "model-b", "fraction": 0.3}}],
    )
    with pytest.raises(InputError):
        load_request(output, CostRequest(scenario=scenario_path, catalog=fixture_catalog))


def test_model_transfer_to_must_be_matched_in_the_rate_sheet(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    scenario_path = _write_scenario(
        tmp_path / "unmatched-destination.yaml",
        schema="scenario/1.0",
        name="unmatched-destination",
        scope={"type": "team", "name": "Team A"},
        # "model-a" is a real series in this fixture's forecast run; "model-ghost" is
        # not in `fixture_catalog` at all (§6.4).
        changes=[{"model_transfer": {"from": "model-a", "to": "model-ghost", "fraction": 0.3}}],
    )
    with pytest.raises(InputError):
        load_request(output, CostRequest(scenario=scenario_path, catalog=fixture_catalog))


def test_model_transfer_fraction_out_of_range(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    scenario_path = _write_scenario(
        tmp_path / "bad-fraction.yaml",
        schema="scenario/1.0",
        name="bad-fraction",
        scope={"type": "team", "name": "Team A"},
        changes=[{"model_transfer": {"from": "model-a", "to": "model-b", "fraction": 1.5}}],
    )
    with pytest.raises(InputError):
        load_request(output, CostRequest(scenario=scenario_path, catalog=fixture_catalog))


def test_agent_transfer_from_must_have_usage_in_scope(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    scenario_path = _write_scenario(
        tmp_path / "bad-agent-from.yaml",
        schema="scenario/1.0",
        name="bad-agent-from",
        scope={"type": "team", "name": "Team A"},
        changes=[{"agent_transfer": {"from": "agent-x", "to": "agent-b", "fraction": 0.5}}],
    )
    with pytest.raises(InputError):
        load_request(output, CostRequest(scenario=scenario_path, catalog=fixture_catalog))


def test_agent_transfer_to_must_have_usage_in_org(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    scenario_path = _write_scenario(
        tmp_path / "bad-agent-to.yaml",
        schema="scenario/1.0",
        name="bad-agent-to",
        scope={"type": "team", "name": "Team A"},
        changes=[{"agent_transfer": {"from": "agent-a", "to": "agent-y", "fraction": 0.5}}],
    )
    with pytest.raises(InputError):
        load_request(output, CostRequest(scenario=scenario_path, catalog=fixture_catalog))


def test_growth_factor_must_be_nonnegative(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    scenario_path = _write_scenario(
        tmp_path / "bad-growth.yaml",
        schema="scenario/1.0",
        name="bad-growth",
        scope={"type": "organization"},
        changes=[{"growth": {"factor": -0.1}}],
    )
    with pytest.raises(InputError):
        load_request(output, CostRequest(scenario=scenario_path, catalog=fixture_catalog))


def test_token_factor_must_be_positive(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    scenario_path = _write_scenario(
        tmp_path / "bad-token-factor.yaml",
        schema="scenario/1.0",
        name="bad-token-factor",
        scope={"type": "team", "name": "Team A"},
        changes=[
            {
                "model_transfer": {
                    "from": "model-a",
                    "to": "model-b",
                    "fraction": 0.3,
                    "token_factor": 0,
                }
            }
        ],
    )
    with pytest.raises(InputError):
        load_request(output, CostRequest(scenario=scenario_path, catalog=fixture_catalog))


def test_effective_from_must_fall_within_forecast_dates(tmp_path, forecast_run, fixture_catalog):
    output, _ = forecast_run
    scenario_path = _write_scenario(
        tmp_path / "bad-effective-from.yaml",
        schema="scenario/1.0",
        name="bad-effective-from",
        scope={"type": "organization"},
        changes=[{"growth": {"factor": 1.2, "effective_from": "2020-01-01"}}],
    )
    with pytest.raises(InputError):
        load_request(output, CostRequest(scenario=scenario_path, catalog=fixture_catalog))
