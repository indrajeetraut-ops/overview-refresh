"""F0 drift guard -- the committed `server/api/openapi.json` is the serialised contract the
front-end's TS types are generated from. If a `response_model` changes and this file is not
regenerated, the build is red.

    regenerate:  utils/export_openapi.sh   (or: python -m server.api.cli_export_openapi)
"""
from __future__ import annotations

from server.api.cli_export_openapi import DEFAULT_OUT, _serialise, build_schema


def test_committed_openapi_matches_the_live_schema():
    assert DEFAULT_OUT.exists(), "run utils/export_openapi.sh"
    live = _serialise(build_schema())
    committed = DEFAULT_OUT.read_text(encoding="utf-8")
    assert committed == live, (
        "server/api/openapi.json is stale -- a response_model changed without regenerating. "
        "Run: python -m server.api.cli_export_openapi"
    )


def test_pricing_reference_port_is_typed_in_the_schema():
    """The F0 worked example: `/v1/pricing/models` resolves to `PricingModelsResponse`, and
    that model's fields are all present (a planted rename here without regenerating openapi.json
    fails `test_committed_openapi_matches_the_live_schema`)."""
    schema = build_schema()
    ref = schema["paths"]["/v1/pricing/models"]["get"]["responses"]["200"]["content"][
        "application/json"]["schema"]["$ref"]
    assert ref == "#/components/schemas/PricingModelsResponse"

    model = schema["components"]["schemas"]["PricingModel"]
    assert set(model["properties"]) == {
        "effective_from", "input", "output", "cache_read", "cache_write_5m", "cache_write_1h",
    }
    assert model.get("additionalProperties") is False  # extra="forbid" crossed into the schema


def test_forecast_routes_are_typed_in_the_schema():
    """T6: both forecast routes resolve to their envelope models, `scope_id` is a plain query
    parameter (a repo id has ':' and '/'), and the nested models forbid extras."""
    schema = build_schema()
    get = schema["paths"]["/v1/forecast"]["get"]
    assert get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/Envelope_BudgetForecast_")
    params = {p["name"]: p for p in get["parameters"]}
    assert set(params) == {"scope_type", "scope_id"}
    assert all(p["in"] == "query" and p["required"] for p in params.values())
    plan = schema["paths"]["/v1/forecast/plan"]["get"]
    assert plan["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/Envelope_BudgetPlan_")
    assert plan.get("parameters", []) == []

    models = schema["components"]["schemas"]
    for name in ("BudgetForecast", "ForecastView", "ForecastSeries", "ForecastPeriod", "Runway",
                 "RunsOutOn", "ForecastLanding", "BudgetPlan", "PlanRow", "PlanPeriodFigures"):
        assert models[name].get("additionalProperties") is False, name
    assert set(models["Runway"]["properties"]) == {
        "status", "runs_out_probability", "runs_out_on", "days_short"}
    assert models["Runway"]["properties"]["status"]["enum"] == [
        "lasts", "at_risk", "runs_out", "exhausted"]
    assert set(models["ForecastPeriod"]["properties"]) == {
        "start", "end", "forecast_from", "actual_to_date_usd_micros", "projected", "landing",
        "within_budget_probability", "delta_vs_baseline_usd_micros", "runway"}
    assert models["ActualDailyPoint"]["properties"]["date"]["format"] == "date"
