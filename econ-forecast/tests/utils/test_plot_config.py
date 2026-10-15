from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forecast.forecasting.errors import InputError
from forecast.utils.plot_config import (
    Band,
    CostEntry,
    Defaults,
    ForecastEntry,
    load_plot_config,
    stem,
)


def _write(path: Path, doc: dict) -> Path:
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def _example_doc(**overrides) -> dict:
    """The design §6 worked example."""
    doc = {
        "schema_version": 1,
        "plotly_js": "../assets/plotly.min.js",
        "defaults": {"format": "both", "band": {"low": 10, "high": 90}, "samples": 0},
        "forecast": [
            {"scope_type": "organization", "samples": 8},
            {"scope_type": "team"},
            {"scope_type": "project", "scope_name": "auth_service_rewrite",
             "models": ["claude-opus-5"]},
        ],
        "cost": [
            {"scope_type": "organization", "views": ["total", "breakdown", "per-model"]},
            {"scope_type": "team", "scope_name": "team3"},
        ],
    }
    doc.update(overrides)
    return doc


def test_design_example_validates(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "plotly.min.js").write_text("/* js */", encoding="utf-8")
    config_dir = tmp_path / "workspace"
    config_dir.mkdir()
    path = _write(config_dir / "plot.yaml", _example_doc())
    config = load_plot_config(path)

    assert config.plotly_js == (tmp_path / "assets" / "plotly.min.js").resolve()
    assert len(config.forecast) == 3
    assert len(config.cost) == 2

    resolved_forecast = [entry.resolve(config.defaults) for entry in config.forecast]
    assert resolved_forecast[0].scope_type == "organization" and resolved_forecast[0].samples == 8
    assert resolved_forecast[1].scope_type == "team" and resolved_forecast[1].scope_name is None
    assert resolved_forecast[2].models == ["claude-opus-5"]

    resolved_cost = [entry.resolve(config.defaults) for entry in config.cost]
    assert resolved_cost[0].views == ["total", "breakdown", "per-model"]
    # views omitted on the second entry -> default [total, breakdown] (§6).
    assert resolved_cost[1].views == ["total", "breakdown"]
    assert resolved_cost[1].scope_name == "team3"


def test_plotly_js_omitted_is_none(tmp_path):
    doc = _example_doc()
    del doc["plotly_js"]
    path = _write(tmp_path / "plot.yaml", doc)
    config = load_plot_config(path)
    assert config.plotly_js is None


def test_plotly_js_absolute_path_is_kept(tmp_path):
    js = tmp_path / "shared" / "plotly.min.js"
    js.parent.mkdir()
    js.write_text("/* js */", encoding="utf-8")
    path = _write(tmp_path / "plot.yaml", _example_doc(plotly_js=str(js)))
    config = load_plot_config(path)
    assert config.plotly_js == js


def test_scope_name_omitted_for_team_or_project_is_legal(tmp_path):
    """Design §5/§6 (Revision 2): unlike the old shape, omitting scope_name for
    team/project is not a validation error -- it means "expand to every scope
    of that type", resolved later against the run (T4), not here."""
    doc = {
        "schema_version": 1,
        "forecast": [{"scope_type": "team"}, {"scope_type": "project"}],
    }
    path = _write(tmp_path / "plot.yaml", doc)
    config = load_plot_config(path)
    assert config.forecast[0].scope_name is None
    assert config.forecast[1].scope_name is None


@pytest.mark.parametrize("bad_doc", [
    {"artifact": "run"},
    {"output_dir": "out"},
    {"forecast-data": {"dataset": "x"}},
    {"config-default": {"format": "html"}},
    {"plots": [{"scope_type": "organization"}]},
])
def test_old_revision_1_keys_are_rejected(tmp_path, bad_doc):
    doc = _example_doc(**bad_doc)
    path = _write(tmp_path / "plot.yaml", doc)
    with pytest.raises(InputError):
        load_plot_config(path)


def test_schema_version_must_be_1(tmp_path):
    path = _write(tmp_path / "plot.yaml", _example_doc(schema_version=2))
    with pytest.raises(InputError):
        load_plot_config(path)


def test_at_least_one_section_required(tmp_path):
    path = _write(tmp_path / "plot.yaml", {"schema_version": 1})
    with pytest.raises(InputError):
        load_plot_config(path)


