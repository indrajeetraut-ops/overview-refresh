"""F0 -- the backend-owned contract primitives (`server/api/schemas/envelope.py`).

These are the shapes every manager view model is built from; pin them so a change is
deliberate and shows up in review (and, via the OpenAPI export, in the generated TS diff).
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from server.api.schemas import (
    AggregateMode,
    Envelope,
    Money,
    SessionMode,
    Sourced,
    Unavailable,
)


class _Sample(BaseModel):
    n: int


def test_envelope_carries_data_and_an_aggregate_mode_block():
    env = Envelope[_Sample](
        data=_Sample(n=3),
        mode=AggregateMode(modes_present=["both"], sessions_by_mode={"both": 2}),
    )
    dumped = env.model_dump()
    assert dumped["data"] == {"n": 3}
    assert dumped["mode"] == {"modes_present": ["both"], "sessions_by_mode": {"both": 2}}
    assert dumped["unavailable"] is None


def test_envelope_null_data_with_unavailable_is_the_dark_whole_block_shape():
    env = Envelope[_Sample](
        data=None,
        mode=SessionMode(datasource="otel_only", vendor="anthropic"),
        unavailable={"block": "prs", "requires": "github"},
    )
    dumped = env.model_dump()
    assert dumped["data"] is None
    assert dumped["unavailable"] == {"block": "prs", "requires": "github"}


def test_money_reports_basis_and_completeness_not_a_bare_number():
    m = Money(usd_micros=1_250_000, cost_basis="otel_vendor,estimated", cost_complete=False)
    assert m.model_dump() == {
        "usd_micros": 1_250_000,
        "cost_basis": "otel_vendor,estimated",
        "cost_complete": False,
    }


def test_sourced_is_generic_over_its_value():
    assert Sourced[int](value=200, source="turn_duration").model_dump() == {
        "value": 200, "source": "turn_duration",
    }


def test_unavailable_sub_block_shape():
    assert Unavailable(unavailable={"block": "provenance", "requires": "jsonl"}).model_dump() == {
        "unavailable": {"block": "provenance", "requires": "jsonl"},
    }


def test_models_forbid_unknown_fields():
    """`extra="forbid"` everywhere -- a typo'd key in a service payload is a test failure, not
    a silently-dropped field the UI then can't find."""
    with pytest.raises(ValidationError):
        Money(usd_micros=1, colour="#1A7A4A")  # type: ignore[call-arg]


def test_forecast_models_forbid_unknown_fields_and_carry_no_presentation():
    """T6: the forecast view models are strict, and no field name is a colour or a
    pre-formatted string (schemas/README.md)."""
    import server.api.schemas as schemas
    from server.api.schemas import manager

    names = ["ForecastPreset", "ActualDailyPoint", "ForecastDailyPoint", "ForecastLanding",
             "RunsOutOn", "Runway", "ForecastPeriod", "ForecastSeries", "ForecastView",
             "BudgetForecast", "PlanPeriodRef", "PlanNeeded", "TokensByModel",
             "PlanPeriodFigures", "PlanPresetFigures", "PlanRow", "BudgetPlan"]
    for name in names:
        model = getattr(schemas, name)
        assert model.model_config["extra"] == "forbid", name
        assert getattr(manager, name) is model
        for field in model.model_fields:
            assert not any(bad in field for bad in ("color", "colour", "label_text", "display")), field
    with pytest.raises(ValidationError):
        schemas.Runway(status="lasts", runs_out_probability=0.1, runs_out_on={}, colour="red")
    with pytest.raises(ValidationError):
        schemas.Runway(status="maybe", runs_out_probability=0.1, runs_out_on={})


def test_forecast_envelopes_wrap_the_models_like_every_manager_view():
    from datetime import date
    from server.api.schemas import AggregateMode, BudgetForecastEnvelope, BudgetPlanEnvelope

    env = BudgetForecastEnvelope(
        data={"scope_type": "org", "scope_id": "default", "forecast": None},
        mode=AggregateMode(modes_present=[], sessions_by_mode={}))
    assert env.model_dump()["data"]["forecast"] is None
    plan = BudgetPlanEnvelope(data=None, mode=AggregateMode(modes_present=[], sessions_by_mode={}),
                              unavailable={"block": "forecast", "requires": "forecast"})
    assert plan.data is None and plan.unavailable.block == "forecast"
    from server.api.schemas import RunsOutOn
    assert RunsOutOn(p50=date(2026, 10, 3)).model_dump() == {
        "p10": None, "p50": date(2026, 10, 3), "p90": None}
