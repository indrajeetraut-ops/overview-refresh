"""`sources.azure_retail` against captured fixture pages (design §5.3, §6.2-§6.4, §10.1).

Fixture: `fixtures/azure_eastus2_pages.json`, a small (~20 KB) two-page extraction from
a real Azure Retail Prices `eastus2` "Foundry Models" response (`gpt-5.6-sol` and
`gpt-5.5` meters only) plus two synthetic edge-case items appended to page 2 (a
non-GPT product, and a well-formed meter published in a `1K` unit this adapter does
not read) -- see `build_azure_fixture.py`'s extraction list for exactly which meters.
Covers: `ShortCo`/`LongCo`, `Std`/`PP`/`Batch`, `Gl`/`DZ`, mixed-case tokens
(`cd inp`/`Cd Inp`), paging via `NextPageLink`.

The model registry comes from `sources.litellm.parse` on `fixtures/litellm_subset.json`
(that fixture already carries `gpt-5.6-sol` and `gpt-5.5` as vendor-channel entries),
exactly as `build.py`'s real pipeline runs LiteLLM first to build the registry every
other source normalizes against (§6.1).
"""
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from common.model_pricing.config import ModelPricingConfig
from common.model_pricing.normalize import Registry
from common.model_pricing.sources import azure_retail, litellm

FIXTURES = Path(__file__).parent / "fixtures"
AZURE_FIXTURE = FIXTURES / "azure_eastus2_pages.json"
LITELLM_FIXTURE = FIXTURES / "litellm_subset.json"

REGION = "eastus2"


@pytest.fixture(scope="module")
def registry() -> Registry:
    payload = json.loads(LITELLM_FIXTURE.read_text())
    registry, _rows, _unmapped = litellm.parse(payload, ModelPricingConfig())
    assert "gpt-5-6-sol" in registry.models
    assert "gpt-5-5" in registry.models
    return registry


@pytest.fixture(scope="module")
def pages() -> list[dict]:
    return json.loads(AZURE_FIXTURE.read_text())


@pytest.fixture
def items(pages) -> list[dict]:
    """All items across both fixture pages, as `parse()` receives them once `fetch()`
    has followed paging (mirrors what `build.py` passes -- one flat list)."""
    out = []
    for page in pages:
        out.extend(page["Items"])
    return out


def _row(rows, channel, model_id):
    matches = [r for r in rows if r.channel == channel and r.model_id == model_id]
    assert len(matches) == 1, f"expected exactly one row for {channel}/{model_id}, got {matches}"
    return matches[0]


def test_data_zone_gpt_5_6_sol_rates(items, registry):
    rows, unmapped, candidates, warnings = azure_retail.parse(items, REGION, registry)
    row = _row(rows, "azure:eastus2:data_zone", "gpt-5-6-sol")
    assert row.rates["input"] == Decimal("4.40") / Decimal(1_000_000)
    assert row.rates["output"] == Decimal("22.00") / Decimal(1_000_000)
    assert row.rates["cache_write_5m"] == Decimal("5.50") / Decimal(1_000_000)


def test_global_gpt_5_6_sol_rates(items, registry):
    rows, unmapped, candidates, warnings = azure_retail.parse(items, REGION, registry)
    row = _row(rows, "azure:eastus2:global", "gpt-5-6-sol")
    assert row.rates["input"] == Decimal("4.00") / Decimal(1_000_000)
    assert row.rates["output"] == Decimal("20.00") / Decimal(1_000_000)


def test_gpt_5_5_data_zone_and_global_rates(items, registry):
    """gpt-5.5's kept meters carry no `Std` token at all (§6.3: "Std or no tier
    token") and use lowercase `cd inp` -- both must still parse."""
    rows, unmapped, candidates, warnings = azure_retail.parse(items, REGION, registry)

    dz = _row(rows, "azure:eastus2:data_zone", "gpt-5-5")
    assert dz.rates["input"] == Decimal("5.50") / Decimal(1_000_000)
    assert dz.rates["output"] == Decimal("33.00") / Decimal(1_000_000)
    assert dz.rates["cache_read"] == Decimal("0.55") / Decimal(1_000_000)

    gl = _row(rows, "azure:eastus2:global", "gpt-5-5")
    assert gl.rates["input"] == Decimal("5.00") / Decimal(1_000_000)
    assert gl.rates["output"] == Decimal("30.00") / Decimal(1_000_000)
    assert gl.rates["cache_read"] == Decimal("0.50") / Decimal(1_000_000)


