"""`norm`/`strip_date`/`canon` (design §5.1), registry grouping (§5.2), and `normalize()`
(§5.3) -- including the `gpt-4o` family conflict, the real case the design cites."""
from decimal import Decimal

import pytest

from common.model_pricing.normalize import (
    ModelEntry,
    Registry,
    RegistryCandidate,
    build_registry,
    canon,
    normalize,
    norm,
    strip_date,
)

# ------------------------------------------------------------------ §5.1 table

@pytest.mark.parametrize(
    "x, expected",
    [
        ("us.anthropic.claude-sonnet-5", "claude-sonnet-5"),
        ("gpt-5.6-sol", "gpt-5-6-sol"),
        ("global.anthropic.claude-opus-5", "claude-opus-5"),
        ("vertex_ai/gemini-3-pro-preview", "gemini-3-pro-preview"),
        ("azure_ai/claude-haiku-4-5", "claude-haiku-4-5"),
        ("azure/eu/gpt-4o-2024-08-06", "gpt-4o-2024-08-06"),
        ("CLAUDE-SONNET-5", "claude-sonnet-5"),  # lowercased
    ],
)
def test_norm(x, expected):
    assert norm(x) == expected


@pytest.mark.parametrize(
    "x, expected",
    [
        ("claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5"),
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("claude-3-5-haiku@20241022", "claude-3-5-haiku"),
        ("claude-3-5-haiku@default", "claude-3-5-haiku"),
        ("gpt-4o-2024-08-06", "gpt-4o"),
        ("model-v2", "model"),
        ("no-date-here", "no-date-here"),
    ],
)
def test_strip_date(x, expected):
    assert strip_date(x) == expected


@pytest.mark.parametrize(
    "x, expected",
    [
        ("gpt-5.5-2026-04-24", "gpt-5-5"),
        ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5"),
    ],
)
def test_canon(x, expected):
    assert canon(x) == expected


# ------------------------------------------------------------------ registry (§5.2)

def _candidate(key: str, vendor: str, **rates) -> RegistryCandidate:
    fields = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")
    values = tuple(
        Decimal(str(rates[f])) if f in rates and rates[f] is not None else None
        for f in fields
    )
    return RegistryCandidate(key=key, vendor=vendor, rates=values)


def test_registry_collapses_identical_rates_to_one_model():
    candidates = [
        _candidate("claude-sonnet-5", "anthropic", input=2, output=10),
    ]
    registry = build_registry(candidates)
    assert "claude-sonnet-5" in registry
    assert registry.models["claude-sonnet-5"] == ModelEntry(
        vendor="anthropic", names_seen=("claude-sonnet-5",)
    )
    assert registry.ambiguous == set()


def test_gpt4o_family_conflict_makes_four_models(monkeypatch=None):
    """Real case from §5.2: gpt-4o, -2024-05-13, -2024-08-06, -2024-11-20 share canon
    gpt-4o; -2024-05-13 differs in rate -> four separate models, gpt-4o ambiguous."""
    same = dict(input=2.5e-6, output=1e-5)
    candidates = [
        _candidate("gpt-4o", "openai", **same),
        _candidate("gpt-4o-2024-05-13", "openai", input=5e-6, output=1.5e-5),
        _candidate("gpt-4o-2024-08-06", "openai", **same),
        _candidate("gpt-4o-2024-11-20", "openai", **same),
    ]
    registry = build_registry(candidates)

    assert "gpt-4o" in registry.ambiguous
    for key in ("gpt-4o", "gpt-4o-2024-05-13", "gpt-4o-2024-08-06", "gpt-4o-2024-11-20"):
        assert key in registry, key
        assert registry.models[key].vendor == "openai"
    assert len(registry.models) == 4


def test_registry_groups_undated_variants_with_identical_rates():
    same = dict(input=1, output=2)
    candidates = [
        _candidate("claude-x-20260101", "anthropic", **same),
        _candidate("claude-x-20260601", "anthropic", **same),
    ]
    registry = build_registry(candidates)
    assert registry.models.keys() == {"claude-x"}
    assert set(registry.models["claude-x"].names_seen) == {
        "claude-x-20260101",
        "claude-x-20260601",
    }


# ------------------------------------------------------------------ normalize() (§5.3)

@pytest.fixture
def gpt4o_registry() -> Registry:
    same = dict(input=2.5e-6, output=1e-5)
    candidates = [
        _candidate("gpt-4o", "openai", **same),
        _candidate("gpt-4o-2024-05-13", "openai", input=5e-6, output=1.5e-5),
        _candidate("gpt-4o-2024-08-06", "openai", **same),
        _candidate("gpt-4o-2024-11-20", "openai", **same),
        _candidate("claude-sonnet-5", "anthropic", input=2, output=10),
    ]
    return build_registry(candidates)


def test_normalize_direct_model_id_hit_wins_over_ambiguous_set(gpt4o_registry):
    """gpt-4o is both a real model_id and an ambiguous canon; step 2 must win."""
    result = normalize("gpt-4o", gpt4o_registry)
    assert result.status == "matched"
    assert result.model_id == "gpt-4o"
    assert result.step == "normalized"


def test_normalize_dated_variant_with_exact_registry_entry_matches(gpt4o_registry):
    result = normalize("gpt-4o-2024-08-06", gpt4o_registry)
    assert result.status == "matched"
    assert result.model_id == "gpt-4o-2024-08-06"
    assert result.step == "normalized"


def test_normalize_unseen_dated_variant_of_ambiguous_family_is_ambiguous(gpt4o_registry):
    result = normalize("gpt-4o-2025-06-01", gpt4o_registry)
    assert result.status == "ambiguous"
    assert result.model_id is None


def test_normalize_canonical_step_for_non_ambiguous_dated_variant(gpt4o_registry):
    """claude-sonnet-5 has no date-suffixed siblings, so a dated form of it (not seen
    at registry-build time) resolves via canon(), step 4 -- not ambiguous."""
    result = normalize("claude-sonnet-5-20261001", gpt4o_registry)
    assert result.status == "matched"
    assert result.model_id == "claude-sonnet-5"
    assert result.step == "canonical"


def test_normalize_region_prefixed_string_matches_via_norm(gpt4o_registry):
    result = normalize("us.anthropic.claude-sonnet-5", gpt4o_registry)
    assert result.status == "matched"
    assert result.model_id == "claude-sonnet-5"
    assert result.step == "normalized"


def test_normalize_unmatched(gpt4o_registry):
    result = normalize("totally-unknown-model", gpt4o_registry)
    assert result.status == "unmatched"
    assert result.model_id is None


def test_normalize_alias_wins_first(gpt4o_registry):
    aliases = {"arn:aws:bedrock:inference-profile/foo": "claude-sonnet-5"}
    result = normalize("arn:aws:bedrock:inference-profile/foo", gpt4o_registry, aliases)
    assert result.status == "matched"
    assert result.model_id == "claude-sonnet-5"
    assert result.step == "alias"
