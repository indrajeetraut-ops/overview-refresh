"""Config loading (design §4, §9): absent files -> defaults; invalid channel ids and
unknown vendors -> `ConfigError`."""
from pathlib import Path

import pytest
import yaml

from common.model_pricing.config import (
    DEFAULT_VENDOR_PREFIXES,
    ChannelConfig,
    ModelPricingConfig,
    channel_cloud,
    infer_vendor,
    load_aliases_config,
    load_channel_config,
    load_model_pricing_config,
    validate_channel_id,
)
from common.model_pricing.errors import ConfigError


# ------------------------------------------------------------------ absent files -> defaults

def test_absent_model_pricing_yaml_gives_vendor_channels_only(tmp_path):
    cfg = load_model_pricing_config(tmp_path / "does-not-exist.yaml")
    assert cfg.channels == []
    assert cfg.sources.gcp_billing.enabled == "auto"
    assert cfg.geo["us-east-1"] == "us"


def test_absent_channel_config_yaml_defaults_every_org_to_vendor(tmp_path):
    cfg = load_channel_config(tmp_path / "nope.yaml")
    assert cfg.channel_for("talium.ai", "anthropic") == "anthropic"


def test_absent_aliases_yaml_gives_empty_aliases(tmp_path):
    cfg = load_aliases_config(tmp_path / "nope.yaml")
    assert cfg.aliases == {}


# ------------------------------------------------------------------ channel id validation

@pytest.mark.parametrize(
    "channel",
    [
        "anthropic",
        "openai",
        "gemini",
        "aws_bedrock:us-east-1:regional",
        "aws_bedrock:us-east-1:global",
        "azure:eastus2:data_zone",
        "azure:eastus2:global",
        "gcp_vertex:us-central1:regional",
    ],
)
def test_valid_channel_ids(channel):
    assert validate_channel_id(channel) == channel


@pytest.mark.parametrize(
    "channel",
    [
        "not-a-channel",
        "aws_bedrock:us-east-1",  # missing scope
        "aws_bedrock:us-east-1:data_zone",  # wrong scope for aws_bedrock
        "azure:eastus2:regional",  # wrong scope for azure
        "unknown_prefix:us-east-1:regional",
        "aws_bedrock::regional",  # empty region
    ],
)
def test_invalid_channel_ids(channel):
    with pytest.raises(ValueError):
        validate_channel_id(channel)


def test_channel_cloud_splits_cloud_ids():
    assert channel_cloud("aws_bedrock:us-east-1:regional") == (
        "aws_bedrock",
        "us-east-1",
        "regional",
    )
    assert channel_cloud("anthropic") is None


# ------------------------------------------------------------------ model_pricing.yaml validation

def test_model_pricing_yaml_rejects_vendor_channel_in_channels_list(tmp_path):
    p = tmp_path / "model_pricing.yaml"
    p.write_text(yaml.safe_dump({"channels": ["anthropic"]}))
    with pytest.raises(ConfigError):
        load_model_pricing_config(p)


def test_model_pricing_yaml_rejects_invalid_channel(tmp_path):
    p = tmp_path / "model_pricing.yaml"
    p.write_text(yaml.safe_dump({"channels": ["aws_bedrock:us-east-1:middle-earth"]}))
    with pytest.raises(ConfigError):
        load_model_pricing_config(p)


def test_model_pricing_yaml_loads_valid_channels(tmp_path):
    p = tmp_path / "model_pricing.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "channels": ["aws_bedrock:us-east-1:regional", "azure:eastus2:data_zone"],
                "geo": {"us-east-1": "us"},
            }
        )
    )
    cfg = load_model_pricing_config(p)
    assert cfg.channels == ["aws_bedrock:us-east-1:regional", "azure:eastus2:data_zone"]


def test_model_pricing_yaml_rejects_unknown_key(tmp_path):
    p = tmp_path / "model_pricing.yaml"
    p.write_text(yaml.safe_dump({"typo_field": 1}))
    with pytest.raises(ConfigError):
        load_model_pricing_config(p)


# ------------------------------------------------------------------ channel_config.yaml validation

def test_channel_config_yaml_rejects_unknown_vendor(tmp_path):
    p = tmp_path / "channel_config.yaml"
    p.write_text(yaml.safe_dump({"orgs": {"talium.ai": {"not_a_vendor": "anthropic"}}}))
    with pytest.raises(ConfigError):
        load_channel_config(p)


def test_channel_config_yaml_rejects_invalid_channel(tmp_path):
    p = tmp_path / "channel_config.yaml"
    p.write_text(yaml.safe_dump({"orgs": {"talium.ai": {"anthropic": "not-a-channel"}}}))
    with pytest.raises(ConfigError):
        load_channel_config(p)


def test_channel_config_yaml_channel_for(tmp_path):
    p = tmp_path / "channel_config.yaml"
    p.write_text(
        yaml.safe_dump(
            {"orgs": {"talium.ai": {"anthropic": "aws_bedrock:us-east-1:regional"}}}
        )
    )
    cfg = load_channel_config(p)
    assert cfg.channel_for("talium.ai", "anthropic") == "aws_bedrock:us-east-1:regional"
    assert cfg.channel_for("talium.ai", "openai") == "openai"  # not listed -> vendor
    assert cfg.channel_for("unknown-org", "anthropic") == "anthropic"  # org not listed


def test_invalid_yaml_raises_config_error(tmp_path):
    p = tmp_path / "model_pricing.yaml"
    p.write_text("channels: [unterminated")
    with pytest.raises(ConfigError):
        load_model_pricing_config(p)


# ------------------------------------------------------------------ vendor_prefixes / infer_vendor (§5.2)


def test_model_pricing_config_defaults_vendor_prefixes(tmp_path):
    cfg = load_model_pricing_config(tmp_path / "does-not-exist.yaml")
    assert cfg.vendor_prefixes == DEFAULT_VENDOR_PREFIXES


@pytest.mark.parametrize(
    "model_string,expected_vendor",
    [
        ("claude-sonnet-4", "anthropic"),
        ("claude-3-haiku", "anthropic"),
        ("gpt-5-6-sol", "openai"),
        ("gemini-2-5-flash", "gemini"),
        ("o1", "openai"),
        ("o3-mini", "openai"),
    ],
)
def test_infer_vendor_recognized_prefixes(model_string, expected_vendor):
    assert infer_vendor(model_string, DEFAULT_VENDOR_PREFIXES) == expected_vendor


def test_infer_vendor_unknown_prefix_is_none():
    assert infer_vendor("cohere-generate-model", DEFAULT_VENDOR_PREFIXES) is None
    assert infer_vendor("mistral-large", DEFAULT_VENDOR_PREFIXES) is None


def test_infer_vendor_o_series_not_confused_with_openai_prefix():
    """`o<digit>` is checked before -- and independently of -- the `gpt` prefix, so it
    still resolves even though `vendor_prefixes` has no literal `"o"` entry."""
    assert "o" not in DEFAULT_VENDOR_PREFIXES
    assert infer_vendor("o1-mini", DEFAULT_VENDOR_PREFIXES) == "openai"


def test_infer_vendor_respects_custom_prefixes():
    custom = {"mistral": "mistral_ai"}
    assert infer_vendor("mistral-large", custom) == "mistral_ai"
    assert infer_vendor("claude-sonnet-4", custom) is None  # not in the custom map
