"""T3 accept: every §8.2 example validates; readers ignore unknown/additive fields."""

from datetime import date

import pytest
from pydantic import ValidationError

from forecast.cost.schemas import (
    CostDailyExpectedRow,
    CostIndex,
    CostManifest,
    CostMix,
    CostPeriodSummary,
    CostRequestDoc,
    ScenarioConfig,
    schema_major,
)


def test_manifest_example_validates():
    manifest = CostManifest.model_validate(
        {
            "status": "complete",
            "created_at": "2026-10-01T09:00:00Z",
            "code_version": "7cf5f13",
            "files": {
                "paths/index.json": {
                    "schema": "cost-index/1.0",
                    "bytes": 18211,
                    "sha256": "a" * 64,
                }
            },
        }
    )
    assert manifest.files["paths/index.json"].schema_ == "cost-index/1.0"


def test_request_example_validates():
    doc = CostRequestDoc.model_validate(
        {
            "org_id": "talium.ai",
            "forecast_run": {
                "name": "sep-forecast",
                "path": "artifacts/sep-forecast",
                "manifest_sha256": "b" * 64,
            },
            "scenario": "baseline",
            "periods": [
                {"type": "requested", "start": "2026-10-01", "end": "2026-10-31"},
                {"type": "calendar", "start": "2026-10-01", "end": "2026-10-31"},
            ],
            "settings": {
                "half_life_days": 14,
                "own_mix_half_weight_days": 7,
                "max_age_days": 90,
            },
        }
    )
    assert doc.periods[0].start == date(2026, 10, 1)
    assert doc.settings.half_life_days == 14


def test_index_example_validates():
    index = CostIndex.model_validate(
        {
            "org_id": "talium.ai",
            "dates": ["2026-10-01", "2026-10-02"],
            "samples": 1000,
            "scopes": [
                {
                    "scope_type": "team",
                    "scope_name": "team1",
                    "status": "complete",
                    "risk_ready": True,
                    "assumptions": ["uncalibrated_model_estimate"],
                    "reasons": [],
                }
            ],
            "series": [
                {
                    "scope_type": "team",
                    "scope_name": "team1",
                    "model": "claude-sonnet-5",
                    "status": "qualified",
                    "path": "paths/team-team1__claude-sonnet-5.npy",
                    "assumptions": ["fixed_mix_estimate"],
                    "reasons": [],
                }
            ],
        }
    )
    assert index.series[0].status == "qualified"


def test_daily_expected_row_example_validates():
    row = CostDailyExpectedRow.model_validate(
        {
            "date": "2026-10-01",
            "scope_type": "team",
            "scope_name": "team1",
            "model": "claude-sonnet-5",
            "agent_type": "claude",
            "usd": {"input": 0.61, "cache_write_5m": 0.05, "cache_write_1h": 0.31, "cache_read": 0.23,
                    "output": 0.45},
            "tokens": {"input": 215855.6, "cache_write_5m": 1200.0, "cache_write_1h": 40000.0,
                       "cache_read": 851766.7, "output": 32028.8},
        }
    )
    assert row.usd.input == 0.61


def test_period_summary_example_validates():
    summary = CostPeriodSummary.model_validate(
        {
            "entries": [
                {
                    "scope_type": "team",
                    "scope_name": "team1",
                    "model": None,
                    "period_type": "requested",
                    "start": "2026-10-01",
                    "end": "2026-10-31",
                    "status": "complete",
                    "expected": 37210.4,
                    "p10": 33020.1,
                    "p50": 36980.0,
                    "p90": 41870.3,
                    "assumptions": [],
                }
            ]
        }
    )
    assert summary.entries[0].model is None


def test_mix_example_validates():
    mix = CostMix.model_validate(
        {
            "entries": [
                {
                    "scope_type": "project",
                    "scope_name": "payment_service_improvement",
                    "model": "claude-sonnet-5",
                    "window": {"start": "2026-07-04", "end": "2026-09-30"},
                    "active_days": 41,
                    "own_weight": 0.85,
                    "shares": [
                        {
                            "agent_type": "claude",
                            "input": 0.001,
                            "cache_write_5m": 0.006,
                            "cache_write_1h": 0.193,
                            "cache_read": 0.77,
                            "output": 0.03,
                        }
                    ],
                    "attribution": {"team": 0.62, "organization": 0.18},
                }
            ]
        }
    )
    assert mix.entries[0].attribution["team"] == 0.62


def test_scenario_config_example_validates():
    config = ScenarioConfig.model_validate(
        {
            "schema": "scenario/1.0",
            "name": "team1-opus-to-sonnet",
            "scope": {"type": "team", "name": "team1"},
            "changes": [
                {
                    "model_transfer": {
                        "from": "claude-opus-5",
                        "to": "claude-sonnet-5",
                        "fraction": 0.3,
                        "token_factor": 1.0,
                        "effective_from": "2026-10-15",
                    }
                },
                {
                    "agent_transfer": {
                        "from": "codex",
                        "to": "claude",
                        "fraction": 0.5,
                        "token_factor": 1.0,
                    }
                },
                {"growth": {"factor": 1.2, "effective_from": "2026-10-15"}},
            ],
        }
    )
    assert config.changes[0].model_transfer.from_ == "claude-opus-5"
    assert config.changes[1].agent_transfer.to == "claude"
    assert config.changes[2].growth.factor == 1.2


def test_readers_ignore_unknown_fields():
    manifest = CostManifest.model_validate(
        {
            "status": "complete",
            "created_at": "now",
            "code_version": "x",
            "files": {},
            "future_top_level_field": "ignored",
        }
    )
    assert manifest.status == "complete"


def test_schema_major_reads_the_major_component():
    assert schema_major("cost-index/1.0") == "1"
    assert schema_major("cost-index/2.3") == "2"


def test_scenario_name_baseline_is_rejected():
    with pytest.raises(ValidationError):
        ScenarioConfig.model_validate(
            {
                "schema": "scenario/1.0",
                "name": "baseline",
                "changes": [{"growth": {"factor": 1.1}}],
            }
        )


def test_scenario_change_requires_exactly_one_type():
    with pytest.raises(ValidationError):
        ScenarioConfig.model_validate(
            {
                "schema": "scenario/1.0",
                "name": "empty-change",
                "changes": [{}],
            }
        )
    with pytest.raises(ValidationError):
        ScenarioConfig.model_validate(
            {
                "schema": "scenario/1.0",
                "name": "double-change",
                "changes": [
                    {
                        "growth": {"factor": 1.1},
                        "model_transfer": {"from": "a", "to": "b", "fraction": 0.5},
                    }
                ],
            }
        )


def test_scenario_organization_scope_defaults_and_rejects_a_name():
    config = ScenarioConfig.model_validate(
        {
            "schema": "scenario/1.0",
            "name": "org-growth",
            "changes": [{"growth": {"factor": 1.1}}],
        }
    )
    assert config.scope.type == "organization"
    assert config.scope.name is None
    with pytest.raises(ValidationError):
        ScenarioConfig.model_validate(
            {
                "schema": "scenario/1.0",
                "name": "org-growth-named",
                "scope": {"type": "organization", "name": "should not be here"},
                "changes": [{"growth": {"factor": 1.1}}],
            }
        )
