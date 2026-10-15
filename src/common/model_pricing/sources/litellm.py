"""LiteLLM source adapter (design §6.2-§6.4).

The only source that builds the model **registry** (§5.2) -- every other source maps
its own model names against the registry this module returns, so `parse()` here must
run before any other source's `parse()` (build.py enforces the order).

`fetch()` is the only network-touching function; `parse()` is pure and is what the tests
in `test_source_litellm.py` call directly on a small fixture subset (§10.1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from common.model_pricing.catalog import TOKEN_TYPES, SourceRow, UnmappedEntry
from common.model_pricing.config import ModelPricingConfig, channel_cloud
from common.model_pricing.normalize import (
    Registry,
    RegistryCandidate,
    build_registry,
    normalize,
)

GITHUB_COMMITS_API = "https://api.github.com/repos/BerriAI/litellm/commits"
RAW_FILE_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/{sha}/"
    "model_prices_and_context_window.json"
)
_PRICE_FILE_PATH = "model_prices_and_context_window.json"

#: LiteLLM field -> our token_type (§6.3).
_TOKEN_FIELDS: dict[str, str] = {
    "input_cost_per_token": "input",
    "output_cost_per_token": "output",
    "cache_read_input_token_cost": "cache_read",
    "cache_creation_input_token_cost": "cache_write_5m",
    "cache_creation_input_token_cost_above_1hr": "cache_write_1h",
}

#: `litellm_provider` values that identify a *vendor-channel* entry (§5.2): the model is
#: sold directly by the vendor's own API, not via a cloud reseller.
_VENDOR_PROVIDERS = {"anthropic": "anthropic", "openai": "openai", "gemini": "gemini"}

#: Region prefixes (§6.4) that select the Bedrock/Azure listing rules below.
_BEDROCK_GLOBAL_RE = re.compile(r"^global\.(anthropic|openai)\.")
_BEDROCK_REGIONAL_RE = re.compile(r"^(us|eu|jp|apac|au)\.(anthropic|openai)\.")
_VERTEX_PREFIX = "vertex_ai/"
_AZURE_GEO_RE = re.compile(r"^azure(_ai)?/(us|eu)/")
_AZURE_GLOBAL_RE = re.compile(r"^azure(_ai)?/")

_MODES = {"chat", "responses"}


def fetch(commit: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Download the LiteLLM price file (§6.2).

    `commit=None` (default) resolves the latest commit SHA on the default branch via
    the GitHub commits API, then downloads the raw file at that SHA -- so the file and
    its recorded provenance are always for the exact same commit, never "latest" at two
    different moments. `commit` pins a specific SHA (the `update --litellm-commit`
    option, §8), for reproducing a past build.
    """
    with httpx.Client(timeout=30.0) as client:
        if commit is None:
            resp = client.get(GITHUB_COMMITS_API, params={"path": _PRICE_FILE_PATH, "per_page": 1})
            resp.raise_for_status()
            commits = resp.json()
            if not commits:
                raise ValueError("GitHub commits API returned no commits for the price file")
            commit = commits[0]["sha"]
            commit_date = commits[0]["commit"]["committer"]["date"]
        else:
            resp = client.get(f"{GITHUB_COMMITS_API}/{commit}")
            resp.raise_for_status()
            commit_date = resp.json()["commit"]["committer"]["date"]

        raw = client.get(RAW_FILE_URL.format(sha=commit))
        raw.raise_for_status()
        payload = raw.json()

    return payload, {"commit_sha": commit, "commit_date": commit_date}


def _rates_from_entry(entry: dict[str, Any]) -> dict[str, Decimal | None]:
    """LiteLLM's `*_cost_per_token` fields are already USD **per token** -- unlike AWS
    and Azure, which publish USD per one million and need dividing (§6.3's unit row:
    "as is" for LiteLLM vs. "÷ 1,000,000" for the other two)."""
    rates: dict[str, Decimal | None] = dict.fromkeys(TOKEN_TYPES)
    for field, token_type in _TOKEN_FIELDS.items():
        v = entry.get(field)
        if v is None:
            continue
        try:
            rates[token_type] = Decimal(str(v))
        except InvalidOperation:
            rates[token_type] = None
    return rates


