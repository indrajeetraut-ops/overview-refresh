"""`sources.litellm.parse` on a captured fixture subset (design §6.2-§6.4, §10.1).

Fixture: `fixtures/litellm_subset.json`, extracted from a real LiteLLM price file
(commit `d3462c65`) by `scripts_extract_litellm.py`-style selection (see the module
docstring in that file for which keys and why). Covers: vendor-channel entries for the
registry: Sonnet 5 / Opus 4.8 / Haiku 4.5 / a gemini entry; the `gpt-4o` dated-variant
conflict; Bedrock `us.`/`global.` rows; Azure `azure/`, `azure/eu/`, `azure_ai/` rows;
a Vertex row; a genuinely unmapped Vertex entry (`gemini-3-pro-preview`, no matching
vendor-channel model in this subset); a model without cache-write fields (`gpt-5.6-sol`
has none; `gpt-5.5` has none either -- both feed the build.py derive-rate test later).
"""
import json
from pathlib import Path
from decimal import Decimal

import pytest

from common.model_pricing.config import ModelPricingConfig
from common.model_pricing.sources import litellm

FIXTURE = Path(__file__).parent / "fixtures" / "litellm_subset.json"


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def cfg() -> ModelPricingConfig:
    return ModelPricingConfig.model_validate(
        {
            "channels": [
                "aws_bedrock:us-east-1:regional",
                "aws_bedrock:us-east-1:global",
                "azure:eastus2:global",
                "azure:westeurope:data_zone",
                "gcp_vertex:us-central1:regional",
            ],
            "geo": {
                "us-east-1": "us",
                "eastus2": "us",
                "westeurope": "eu",
                "us-central1": "us",
            },
        }
    )


def _row(rows, channel, model_id):
    matches = [r for r in rows if r.channel == channel and r.model_id == model_id]
    assert len(matches) == 1, f"expected exactly one row for {channel}/{model_id}, got {matches}"
    return matches[0]


def test_registry_has_the_gpt4o_conflict(payload, cfg):
    registry, rows, unmapped = litellm.parse(payload, cfg)
    assert "gpt-4o" in registry.ambiguous
    for model_id in ("gpt-4o", "gpt-4o-2024-05-13", "gpt-4o-2024-08-06", "gpt-4o-2024-11-20"):
        assert model_id in registry.models


def test_vendor_channel_rows(payload, cfg):
    registry, rows, unmapped = litellm.parse(payload, cfg)
    row = _row(rows, "anthropic", "claude-sonnet-5")
    assert row.source == "litellm"
    assert row.rates["input"] == Decimal("0.000002")
    assert row.rates["output"] == Decimal("0.00001")


def test_deprecation_date_becomes_retired_on(payload, cfg):
    registry, rows, unmapped = litellm.parse(payload, cfg)
    row = _row(rows, "openai", "gpt-4o-2024-05-13")
    assert row.retired_on is not None
    assert row.retired_on.isoformat() == "2026-10-23"


def test_bedrock_regional_and_global_rows(payload, cfg):
    registry, rows, unmapped = litellm.parse(payload, cfg)
    regional = _row(rows, "aws_bedrock:us-east-1:regional", "claude-sonnet-5")
    assert regional.source_key == "us.anthropic.claude-sonnet-5"
    assert regional.rates["input"] == Decimal("0.0000022")

    glob = _row(rows, "aws_bedrock:us-east-1:global", "claude-sonnet-5")
    assert glob.source_key == "global.anthropic.claude-sonnet-5"
    assert glob.rates["input"] == Decimal("0.000002")


def test_bedrock_regional_and_global_rows_for_openai_prefix(payload, cfg):
    """Regression: Bedrock `us./eu./global.` listing keys occur for `openai.` models
    too, not just `anthropic.` (§6.4) -- `us.openai.gpt-5.6-sol` /
    `global.openai.gpt-5.6-sol` must map the same way `us.anthropic....` /
    `global.anthropic....` do, into `aws_bedrock:<region>:regional` /
    `aws_bedrock:<region>:global` for `model_id` `gpt-5-6-sol`."""
    registry, rows, unmapped = litellm.parse(payload, cfg)

    regional = _row(rows, "aws_bedrock:us-east-1:regional", "gpt-5-6-sol")
    assert regional.source_key == "us.openai.gpt-5.6-sol"
    assert regional.rates["input"] == Decimal("0.0000044")  # 4.40 / 1M

    glob = _row(rows, "aws_bedrock:us-east-1:global", "gpt-5-6-sol")
    assert glob.source_key == "global.openai.gpt-5.6-sol"
    assert glob.rates["input"] == Decimal("0.000004")  # 4.00 / 1M


def test_bedrock_rows_are_not_duplicated_across_scopes(payload, cfg):
    """Regression: a region configured with both regional and global scope must not
    produce two identical global. rows just because two channel entries share the
    region."""
    registry, rows, unmapped = litellm.parse(payload, cfg)
    globals_ = [
        r
        for r in rows
        if r.channel == "aws_bedrock:us-east-1:global" and r.model_id == "claude-sonnet-5"
    ]
    assert len(globals_) == 1


def test_azure_global_row_from_bare_azure_prefix(payload, cfg):
    registry, rows, unmapped = litellm.parse(payload, cfg)
    row = _row(rows, "azure:eastus2:global", "gpt-5-6-sol")
    assert row.source_key == "azure/gpt-5.6-sol"


def test_azure_ai_prefix_also_maps_to_global(payload, cfg):
    registry, rows, unmapped = litellm.parse(payload, cfg)
    row = _row(rows, "azure:eastus2:global", "claude-sonnet-5")
    assert row.source_key == "azure_ai/claude-sonnet-5"


def test_azure_geo_prefix_maps_to_data_zone(payload, cfg):
    registry, rows, unmapped = litellm.parse(payload, cfg)
    row = _row(rows, "azure:westeurope:data_zone", "gpt-5-6-sol")
    assert row.source_key == "azure/eu/gpt-5.6-sol"
    assert row.rates["input"] == Decimal("0.0000044")


def test_vertex_row(payload, cfg):
    registry, rows, unmapped = litellm.parse(payload, cfg)
    row = _row(rows, "gcp_vertex:us-central1:regional", "claude-sonnet-5")
    assert row.source_key == "vertex_ai/claude-sonnet-5"


def test_unmapped_vertex_entry_is_listed(payload, cfg):
    registry, rows, unmapped = litellm.parse(payload, cfg)
    keys = {u.source_key for u in unmapped}
    assert "vertex_ai/gemini-3-pro-preview" in keys


def test_no_cloud_channels_configured_gives_vendor_rows_only(payload):
    cfg = ModelPricingConfig()
    registry, rows, unmapped = litellm.parse(payload, cfg)
    assert all(r.channel in ("anthropic", "openai", "gemini") for r in rows)


def test_model_without_cache_write_fields_has_none_rates(payload, cfg):
    registry, rows, unmapped = litellm.parse(payload, cfg)
    row = _row(rows, "openai", "gpt-5-5")
    assert row.rates["cache_write_5m"] is None
    assert row.rates["cache_write_1h"] is None
    assert row.rates["input"] == Decimal("0.000005")
