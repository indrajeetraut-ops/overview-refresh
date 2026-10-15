from datetime import date
from decimal import Decimal

from common.model_pricing import Pricer
from server.api.single_user import service


def test_pricing_models_returns_the_effective_rates_as_plain_strings():
    pricing = Pricer.load(None, None)
    result = service.pricing_models(pricing, at=date(2026, 10, 1))
    assert result["as_of"] == "2026-10-01"
    opus = result["models"]["claude-opus-5"]
    # numeric contract: five rates, USD per million; strings, never floats
    assert {k: Decimal(v) for k, v in opus.items() if k != "effective_from"} == {
        "input": Decimal("5.00"), "output": Decimal("25.00"), "cache_read": Decimal("0.50"),
        "cache_write_5m": Decimal("6.25"), "cache_write_1h": Decimal("10.00"),
    }
    assert opus["input"] == "5.00" and opus["cache_read"] == "0.50"
    assert all(isinstance(v, str) for v in opus.values())
    assert opus["effective_from"] == "2026-09-25"     # start_date of the catalog row


def test_pricing_models_keys_are_registry_ids_not_dated_ids():
    # §4 #8: the catalog normalises the dated Haiku id to its registry id.
    models = service.pricing_models(Pricer.load(None, None), at=date(2026, 10, 1))["models"]
    assert "claude-haiku-4-5" in models
    assert "claude-haiku-4-5-20251001" not in models


def test_pricing_models_survives_a_missing_pricing_table():
    result = service.pricing_models(None, at=date(2026, 1, 1))
    assert result == {"as_of": "2026-01-01", "models": {}}


def test_per_million_str_keeps_extra_precision_exact():
    assert service._per_million_str(Decimal("0.0000003125")) == "0.3125"
    assert service._per_million_str(Decimal("0.000005")) == "5.00"
    assert service._per_million_str(None) is None


def test_shipped_catalog_serves_without_error():
    result = service.pricing_models(Pricer.load(None, None))
    assert isinstance(result["models"], dict) and result["models"]