def test_models_under_cost_is_rejected(tmp_path):
    doc = {"schema_version": 1, "cost": [{"scope_type": "organization", "models": ["m"]}]}
    path = _write(tmp_path / "plot.yaml", doc)
    with pytest.raises(InputError):
        load_plot_config(path)


def test_views_under_forecast_is_rejected(tmp_path):
    doc = {"schema_version": 1, "forecast": [{"scope_type": "organization", "views": ["total"]}]}
    path = _write(tmp_path / "plot.yaml", doc)
    with pytest.raises(InputError):
        load_plot_config(path)


@pytest.mark.parametrize("section", ["forecast", "cost"])
def test_scope_name_on_organization_is_rejected(tmp_path, section):
    doc = {"schema_version": 1, section: [{"scope_type": "organization", "scope_name": "x"}]}
    path = _write(tmp_path / "plot.yaml", doc)
    with pytest.raises(InputError):
        load_plot_config(path)


@pytest.mark.parametrize("bad_doc", [
    {"forecast": [{"scope_type": "team", "band": {"low": 90, "high": 10}}]},
    {"forecast": [{"scope_type": "team", "band": {"low": 0, "high": 100}}]},
    {"forecast": [{"scope_type": "team", "format": "pdf"}]},
    {"forecast": [{"scope_type": "team", "samples": -1}]},
    {"forecast": [{"scope_type": "team", "models": []}]},
    {"forecast": [{"scope_type": "team", "models": ["a", "a"]}]},
    {"cost": [{"scope_type": "team", "views": []}]},
    {"cost": [{"scope_type": "team", "views": ["total", "total"]}]},
    {"cost": [{"scope_type": "team", "views": ["breakdown-and-more"]}]},
    {"forecast": []},
    {"unexpected_field": True, "forecast": [{"scope_type": "organization"}]},
])
def test_invalid_configs_are_rejected(tmp_path, bad_doc):
    doc = {"schema_version": 1, **bad_doc}
    path = _write(tmp_path / "plot.yaml", doc)
    with pytest.raises(InputError):
        load_plot_config(path)


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    path = tmp_path / "plot.yaml"
    path.write_text(
        "schema_version: 1\nforecast:\n  - {scope_type: organization}\n"
        "forecast:\n  - {scope_type: team}\n",
        encoding="utf-8",
    )
    with pytest.raises(InputError):
        load_plot_config(path)


def test_defaults_apply_unless_entry_overrides():
    defaults = Defaults(format="html", samples=5, band=Band(low=5, high=95))
    entry = ForecastEntry.model_validate({"scope_type": "organization"})
    resolved = entry.resolve(defaults)
    assert resolved.format == "html" and resolved.samples == 5
    assert resolved.band.as_tuple() == (5, 95)

    override = ForecastEntry.model_validate({"scope_type": "organization", "format": "png"})
    resolved_override = override.resolve(defaults)
    assert resolved_override.format == "png" and resolved_override.samples == 5


def test_cost_views_default_when_omitted():
    entry = CostEntry.model_validate({"scope_type": "team", "scope_name": "team3"})
    resolved = entry.resolve(Defaults())
    assert resolved.views == ["total", "breakdown"]


def test_band_ordering_and_bounds():
    Band(low=1, high=99)
    with pytest.raises(Exception):
        Band(low=50, high=50)
    with pytest.raises(Exception):
        Band(low=0, high=90)
    with pytest.raises(Exception):
        Band(low=10, high=100)


def test_stem_naming_examples_from_design_section_7():
    # forecast {scope_type: team, scope_name: team1} -> team-team1__models__tokens
    assert stem("team", "team1", "models", "tokens") == "team-team1__models__tokens"
    # cost {scope_type: organization, views: [total, per-model]}, org talium.ai
    assert stem("organization", "talium.ai", "total", "cost") == "organization-talium-ai__total__cost"
    assert (stem("organization", "talium.ai", "claude-opus-5", "cost")
            == "organization-talium-ai__claude-opus-5__cost")
    # cost {scope_type: project} (default views) -> project-<name>__total__cost, __breakdown__cost
    assert stem("project", "auth_service_rewrite", "total", "cost") == "project-auth-service-rewrite__total__cost"
    assert (stem("project", "auth_service_rewrite", "breakdown", "cost")
            == "project-auth-service-rewrite__breakdown__cost")
