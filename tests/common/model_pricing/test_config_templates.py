"""Config templates in `config/model_pricing/` load with `config.py`'s loaders
(design §4.2-§4.3, §5.3).

`model_pricing.yaml` is the real, always-present template (deliverable straight in
`config/model_pricing/`), loaded from its committed path. `channel_config.yaml` and
`aliases.yaml` are OPTIONAL and absent by default, so their templates ship as examples
(`*.example.yaml`); this test copies each to its real name under `tmp_path` and loads
it from there, never writing a copy anywhere under the committed `config/` tree.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from common.model_pricing.config import (
    load_aliases_config,
    load_channel_config,
    load_model_pricing_config,
)

# tests/common/model_pricing/test_config_templates.py -> repo root is four parents up.
REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_ROOT = REPO_ROOT / "config" / "model_pricing"


# ------------------------------------------------------------------ model_pricing.yaml (real file)


def test_model_pricing_yaml_template_loads():
    cfg = load_model_pricing_config(CONFIG_ROOT / "model_pricing.yaml")

    assert cfg.schema_version == 1
    assert "aws_bedrock:us-east-1:regional" in cfg.channels
    assert "aws_bedrock:us-east-1:global" in cfg.channels
    assert "azure:eastus2:data_zone" in cfg.channels
    assert "azure:eastus2:global" in cfg.channels
    # No vendor channel may appear in `channels` (§4.1) -- the loader itself enforces
    # this (ConfigError otherwise), but assert it here too as documentation.
    assert set(cfg.channels).isdisjoint({"anthropic", "openai", "gemini"})

    assert cfg.geo["us-east-1"] == "us"
    assert cfg.geo["eastus2"] == "us"

    # Commented-out `sources:` block -> defaults apply.
    assert cfg.sources.aws_price_list.enabled is True
    assert cfg.sources.azure_retail.enabled is True
    assert cfg.sources.gcp_billing.enabled == "auto"


# ------------------------------------------------------------------ channel_config.example.yaml (copied to the real name)


def test_channel_config_example_loads_under_real_name(tmp_path):
    real = tmp_path / "channel_config.yaml"
    shutil.copy(CONFIG_ROOT / "channel_config.example.yaml", real)

    cfg = load_channel_config(real)

    assert cfg.channel_for("talium.ai", "anthropic") == "aws_bedrock:us-east-1:regional"
    assert cfg.channel_for("talium.ai", "openai") == "openai"  # not listed -> vendor
    assert cfg.channel_for("acme", "openai") == "azure:eastus2:data_zone"
    assert cfg.channel_for("acme", "anthropic") == "anthropic"  # not listed -> vendor
    assert cfg.channel_for("unknown-org", "anthropic") == "anthropic"  # org not listed

    # The example was not left behind next to the real templates.
    assert not (CONFIG_ROOT / "channel_config.yaml").exists()


# ------------------------------------------------------------------ aliases.example.yaml (copied to the real name)


def test_aliases_example_loads_under_real_name(tmp_path):
    real = tmp_path / "aliases.yaml"
    shutil.copy(CONFIG_ROOT / "aliases.example.yaml", real)

    cfg = load_aliases_config(real)

    assert cfg.aliases == {
        "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/abcd1234": (
            "claude-sonnet-5"
        )
    }

    assert not (CONFIG_ROOT / "aliases.yaml").exists()


# ------------------------------------------------------------------ the model_pricing.yaml + channel_config.example.yaml combination is consistent


def test_channel_config_example_channels_are_all_priced_in_model_pricing_yaml(tmp_path):
    """The example channel config only names channels that the committed
    `model_pricing.yaml` actually prices -- otherwise `Catalog.for_org` would raise
    `ConfigError` for anyone who copies both templates as-is (§4.3)."""
    mp_cfg = load_model_pricing_config(CONFIG_ROOT / "model_pricing.yaml")

    real = tmp_path / "channel_config.yaml"
    shutil.copy(CONFIG_ROOT / "channel_config.example.yaml", real)
    cc_cfg = load_channel_config(real)

    priced = set(mp_cfg.channels) | {"anthropic", "openai", "gemini"}
    for org, vendors in cc_cfg.orgs.items():
        for vendor, channel in vendors.items():
            assert channel in priced, (
                f"{org}/{vendor} -> {channel!r} is not priced by model_pricing.yaml's "
                f"channels list"
            )
