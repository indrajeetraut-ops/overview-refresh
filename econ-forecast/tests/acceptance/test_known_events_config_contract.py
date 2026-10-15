"""Black-box acceptance checks for known_events static config validation (design §7).

Mirrors test_weekday_config_contract.py's conventions: config.py's static checks (dates
well-formed, multiplier/spread bounds, nonempty names) are exercised here through
load_config against a real dataset-backed config file. Dataset/horizon cross-checks
(name resolution, window-in-horizon) require the loaded dataset/window and are covered
in test_known_events_overlay.py instead.
"""

import pytest
import yaml

from forecast.forecasting.config import load_config
from forecast.forecasting.errors import InputError


def _with_known_events(path, known_events):
    raw = yaml.safe_load(path.read_text())
    raw["known_events"] = known_events
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


def test_known_events_is_optional_and_defaults_empty(
    tmp_path, neutral_dataset, forecast_config_factory
):
    path = forecast_config_factory(tmp_path / "config.yaml", neutral_dataset)
    cfg = load_config(path)
    assert cfg.known_events.is_empty()


def test_well_formed_known_events_round_trip(
    tmp_path, neutral_dataset, forecast_config_factory
):
    path = forecast_config_factory(tmp_path / "config.yaml", neutral_dataset)
    _with_known_events(path, {
        "org": {"start_date": "2026-08-01", "end_date": "2026-08-05",
                "multiplier": 1.4, "spread": 0.15},
        "teams": {"Team A": {"start_date": "2026-08-01", "end_date": "2026-08-05",
                              "multiplier": 2.0, "spread": 0.0}},
        "projects": {}, "agents": {},
    })
    cfg = load_config(path)
    assert cfg.known_events.org.multiplier == 1.4
    assert cfg.known_events.teams["Team A"].spread == 0.0
    assert not cfg.known_events.is_empty()


@pytest.mark.parametrize("start,end", [("2026-08-05", "2026-08-01")])
def test_end_before_start_is_rejected(start, end, tmp_path, neutral_dataset, forecast_config_factory):
    path = forecast_config_factory(tmp_path / "config.yaml", neutral_dataset)
    _with_known_events(path, {
        "org": {"start_date": start, "end_date": end, "multiplier": 1.0, "spread": 0.0}
    })
    with pytest.raises(InputError, match="end_date"):
        load_config(path)


@pytest.mark.parametrize("multiplier,spread", [(-0.1, 0.0), (1.0, -0.1), (1.0, 1.5)])
def test_bad_multiplier_or_spread_is_rejected(
    multiplier, spread, tmp_path, neutral_dataset, forecast_config_factory
):
    path = forecast_config_factory(tmp_path / "config.yaml", neutral_dataset)
    _with_known_events(path, {
        "org": {"start_date": "2026-08-01", "end_date": "2026-08-05",
                "multiplier": multiplier, "spread": spread}
    })
    with pytest.raises(InputError):
        load_config(path)


@pytest.mark.parametrize("value", [True, "1.0", float("nan"), float("inf")])
def test_non_finite_or_non_numeric_multiplier_is_rejected(
    value, tmp_path, neutral_dataset, forecast_config_factory
):
    path = forecast_config_factory(tmp_path / "config.yaml", neutral_dataset)
    _with_known_events(path, {
        "org": {"start_date": "2026-08-01", "end_date": "2026-08-05",
                "multiplier": value, "spread": 0.0}
    })
    with pytest.raises(InputError):
        load_config(path)


def test_malformed_date_string_is_rejected(tmp_path, neutral_dataset, forecast_config_factory):
    path = forecast_config_factory(tmp_path / "config.yaml", neutral_dataset)
    _with_known_events(path, {
        "org": {"start_date": "not-a-date", "end_date": "2026-08-05",
                "multiplier": 1.0, "spread": 0.0}
    })
    with pytest.raises(InputError):
        load_config(path)


def test_empty_team_or_project_or_agent_name_is_rejected(
    tmp_path, neutral_dataset, forecast_config_factory
):
    path = forecast_config_factory(tmp_path / "config.yaml", neutral_dataset)
    _with_known_events(path, {
        "teams": {"": {"start_date": "2026-08-01", "end_date": "2026-08-05",
                       "multiplier": 1.0, "spread": 0.0}}
    })
    with pytest.raises(InputError):
        load_config(path)


def test_unknown_known_events_field_is_rejected(tmp_path, neutral_dataset, forecast_config_factory):
    path = forecast_config_factory(tmp_path / "config.yaml", neutral_dataset)
    _with_known_events(path, {
        "org": {"start_date": "2026-08-01", "end_date": "2026-08-05",
                "multiplier": 1.0, "spread": 0.0, "unexpected": True}
    })
    with pytest.raises(InputError):
        load_config(path)


def test_default_org_id_required_when_known_events_is_set(
    tmp_path, neutral_dataset, forecast_config_factory
):
    path = forecast_config_factory(
        tmp_path / "config.yaml", neutral_dataset,
        scopes=[{"type": "organization", "org_id": "org-a"}],
        default_org_id=None,
    )
    _with_known_events(path, {
        "org": {"start_date": "2026-08-01", "end_date": "2026-08-05",
                "multiplier": 1.0, "spread": 0.0}
    })
    with pytest.raises(InputError, match="default_org_id"):
        load_config(path)