def _retired_on(entry: dict[str, Any]) -> date | None:
    raw = entry.get("deprecation_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _is_priceable(entry: Any) -> bool:
    return isinstance(entry, dict) and entry.get("mode") in _MODES


@dataclass(frozen=True)
class _CloudTarget:
    channel: str


def _built_regions(cfg: ModelPricingConfig, prefix: str) -> list[tuple[str, str]]:
    """`(region, scope)` pairs for every configured channel of `prefix` (`aws_bedrock`,
    `azure`, `gcp_vertex`)."""
    out = []
    for ch in cfg.channels:
        cloud = channel_cloud(ch)
        if cloud and cloud[0] == prefix:
            out.append((cloud[1], cloud[2]))
    return out


def _cloud_channels_for_key(key: str, cfg: ModelPricingConfig) -> list[str]:
    """Listing -> channel (§6.4), for one LiteLLM key that is NOT a bare vendor-channel
    entry. Returns the list of configured, *built* cloud channel ids this key's rates
    should also be emitted for (LiteLLM fallback rows), or `[]` if the key does not
    match any listing pattern this adapter recognizes."""
    geo = cfg.geo

    if _BEDROCK_GLOBAL_RE.match(key):
        return [
            f"aws_bedrock:{region}:global"
            for region, scope in _built_regions(cfg, "aws_bedrock")
            if scope == "global"
        ]

    m = _BEDROCK_REGIONAL_RE.match(key)
    if m:
        key_geo = m.group(1)
        return [
            f"aws_bedrock:{region}:regional"
            for region, scope in _built_regions(cfg, "aws_bedrock")
            if scope == "regional" and geo.get(region) == key_geo
        ]

    if key.startswith(_VERTEX_PREFIX):
        return [f"gcp_vertex:{region}:{scope}" for region, scope in _built_regions(cfg, "gcp_vertex")]

    m = _AZURE_GEO_RE.match(key)
    if m:
        key_geo = m.group(2)
        return [
            f"azure:{region}:data_zone"
            for region, scope in _built_regions(cfg, "azure")
            if scope == "data_zone" and geo.get(region) == key_geo
        ]

    if _AZURE_GLOBAL_RE.match(key):
        return [
            f"azure:{region}:global"
            for region, scope in _built_regions(cfg, "azure")
            if scope == "global"
        ]

    return []


def _model_string(key: str) -> str:
    """The part of a LiteLLM key normalize() should read -- everything after the last
    `/`, unchanged otherwise. `norm()` does this same split internally for the final
    `model_id`, but the *registry-building* candidate key must keep the full key (so
    `canon()`/`norm()` grouping and `names_seen` stay keyed on what LiteLLM actually
    calls it); this helper is only used for cloud-listing keys whose model string is
    looked up via `normalize()`, which does its own `/`-splitting -- so it is simply the
    key itself. Kept as a named function so the intent at each call site is legible.
    """
    return key


def parse(
    payload: dict[str, Any], cfg: ModelPricingConfig
) -> tuple[Registry, list[SourceRow], list[UnmappedEntry]]:
    """Build the registry (§5.2) and emit rows for vendor channels plus, via key
    prefixes + the geo map, configured cloud channels (§6.4).

    Returns `(registry, rows, unmapped)`. `rows` always has `source="litellm"`, never
    `litellm_rates` (that field is populated later, in `build.py`'s merge step, only on
    a *different* source's winning row).
    """
    candidates: list[RegistryCandidate] = []
    vendor_entries: dict[str, tuple[str, dict[str, Any]]] = {}  # key -> (vendor, entry)

    for key, entry in payload.items():
        if not _is_priceable(entry):
            continue
        provider = entry.get("litellm_provider")
        vendor = _VENDOR_PROVIDERS.get(provider)
        if vendor is None:
            continue
        rates = _rates_from_entry(entry)
        candidates.append(
            RegistryCandidate(
                key=key,
                vendor=vendor,
                rates=tuple(rates[tt] for tt in TOKEN_TYPES),
            )
        )
        vendor_entries[key] = (vendor, entry)

    registry = build_registry(candidates)

    rows: list[SourceRow] = []
    unmapped: list[UnmappedEntry] = []

    # Vendor-channel rows: one per registry model_id, from whichever key(s) fed it.
    # names_seen may hold several raw keys (identical-rate group, §5.2); rates are the
    # same for all of them by construction, so any one representative entry works, and
    # source_key records the representative key actually used.
    for model_id, model_entry in registry.models.items():
        rep_key = model_entry.names_seen[0]
        vendor, entry = vendor_entries[rep_key]
        rates = _rates_from_entry(entry)
        rows.append(
            SourceRow(
                channel=vendor,
                model_id=model_id,
                source="litellm",
                source_key=rep_key,
                rates=rates,
                retired_on=_retired_on(entry),
            )
        )

    # Cloud-channel rows: every other priceable entry, mapped via listing rules (§6.4)
    # and normalized against the registry just built.
    for key, entry in payload.items():
        if key in vendor_entries:
            continue
        if not _is_priceable(entry):
            continue

        targets = _cloud_channels_for_key(key, cfg)
        if not targets:
            continue  # not a listing pattern this adapter maps (e.g. an unrelated provider)

        result = normalize(_model_string(key), registry)
        if result.status != "matched":
            unmapped.append(
                UnmappedEntry(
                    source="litellm", source_key=key, reason=f"model {result.status}"
                )
            )
            continue

        rates = _rates_from_entry(entry)
        retired_on = _retired_on(entry)
        for channel in targets:
            rows.append(
                SourceRow(
                    channel=channel,
                    model_id=result.model_id,
                    source="litellm",
                    source_key=key,
                    rates=rates,
                    retired_on=retired_on,
                )
            )

    return registry, rows, unmapped