def test_longco_batch_pp_meters_are_ignored_not_unmapped(items, registry):
    rows, unmapped, candidates, warnings = azure_retail.parse(items, REGION, registry)

    # None of the ignored meters' distinctive prices leak into a kept row.
    dz_5_6 = _row(rows, "azure:eastus2:data_zone", "gpt-5-6-sol")
    assert dz_5_6.rates["input"] != Decimal("8.8") / Decimal(1_000_000)  # the PP price

    dz_5_5 = _row(rows, "azure:eastus2:data_zone", "gpt-5-5")
    assert dz_5_5.rates["output"] != Decimal("82.5") / Decimal(1_000_000)  # the PP price

    unmapped_keys = " ".join(u.source_key for u in unmapped)
    assert "LongCo" not in unmapped_keys
    assert "PP" not in unmapped_keys
    assert "Batch" not in unmapped_keys


def test_mixed_case_tokens_parsed(items, registry):
    """gpt-5.5's meters spell the price type in lowercase (`inp`, `opt`, `cd inp`)
    while gpt-5.6-sol's spell it capitalized (`Inp`, `Opt`, `Cd Inp`, `Cd Wr`); both
    families must resolve to the same token types."""
    rows, unmapped, candidates, warnings = azure_retail.parse(items, REGION, registry)
    gl_5_5 = _row(rows, "azure:eastus2:global", "gpt-5-5")
    assert gl_5_5.rates["cache_read"] is not None
    dz_5_6 = _row(rows, "azure:eastus2:data_zone", "gpt-5-6-sol")
    assert dz_5_6.rates["cache_write_5m"] is not None


def test_non_gpt_product_is_unmapped(items, registry):
    rows, unmapped, candidates, warnings = azure_retail.parse(items, REGION, registry)
    reasons = {u.reason for u in unmapped if "DALL-E" in u.source_key}
    assert reasons == {"non-GPT product"}


def test_unsupported_unit_is_unmapped(items, registry):
    rows, unmapped, candidates, warnings = azure_retail.parse(items, REGION, registry)
    matches = [u for u in unmapped if "1K Tokens" in u.source_key]
    assert len(matches) == 1
    assert "unit" in matches[0].reason
    assert "1K" in matches[0].reason


def test_wrong_region_items_are_skipped(registry):
    items = [
        {
            "armRegionName": "westeurope",
            "productName": "Azure OpenAI GPT5",
            "meterName": "5.6 sol ShortCo Inp Std DZ 1M Tokens",
            "unitOfMeasure": "1M",
            "retailPrice": 4.4,
        }
    ]
    rows, unmapped, candidates, warnings = azure_retail.parse(items, REGION, registry)
    assert rows == []
    assert unmapped == []


def test_only_input_no_output_is_unmapped_missing_rate(registry):
    items = [
        {
            "armRegionName": REGION,
            "productName": "Azure OpenAI GPT5",
            "meterName": "5.6 sol ShortCo Inp Std DZ 1M Tokens",
            "unitOfMeasure": "1M",
            "retailPrice": 4.4,
        }
    ]
    rows, unmapped, candidates, warnings = azure_retail.parse(items, REGION, registry)
    assert rows == []
    assert any("missing input or output" in u.reason for u in unmapped)


def test_fetch_follows_next_page_link(pages):
    """`fetch()` against an injected `httpx.MockTransport`: no real network, and both
    pages must be concatenated (paging followed, §6.2)."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "$skip" not in str(request.url):
            return httpx.Response(200, json=pages[0])
        return httpx.Response(200, json=pages[1])

    transport = httpx.MockTransport(handler)
    items, provenance = azure_retail.fetch(REGION, _transport=transport)

    assert len(calls) == 2
    expected_count = len(pages[0]["Items"]) + len(pages[1]["Items"])
    assert len(items) == expected_count
    assert provenance["region"] == REGION
    assert "fetched_at" in provenance


def test_fetch_first_request_carries_the_filter(pages):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if not captured:
            captured["url"] = str(request.url)
        return httpx.Response(200, json={"Items": [], "NextPageLink": None})

    transport = httpx.MockTransport(handler)
    azure_retail.fetch(REGION, _transport=transport)

    assert "serviceName" in captured["url"]
    assert "Foundry" in captured["url"]
    assert "eastus2" in captured["url"]


def test_fetch_filter_excludes_non_openai_products(pages):
    """§6.2: the `$filter` gains `contains(productName, 'Azure OpenAI')` so non-OpenAI
    Foundry products (Phi, DeepSeek, Grok, ...) are never fetched at all."""
    import urllib.parse

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if not captured:
            captured["url"] = str(request.url)
        return httpx.Response(200, json={"Items": [], "NextPageLink": None})

    transport = httpx.MockTransport(handler)
    azure_retail.fetch(REGION, _transport=transport)

    decoded_url = urllib.parse.unquote_plus(captured["url"])
    assert "contains(productName, 'Azure OpenAI')" in decoded_url
