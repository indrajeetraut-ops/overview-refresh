"""`sources.aws_price_list.parse` on a captured fixture subset (design §6.2-§6.4, §10.1).

Fixture: `fixtures/aws_us_east_1_subset.json`, extracted from a real AWS Price List
response (us-east-1, publication 2026-09-22). Covers: Claude Sonnet 5 and Claude Opus
4.8 in AWS's newer `usagetype` scheme (`input_tokens_standard`, ...), regional and
global scope for both; Claude Haiku 4.5 in the older scheme
(`InputTokenCount`, ...), regional and global; a `Batch` dimension (`InputTokenCount_Batch`)
and a `Reserved_*` dimension, both ignored per §6.3; a Cohere product (added from the raw
capture, see the extraction below) whose `usagetype` classifies as a standard input-token
dimension but whose `servicename` never appears in the registry and has no recognizable
vendor prefix -- the non-vendor-product `unmapped` case (§6.3's accept criterion
"non-vendor products (Cohere, ...) -> unmapped"). The fixture also carries a real "Claude
Sonnet 4" product (extracted from a raw AWS capture, older `usagetype` scheme, regional +
global) that LiteLLM's fixture never lists -- the §5.2 "official-source models" case:
`parse()` alone can only report it as a `candidates` entry (`test_claude_sonnet_4...`
below); `build.py`'s promotion pass is what actually adds it to the registry and turns it
into a row (covered end-to-end in `test_build.py`). The newer-wins-plus-warning behavior
(§6.3) is exercised by a small synthetic payload built inline
(`test_conflicting_schemes_newer_wins_with_warning`), against a synthetic one-model
registry, independent of the shared fixture/registry above.

The registry passed to `parse()` is built the same way `build.py` builds it for a real run:
`sources.litellm.parse` on `fixtures/litellm_subset.json` (§5.2) -- so a source adapter test
never invents its own registry-building logic.
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from common.model_pricing.config import ModelPricingConfig
from common.model_pricing.decimals import per_million_to_per_token
from common.model_pricing.normalize import ModelEntry, Registry
from common.model_pricing.sources import aws_price_list, litellm

LITELLM_FIXTURE = Path(__file__).parent / "fixtures" / "litellm_subset.json"
AWS_FIXTURE = Path(__file__).parent / "fixtures" / "aws_us_east_1_subset.json"

REGION = "us-east-1"


def _per_m(usd_per_million: str) -> Decimal:
    """A design-table dollars-per-1M figure -> the USD-per-token `Decimal` a row's
    `rates` dict actually holds, via the same conversion the adapter uses."""
    return per_million_to_per_token(Decimal(usd_per_million))


@pytest.fixture(scope="module")
def litellm_payload() -> dict:
    return json.loads(LITELLM_FIXTURE.read_text())


@pytest.fixture(scope="module")
def aws_payload() -> dict:
    return json.loads(AWS_FIXTURE.read_text())


@pytest.fixture(scope="module")
def registry(litellm_payload) -> Registry:
    cfg = ModelPricingConfig.model_validate(
        {
            "channels": [
                "aws_bedrock:us-east-1:regional",
                "aws_bedrock:us-east-1:global",
            ],
            "geo": {"us-east-1": "us"},
        }
    )
    reg, _rows, _unmapped = litellm.parse(litellm_payload, cfg)
    return reg


@pytest.fixture
def parsed(aws_payload, registry):
    return aws_price_list.parse(aws_payload, REGION, registry)


def _row(rows, channel, model_id):
    matches = [r for r in rows if r.channel == channel and r.model_id == model_id]
    assert len(matches) == 1, f"expected exactly one row for {channel}/{model_id}, got {matches}"
    return matches[0]


# ------------------------------------------------------------------ §3.2 worked example rows


def test_sonnet_5_regional_rates(parsed):
    rows, unmapped, candidates, warnings = parsed
    row = _row(rows, "aws_bedrock:us-east-1:regional", "claude-sonnet-5")
    assert row.source == "aws_price_list"
    assert row.source_key == "Claude Sonnet 5 (Amazon Bedrock Edition)"
    assert row.rates["input"] == _per_m("2.20")
    assert row.rates["output"] == _per_m("11.00")
    assert row.rates["cache_read"] == _per_m("0.22")
    assert row.rates["cache_write_5m"] == _per_m("2.75")
    assert row.rates["cache_write_1h"] == _per_m("4.40")


def test_sonnet_5_global_rates(parsed):
    rows, unmapped, candidates, warnings = parsed
    row = _row(rows, "aws_bedrock:us-east-1:global", "claude-sonnet-5")
    assert row.rates["input"] == _per_m("2.00")
    assert row.rates["output"] == _per_m("10.00")
    assert row.rates["cache_read"] == _per_m("0.20")
    assert row.rates["cache_write_5m"] == _per_m("2.50")
    assert row.rates["cache_write_1h"] == _per_m("4.00")


def test_opus_4_8_regional_rates(parsed):
    rows, unmapped, candidates, warnings = parsed
    row = _row(rows, "aws_bedrock:us-east-1:regional", "claude-opus-4-8")
    assert row.rates["input"] == _per_m("5.50")
    assert row.rates["output"] == _per_m("27.50")
    assert row.rates["cache_read"] == _per_m("0.55")
    assert row.rates["cache_write_5m"] == _per_m("6.875")
    assert row.rates["cache_write_1h"] == _per_m("11.00")


def test_haiku_4_5_regional_rates_older_scheme(parsed):
    """Haiku 4.5 is published only in AWS's older `usagetype` scheme
    (`InputTokenCount`, ...) -- the same merge path as the newer scheme must produce
    identical rows either way (§6.3)."""
    rows, unmapped, candidates, warnings = parsed
    row = _row(rows, "aws_bedrock:us-east-1:regional", "claude-haiku-4-5")
    assert row.rates["input"] == _per_m("1.10")
    assert row.rates["output"] == _per_m("5.50")
    assert row.rates["cache_read"] == _per_m("0.11")
    assert row.rates["cache_write_5m"] == _per_m("1.375")
    assert row.rates["cache_write_1h"] == _per_m("2.20")


# ------------------------------------------------------------------ ignored dimensions


def test_batch_dimension_does_not_affect_regional_input_rate(parsed):
    """`InputTokenCount_Batch` (0.55/M) must never leak into the regular input rate
    (1.10/M) -- if `_classify` mis-ignored the `_Batch` suffix, this rate would come out
    wrong or the SKU would land in `unmapped`."""
    rows, unmapped, candidates, warnings = parsed
    row = _row(rows, "aws_bedrock:us-east-1:regional", "claude-haiku-4-5")
    assert row.rates["input"] == _per_m("1.10")
    assert not any("Batch" in u.source_key for u in unmapped)


def test_reserved_dimension_is_ignored_not_unmapped(parsed):
    """`Reserved_3Month_OutputTPM_Global` is a capacity-reservation dimension, not a
    per-token price (§6.3's ignored list) -- it must be silently skipped, never listed
    in `unmapped` (that list is for records this adapter *should* have priced but could
    not map to a model)."""
    rows, unmapped, candidates, warnings = parsed
    assert not any("Reserved" in w for w in warnings)
    # No row's rates dict should carry a $0.27/M value (the Reserved dimension's price).
    all_rates = [v for row in rows for v in row.rates.values() if v is not None]
    assert _per_m("0.27") not in all_rates


# ------------------------------------------------------------------ non-vendor product -> candidate (§5.2)


def test_non_vendor_product_is_a_candidate_not_yet_unmapped(parsed):
    """Cohere is never a `litellm` vendor-channel entry (anthropic/openai/gemini only,
    §5.2), so it is never in the registry -- a Cohere product with an otherwise
    classifiable `usagetype` (a plain input-token dimension, not Batch/Reserved) does
    not normalize, but `parse()` alone cannot yet say whether that is because it is a
    non-vendor product or an official-source model (§5.2): it comes back as an
    `UnmatchedCandidate`, never silently dropped and never turned into a priced row.
    `build.py`'s promotion pass is what actually decides Cohere has no recognizable
    vendor prefix and puts it in `unmapped` (`test_build.py`)."""
    rows, unmapped, candidates, warnings = parsed
    cohere_key = "Cohere Generate Model - Command (Amazon Bedrock Edition)"
    matching = [c for c in candidates if c.source_key == cohere_key]
    assert len(matching) == 1
    assert matching[0].source == "aws_price_list"
    assert matching[0].model_string == "cohere-generate-model---command"
    assert not any(u.source_key == cohere_key for u in unmapped)
    assert not any(r.source_key == cohere_key for r in rows)


def test_claude_sonnet_4_is_a_candidate_on_both_scopes(parsed):
    """§5.2 "official-source models": Claude Sonnet 4 is real AWS Bedrock data
    (extracted from a raw price-list capture) that LiteLLM's fixture never lists --
    `parse()` alone cannot add it to the registry, so both its regional and global
    rows come back as `UnmatchedCandidate`s, fully priced (input/output/cache_read/
    cache_write_5m; AWS's older scheme publishes no 1h cache-write meter here)."""
    rows, unmapped, candidates, warnings = parsed
    by_channel = {c.channel: c for c in candidates if c.model_string == "claude-sonnet-4"}
    assert set(by_channel) == {"aws_bedrock:us-east-1:regional", "aws_bedrock:us-east-1:global"}
    for c in by_channel.values():
        assert c.source == "aws_price_list"
        assert c.rates["input"] == _per_m("3.00")
        assert c.rates["output"] == _per_m("15.00")
        assert c.rates["cache_read"] == _per_m("0.30")
        assert c.rates["cache_write_5m"] == _per_m("3.75")
        assert c.rates.get("cache_write_1h") is None
    assert not any(r.model_id == "claude-sonnet-4" for r in rows)


# ------------------------------------------------------------------ conflicting schemes -> newer wins + warning


def _synthetic_conflict_payload() -> dict:
    """A model priced under *both* usagetype schemes with different rates for the same
    `(token_type, scope)` -- not something any real Bedrock model does (§6.3: "no model
    in the wild uses both at once"), but the merge code must still resolve it
    deterministically: newer scheme wins, and a warning is recorded. Older scheme says
    3.00/M input; newer says 3.30/M input; only the newer figure may end up in the row.
    """
    return {
        "products": {
            "OLDERSKU00000001": {
                "sku": "OLDERSKU00000001",
                "attributes": {
                    "location": "US East (N. Virginia)",
                    "locationType": "AWS Region",
                    "operation": "",
                    "regionCode": "us-east-1",
                    "servicename": "Claude Test Model (Amazon Bedrock Edition)",
                    "usagetype": "USE1-MP:USE1_InputTokenCount-Units",
                },
            },
            "NEWERSKU000000002": {
                "sku": "NEWERSKU000000002",
                "attributes": {
                    "location": "US East (N. Virginia)",
                    "locationType": "AWS Region",
                    "operation": "",
                    "regionCode": "us-east-1",
                    "servicename": "Claude Test Model (Amazon Bedrock Edition)",
                    "usagetype": "USE1-MP:USE1_input_tokens_standard-Units",
                },
            },
        },
        "terms": {
            "OnDemand": {
                "OLDERSKU00000001": {
                    "OLDERSKU00000001.TERM": {
                        "priceDimensions": {
                            "OLDERSKU00000001.TERM.DIM": {
                                "pricePerUnit": {"USD": "3.0000000000"},
                                "unit": "1M tokens",
                            }
                        }
                    }
                },
                "NEWERSKU000000002": {
                    "NEWERSKU000000002.TERM": {
                        "priceDimensions": {
                            "NEWERSKU000000002.TERM.DIM": {
                                "pricePerUnit": {"USD": "3.3000000000"},
                                "unit": "1M tokens",
                            }
                        }
                    }
                },
            }
        },
    }


def _synthetic_registry() -> Registry:
    return Registry(
        models={
            "claude-test-model": ModelEntry(
                vendor="anthropic", names_seen=("claude-test-model",)
            )
        }
    )


def test_conflicting_schemes_newer_wins_with_warning():
    payload = _synthetic_conflict_payload()
    reg = _synthetic_registry()
    rows, unmapped, candidates, warnings = aws_price_list.parse(payload, REGION, reg)

    row = _row(rows, "aws_bedrock:us-east-1:regional", "claude-test-model")
    assert row.rates["input"] == _per_m("3.30")

    assert len(warnings) == 1
    assert "claude-test-model" in warnings[0]
    assert "newer" in warnings[0].lower()
    assert unmapped == []
