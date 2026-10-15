"""Config loading for `model_pricing` (design §4, §9).

Three files, all optional, each absent -> documented defaults (§4.4):

- `model_pricing.yaml` -- which cloud channels to price, the region->geo map, per-source
  enable toggles (§4.2). Absent -> vendor channels only.
- `channel_config.yaml` -- which channel each org buys each vendor's models through
  (§4.3). Absent -> every org uses the vendor channel.
- `aliases.yaml` -- observed strings no normalization rule can read, e.g. a Bedrock
  application inference-profile ARN, mapped straight to a `model_id` (§5.3). Absent ->
  only `norm`/`canon` matching.

Channel-id parsing lives here too (`validate_channel_id`, `channel_cloud`) because it is
config surface: it is `model_pricing.yaml`'s `channels` list and `channel_config.yaml`'s
values that need validating, and nothing else in the package cares what a channel id
looks like syntactically -- everywhere else a channel is just an opaque string key.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from common.model_pricing.errors import ConfigError

#: Anchored to the repository, not the caller's cwd, so a consumer run from any
#: directory (e.g. econ-forecast's utils) finds the same config and catalog.
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = REPO_ROOT / "config/model_pricing"

#: Default `model_string` prefix -> vendor map for official-source models (§5.2): an
#: AWS/Azure record whose reduced model string starts with one of these prefixes, but
#: does not normalize to any LiteLLM model, is still a real model -- just one LiteLLM's
#: vendor list has dropped or never carried. OpenAI's `o<digit>` reasoning family
#: (`o1`, `o3`, ...) is not a fixed string prefix, so it is not an entry here -- see
#: `_O_SERIES_RE` and `infer_vendor` below.
DEFAULT_VENDOR_PREFIXES: dict[str, str] = {
    "claude": "anthropic",
    "gpt": "openai",
    "gemini": "gemini",
}

#: OpenAI's `o<digit>` reasoning-model family (`o1`, `o1-mini`, `o3`, ...) -- a shape,
#: not a fixed string, so it is a hardcoded rule (§5.2: "gpt / o<digit> -> openai"),
#: not a `vendor_prefixes` entry.
_O_SERIES_RE = re.compile(r"^o\d")


def infer_vendor(model_string: str, vendor_prefixes: dict[str, str]) -> str | None:
    """The vendor for `model_string` (already reduced by its adapter, §5.3) per §5.2's
    "official-source models" rule, or `None` if no known prefix matches (§5.2: "No
    known prefix -> unmapped").

    Checked longest-prefix-first so a future `vendor_prefixes` entry that is itself a
    prefix of another never shadows the more specific one. The `o<digit>` family is
    checked first and separately, since it cannot be expressed as a plain string
    prefix in `vendor_prefixes`.
    """
    if _O_SERIES_RE.match(model_string):
        return "openai"
    for prefix in sorted(vendor_prefixes, key=len, reverse=True):
        if model_string.startswith(prefix):
            return vendor_prefixes[prefix]
    return None

#: The three vendor channels (§4.1) -- always priced, never listed in `channels`.
VENDOR_CHANNELS = ("anthropic", "openai", "gemini")

#: Cloud channel prefix -> its two legal `scope` values (§4.1).
_CLOUD_SCOPES: dict[str, tuple[str, str]] = {
    "aws_bedrock": ("regional", "global"),
    "azure": ("global", "data_zone"),
    "gcp_vertex": ("regional", "global"),
}

_REGION_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: region -> LiteLLM geo prefix (§6.4), for the LiteLLM fallback when a cloud channel's
#: own source lacks a model. Not exhaustive by design -- a deployment building channels
#: in a region absent here overrides it via `model_pricing.yaml`'s `geo` map; leaving it
#: out silently means that region's LiteLLM-fallback rows go unmapped, which the refresh
#: report's `unmapped` list would surface.
DEFAULT_GEO: dict[str, str] = {
    "us-east-1": "us",
    "us-east-2": "us",
    "us-west-2": "us",
    "eu-central-1": "eu",
    "eu-west-1": "eu",
    "ap-northeast-1": "jp",
    "ap-southeast-2": "au",
    "eastus2": "us",
    "eastus": "us",
    "westeurope": "eu",
    "japaneast": "jp",
    "australiaeast": "au",
}


def validate_channel_id(channel: str) -> str:
    """Validate a channel id (§4.1): a vendor channel, or `<prefix>:<region>:<scope>`
    for a cloud channel with `prefix`/`scope` from the fixed table. Returns `channel`
    unchanged; raises `ValueError` (callers upgrade this to `ConfigError` when a config
    file is involved -- see `_load`)."""
    if channel in VENDOR_CHANNELS:
        return channel
    parts = channel.split(":")
    if len(parts) != 3:
        raise ValueError(
            f"not a valid channel id: {channel!r} "
            f"(expected one of {VENDOR_CHANNELS} or '<prefix>:<region>:<scope>')"
        )
    prefix, region, scope = parts
    scopes = _CLOUD_SCOPES.get(prefix)
    if scopes is None:
        raise ValueError(
            f"unknown channel prefix {prefix!r} in {channel!r} "
            f"(expected one of {sorted(_CLOUD_SCOPES)})"
        )
    if not region or not _REGION_RE.match(region):
        raise ValueError(f"invalid region {region!r} in channel id {channel!r}")
    if scope not in scopes:
        raise ValueError(
            f"invalid scope {scope!r} for {prefix!r} channel {channel!r} "
            f"(expected one of {scopes})"
        )
    return channel


def channel_cloud(channel: str) -> tuple[str, str, str] | None:
    """`(prefix, region, scope)` for a cloud channel id; `None` for a vendor channel.
    Does not validate -- call `validate_channel_id` first if the input is untrusted."""
    if channel in VENDOR_CHANNELS:
        return None
    prefix, region, scope = channel.split(":")
    return prefix, region, scope


class SourceToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | Literal["auto"] = True


class SourcesToggles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aws_price_list: SourceToggle = Field(default_factory=SourceToggle)
    azure_retail: SourceToggle = Field(default_factory=SourceToggle)
    #: "auto" (default): enabled only when `GCP_BILLING_API_KEY` is set (§4.2, §6.2).
    gcp_billing: SourceToggle = Field(default_factory=lambda: SourceToggle(enabled="auto"))


class ModelPricingConfig(BaseModel):
    """`model_pricing.yaml` (§4.2). Absent file -> `ModelPricingConfig()`: no cloud
    channels, the built-in geo map, every source enabled (gcp_billing auto)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    #: Cloud channels to price (§4.1); vendor channels are always priced and must not
    #: appear here.
    channels: list[str] = Field(default_factory=list)
    geo: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_GEO))
    sources: SourcesToggles = Field(default_factory=SourcesToggles)
    #: `model_string` prefix -> vendor, for official-source models (§5.2). Defaults
    #: cover Claude/GPT/Gemini; extend (don't just override) for another vendor's cloud
    #: naming by merging with `DEFAULT_VENDOR_PREFIXES` in the YAML if ever needed.
    vendor_prefixes: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_VENDOR_PREFIXES))

    @field_validator("channels")
    @classmethod
    def _validate_channels(cls, v: list[str]) -> list[str]:
        for c in v:
            if c in VENDOR_CHANNELS:
                raise ValueError(
                    f"{c!r} is a vendor channel, always priced -- do not list it in `channels`"
                )
            validate_channel_id(c)
        return v


class ChannelConfig(BaseModel):
    """`channel_config.yaml` (§4.3). Absent file -> `ChannelConfig()`: every org uses
    the vendor channel for every vendor."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    orgs: dict[str, dict[str, str]] = Field(default_factory=dict)

    @field_validator("orgs")
    @classmethod
    def _validate_orgs(cls, v: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        for org, vendors in v.items():
            for vendor, channel in vendors.items():
                if vendor not in VENDOR_CHANNELS:
                    raise ValueError(
                        f"org {org!r}: {vendor!r} is not a vendor "
                        f"(expected one of {VENDOR_CHANNELS})"
                    )
                validate_channel_id(channel)
        return v

    def channel_for(self, org_id: str, vendor: str) -> str:
        """The channel `org_id` buys `vendor`'s models through; default = the vendor
        channel itself (§4.3 table). Does not check that the channel is priced --
        "priced" is a fact about a loaded catalog, checked by `Catalog.for_org`, not
        about this file in isolation."""
        return self.orgs.get(org_id, {}).get(vendor, vendor)


class AliasesConfig(BaseModel):
    """`aliases.yaml` (§5.3): observed strings no rule can read (e.g. a Bedrock
    application inference-profile ARN) mapped straight to a `model_id`. Absent file ->
    `AliasesConfig()`: normalization falls through to `norm`/`canon` only."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    aliases: dict[str, str] = Field(default_factory=dict)


_ENV_REF = re.compile(r"\$\{(\w+)\}")


def _read_yaml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    # `${VAR}` substitution, e.g. for a credentials path (the same substitution the other service config
    # loaders do). Unset -> empty string; no file here has a field that is silently wrong
    # when blank (every substitutable field is a free-form string).
    text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), text)
    return yaml.safe_load(text) or {}


def _load(path: Path, model: type[BaseModel]) -> BaseModel:
    if not path.exists():
        return model()
    try:
        data = _read_yaml(path)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_model_pricing_config(
    path: str | Path = DEFAULT_ROOT / "model_pricing.yaml",
) -> ModelPricingConfig:
    result = _load(Path(path), ModelPricingConfig)
    assert isinstance(result, ModelPricingConfig)
    return result


def load_channel_config(path: str | Path = DEFAULT_ROOT / "channel_config.yaml") -> ChannelConfig:
    result = _load(Path(path), ChannelConfig)
    assert isinstance(result, ChannelConfig)
    return result


def load_aliases_config(path: str | Path = DEFAULT_ROOT / "aliases.yaml") -> AliasesConfig:
    result = _load(Path(path), AliasesConfig)
    assert isinstance(result, AliasesConfig)
    return result
