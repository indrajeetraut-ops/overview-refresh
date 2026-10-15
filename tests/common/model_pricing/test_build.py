"""`build.build` / `build.write_catalog` (design §6, §10.1).

Fixture-driven, no network: fetchers are fixture-backed functions injected into
`build()`, exactly as `update` (T9) will inject the real ones. The shared fixtures
(`litellm_subset.json`, `aws_us_east_1_subset.json`) are the same ones
`test_source_litellm.py` / `test_source_aws.py` already exercise at the adapter level
-- this file exercises the pipeline *around* those adapters: acquire, registry,
merge precedence, derive, validate, stamp, version, and the write path.

`GCP_BILLING_API_KEY` is scrubbed for every test here (autouse fixture) so
`gcp_billing.fetch`'s default (real) fetcher always takes its "skipped, no key"
branch -- no network, no `SourceError`, regardless of the host environment.
"""
import json
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from common.model_pricing.build import BuildResult, build, load_previous, write_catalog
from common.model_pricing.catalog import RateRow, load_catalog
from common.model_pricing.config import ModelPricingConfig
from common.model_pricing.decimals import per_million_to_per_token as pm
from common.model_pricing.errors import SourceError
from common.model_pricing.reports import render_refresh

FIXTURES = Path(__file__).parent / "fixtures"
LITELLM_PAYLOAD = json.loads((FIXTURES / "litellm_subset.json").read_text())
AWS_PAYLOAD = json.loads((FIXTURES / "aws_us_east_1_subset.json").read_text())
AZURE_FIXTURE = FIXTURES / "azure_eastus2_pages.json"
_HAS_AZURE_FIXTURE = AZURE_FIXTURE.exists()

BUILT_ON = date(2026, 9, 25)


@pytest.fixture(autouse=True)
def _no_gcp_key(monkeypatch):
    """`gcp_billing.fetch`'s default (real, unmocked) fetcher is always in play here
    (no test overrides it) -- it must stay on its no-network "skipped" branch."""
    monkeypatch.delenv("GCP_BILLING_API_KEY", raising=False)


def _litellm_fetcher(payload: dict | None = None):
    payload = LITELLM_PAYLOAD if payload is None else payload

    def fetch(commit: str | None = None):
        return payload, {"commit_sha": "d3462c65", "commit_date": "2026-09-24T18:08:09Z"}

    return fetch


def _aws_fetcher(payload: dict | None = None):
    payload = AWS_PAYLOAD if payload is None else payload

    def fetch(region: str):
        return payload, {"publication_date": "2026-09-22T16:44:18Z", "version": "1"}

    return fetch


def _aws_payload_without(payload: dict, servicename_substring: str) -> dict:
    """A deep copy of `payload` with every product (and its `OnDemand` pricing terms)
    whose `servicename` contains `servicename_substring` removed -- used to force the
    LiteLLM-fallback path for exactly one model while leaving the rest of the AWS
    fixture intact (§6.4's "else the LiteLLM fallback")."""
    payload = deepcopy(payload)
    products = payload.get("products", {})
    kept = {
        sku: prod
        for sku, prod in products.items()
        if servicename_substring not in prod.get("attributes", {}).get("servicename", "")
    }
    removed_skus = set(products) - set(kept)
    payload["products"] = kept
    terms = payload.get("terms", {}).get("OnDemand", {})
    payload["terms"]["OnDemand"] = {sku: t for sku, t in terms.items() if sku not in removed_skus}
    return payload


def _row(rows: list[RateRow], channel: str, model_id: str) -> RateRow:
    matches = [r for r in rows if r.channel == channel and r.model_id == model_id]
    assert len(matches) == 1, f"expected exactly one row for {channel}/{model_id}, got {matches}"
    return matches[0]


@pytest.fixture
def cfg() -> ModelPricingConfig:
    return ModelPricingConfig.model_validate(
        {
            "channels": ["aws_bedrock:us-east-1:regional", "aws_bedrock:us-east-1:global"],
            "geo": {"us-east-1": "us"},
        }
    )


@pytest.fixture
def base_fetchers() -> dict:
    return {"litellm": _litellm_fetcher(), "aws_price_list": _aws_fetcher()}


@pytest.fixture
def built(cfg, base_fetchers) -> BuildResult:
    return build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)


# ------------------------------------------------------------------ merge precedence (§4.1, §6.4)


def test_aws_wins_over_litellm_for_bedrock_rows_with_litellm_rates_kept(built):
    """§3.2's own worked example: AWS Price List wins Bedrock rows over LiteLLM, and
    since LiteLLM has a row for the same key, `litellm_rates` is recorded for the
    refresh report's diff (§3.1)."""
    row = _row(built.version.rates, "aws_bedrock:us-east-1:regional", "claude-sonnet-5")
    assert row.source == "aws_price_list"
    assert row.source_key == "Claude Sonnet 5 (Amazon Bedrock Edition)"
    assert row.input == pm(Decimal("2.20"))
    assert row.output == pm(Decimal("11.00"))
    assert row.cache_read == pm(Decimal("0.22"))
    assert row.cache_write_5m == pm(Decimal("2.75"))
    assert row.cache_write_1h == pm(Decimal("4.40"))
    assert row.litellm_rates is not None
    assert row.litellm_rates["input"] == "0.0000022"


def test_litellm_fallback_when_aws_lacks_a_model(cfg):
    """AWS's fixture, with Claude Opus 4.8's products removed -- Opus must fall back
    to LiteLLM's own Bedrock rows on both scopes, while Sonnet 5 (still in the
    trimmed AWS payload) keeps winning from AWS (§6.4: "else the LiteLLM fallback")."""
    trimmed = _aws_payload_without(AWS_PAYLOAD, "Claude Opus 4.8")
    result = build(
        cfg,
        fetchers={"litellm": _litellm_fetcher(), "aws_price_list": _aws_fetcher(trimmed)},
        built_on=BUILT_ON,
    )

    for channel in ("aws_bedrock:us-east-1:regional", "aws_bedrock:us-east-1:global"):
        row = _row(result.version.rates, channel, "claude-opus-4-8")
        assert row.source == "litellm"
        # litellm_rates is only populated when a *non*-litellm source wins (§3.1).
        assert row.litellm_rates is None

    sonnet_regional = _row(result.version.rates, "aws_bedrock:us-east-1:regional", "claude-sonnet-5")
    assert sonnet_regional.source == "aws_price_list"


# ------------------------------------------------------------------ source_diff false positive (§6.6, §7)


def _minimal_aws_payload(rates_per_million: dict[str, str], servicename: str, region: str) -> dict:
    """A hand-built AWS Bedrock price-list payload with exactly the `usagetype`
    dimensions in `rates_per_million` (newer naming scheme) -- unlike the shared AWS
    fixture (every model fully priced), this lets a test omit cache-write dimensions
    entirely to force §6.5's derive step."""
    usagetype_for = {
        "input": "input_tokens_standard",
        "output": "output_tokens_standard",
        "cache_read": "cache_read_tokens_standard",
        "cache_write_5m": "cache_write_tokens_standard",
        "cache_write_1h": "cache_write_tokens_1h_standard",
    }
    products: dict[str, Any] = {}
    terms: dict[str, Any] = {}
    for i, (tt, price) in enumerate(rates_per_million.items()):
        sku = f"SKU{i}"
        usagetype = f"USE1-MP:USE1_{usagetype_for[tt]}-Units"
        products[sku] = {
            "attributes": {"regionCode": region, "servicename": servicename, "usagetype": usagetype}
        }
        terms[sku] = {
            f"{sku}.TERM1": {"priceDimensions": {f"{sku}.TERM1.DIM1": {"pricePerUnit": {"USD": price}}}}
        }
    return {
        "products": products,
        "terms": {"OnDemand": terms},
        "publicationDate": "2026-09-22T16:44:18Z",
        "version": "1",
    }


@pytest.fixture
def source_diff_cfg() -> ModelPricingConfig:
    return ModelPricingConfig.model_validate(
        {"channels": ["aws_bedrock:us-east-1:regional"], "geo": {"us-east-1": "us"}}
    )


def _source_diff_litellm_payload() -> dict:
    """A vendor-channel entry (feeds the registry) plus a *separate* Bedrock-regional
    entry (`litellm_provider: "bedrock"`, so it is NOT also a registry candidate,
    §5.2) -- LiteLLM's own row for `aws_bedrock:us-east-1:regional`/`claude-test-1`,
    with no cache-write fields at all (mirrors the live Azure `gpt-5-5` case: LiteLLM
    simply never published a cache-write rate for this channel key)."""
    return {
        "claude-test-1": {
            "input_cost_per_token": 2e-06,
            "output_cost_per_token": 1e-05,
            "litellm_provider": "anthropic",
            "mode": "chat",
        },
        "us.anthropic.claude-test-1": {
            "input_cost_per_token": 2e-06,
            "output_cost_per_token": 1e-05,
            "litellm_provider": "bedrock",
            "mode": "chat",
        },
    }


def test_source_diff_not_raised_when_only_derived_rate_differs(source_diff_cfg):
    """AWS wins `aws_bedrock:us-east-1:regional`/`claude-test-1` with the *same*
    input/output LiteLLM has, but publishes no cache-write dimension at all -- §6.5
    derive then fills `cache_write_5m`/`cache_write_1h` from `input`. Before the fix,
    comparing those *derived* rates against LiteLLM's (which also has no cache-write
    rate for this key, i.e. `None`) wrongly registered as a diff; the fix compares only
    token types LiteLLM actually published (here: just input/output, which agree) and
    uses AWS's rates from *before* derive (§6.6's "first source != litellm")."""
    aws_payload = _minimal_aws_payload(
        {"input": "2.00", "output": "10.00"},
        servicename="Claude Test 1 (Amazon Bedrock Edition)",
        region="us-east-1",
    )
    result = build(
        source_diff_cfg,
        fetchers={
            "litellm": lambda commit=None: (
                _source_diff_litellm_payload(),
                {"commit_sha": "d3462c65", "commit_date": "2026-09-24T18:08:09Z"},
            ),
            "aws_price_list": lambda region: (aws_payload, {"publication_date": "2026-09-22T16:44:18Z"}),
        },
        built_on=BUILT_ON,
    )
    row = _row(result.version.rates, "aws_bedrock:us-east-1:regional", "claude-test-1")
    assert row.source == "aws_price_list"
    assert row.derived == ["cache_write_billed_as_input"]
    assert row.cache_write_5m == row.input  # derived, not published by AWS
    assert not any(
        d.channel == "aws_bedrock:us-east-1:regional" and d.model_id == "claude-test-1"
        for d in result.source_diff
    ), result.source_diff


def test_source_diff_raised_for_genuine_rate_difference(source_diff_cfg):
    """Same setup as above, except AWS's `input` genuinely differs from LiteLLM's
    (both actually published) -- the fix must not suppress this: a real difference in
    a token type LiteLLM did publish still raises `source_diff`, and `winner_rates`
    only ever shows AWS's *raw* (pre-derive) rates, never the derived cache-write
    fill-in."""
    aws_payload = _minimal_aws_payload(
        {"input": "2.20", "output": "10.00"},  # input: +10% vs LiteLLM's 2.00
        servicename="Claude Test 1 (Amazon Bedrock Edition)",
        region="us-east-1",
    )
    result = build(
        source_diff_cfg,
        fetchers={
            "litellm": lambda commit=None: (
                _source_diff_litellm_payload(),
                {"commit_sha": "d3462c65", "commit_date": "2026-09-24T18:08:09Z"},
            ),
            "aws_price_list": lambda region: (aws_payload, {"publication_date": "2026-09-22T16:44:18Z"}),
        },
        built_on=BUILT_ON,
    )
    diffs = [
        d
        for d in result.source_diff
        if d.channel == "aws_bedrock:us-east-1:regional" and d.model_id == "claude-test-1"
    ]
    assert len(diffs) == 1
    diff = diffs[0]
    assert diff.winner_rates == {"input": "0.0000022", "output": "0.00001"}  # no derived cache-write
    assert diff.litellm_rates == {"input": "0.000002", "output": "0.00001"}


# ------------------------------------------------------------------ vendor channels (§4.1)


def test_vendor_rows_from_litellm(built):
    for model_id in ("claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5"):
        row = _row(built.version.rates, "anthropic", model_id)
        assert row.source == "litellm"
        assert row.litellm_rates is None  # litellm never diffs against itself


# ------------------------------------------------------------------ derived rates (§6.5)


def test_derived_cache_write_for_model_without_cache_write_fields(built):
    """`gpt-5.5` publishes no cache-write fields at all (`litellm_subset.json`) --
    both cache-write rates must be derived as billed-at-input, flagged `derived`."""
    row = _row(built.version.rates, "openai", "gpt-5-5")
    assert row.derived == ["cache_write_billed_as_input"]
    assert row.cache_write_5m == row.input
    assert row.cache_write_1h == row.input


def test_only_cache_write_1h_absent_is_billed_as_5m(built):
    """`gpt-5.6-sol` has `cache_write_5m` but no `cache_write_1h` -- §6.5: the 1-hour write
    takes the 5-minute rate, flagged `cache_write_1h_billed_as_5m`; the published 5m rate
    and every other rate are untouched."""
    row = _row(built.version.rates, "openai", "gpt-5-6-sol")
    assert row.cache_write_5m is not None
    assert row.cache_write_1h == row.cache_write_5m
    assert row.derived == ["cache_write_1h_billed_as_5m"]


def test_only_cache_write_5m_absent_stays_none():
    """The reverse case (only `cache_write_1h` published) is NOT derived: a 5m write is
    never silently priced at the dearer 1h rate, and nothing is flagged."""
    from decimal import Decimal as D
    from common.model_pricing.build import derive_cache_write_rates

    rates = {"input": D("3"), "cache_write_5m": None, "cache_write_1h": D("6")}
    derived = derive_cache_write_rates(rates)
    assert rates["cache_write_5m"] is None and rates["cache_write_1h"] == D("6")
    assert derived == []


def test_derive_cache_write_rates_rules():
    from decimal import Decimal as D
    from common.model_pricing.build import derive_cache_write_rates

    both_missing = {"input": D("3"), "cache_write_5m": None, "cache_write_1h": None}
    assert derive_cache_write_rates(both_missing) == ["cache_write_billed_as_input"]
    assert both_missing["cache_write_5m"] == both_missing["cache_write_1h"] == D("3")

    only_5m = {"input": D("3"), "cache_write_5m": D("3.75"), "cache_write_1h": None}
    assert derive_cache_write_rates(only_5m) == ["cache_write_1h_billed_as_5m"]
    assert only_5m["cache_write_1h"] == D("3.75")

    both = {"input": D("3"), "cache_write_5m": D("3.75"), "cache_write_1h": D("6")}
    assert derive_cache_write_rates(both) == []
    assert both["cache_write_1h"] == D("6")


# ------------------------------------------------------------------ hash / determinism / no-op (§3.3-§3.4, §6.1)


def test_hash_determinism(cfg, base_fetchers):
    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    r2 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    assert r1.version.version == r2.version.version
    assert not r1.is_noop
    assert not r2.is_noop  # neither build was given a `previous`


def test_second_build_with_previous_is_noop(cfg, base_fetchers):
    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    r2 = build(cfg, fetchers=base_fetchers, previous=r1.version, built_on=date(2026, 9, 26))
    assert r2.is_noop
    # same rates -> same hash8, even though `built_on` (and so the version string's
    # date prefix) differs between the two builds.
    assert r2.version.version.rsplit("-", 1)[-1] == r1.version.version.rsplit("-", 1)[-1]


def test_one_changed_rate_new_version_and_change_warning(cfg, base_fetchers):
    modified = deepcopy(LITELLM_PAYLOAD)
    modified["claude-sonnet-5"]["input_cost_per_token"] *= 3  # +200% > 50% threshold

    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    r2 = build(
        cfg,
        fetchers={"litellm": _litellm_fetcher(modified), "aws_price_list": _aws_fetcher()},
        previous=r1.version,
        built_on=date(2026, 9, 26),
    )

    assert not r2.is_noop
    assert r2.version.version != r1.version.version
    assert any(
        "claude-sonnet-5" in w and "input" in w and "50%" in w for w in r2.warnings
    ), r2.warnings


def test_first_build_skips_change_check(cfg, base_fetchers):
    """No `previous` -> the >50% change check (§6.6) never runs, however large a
    model's rate might look in isolation."""
    result = build(cfg, fetchers=base_fetchers, previous=None, built_on=BUILT_ON)
    assert not any("changed" in w for w in result.warnings)
    # §6.6/§7: `added`/`changed` are noise on the first build (everything would
    # otherwise show up as "added") -- both stay empty, `is_first_build` says why.
    assert result.added == []
    assert result.changed == []
    assert result.is_first_build


# ------------------------------------------------------------------ added / changed (§6.6, §7)


def test_ten_percent_change_and_new_model_no_warning(cfg, base_fetchers):
    """A 10% rate change stays under the >50% warning threshold but must still be
    itemized in `changed` (compared as `Decimal`, §6.6); a wholly new model must show
    up in `added`, not `changed` -- and both must be reflected in the refresh report
    (§7), which itemizes *all* changes, not only the ones crossing 50%."""
    modified = deepcopy(LITELLM_PAYLOAD)
    modified["claude-opus-4-8"]["input_cost_per_token"] = 5.5e-06  # +10%, well under 50%
    modified["claude-iota-1"] = {
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
        "litellm_provider": "anthropic",
        "mode": "chat",
    }

    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    r2 = build(
        cfg,
        fetchers={"litellm": _litellm_fetcher(modified), "aws_price_list": _aws_fetcher()},
        previous=r1.version,
        built_on=date(2026, 9, 26),
    )

    assert not r2.is_noop
    assert not r2.is_first_build
    assert not any(
        "claude-opus-4-8" in w and "50%" in w for w in r2.warnings
    ), r2.warnings

    changed_items = [
        c for c in r2.changed if c.channel == "anthropic" and c.model_id == "claude-opus-4-8"
    ]
    assert len(changed_items) == 1
    change = changed_items[0]
    assert change.token_type == "input"
    assert change.old == "0.000005"
    assert change.new == "0.0000055"
    assert change.pct is not None
    assert abs(change.pct - Decimal("10")) < Decimal("0.01")

    added_items = [a for a in r2.added if a.model_id == "claude-iota-1"]
    assert len(added_items) == 1
    assert added_items[0].channel == "anthropic"
    # the new model is `added`, never also `changed`; the changed model is never `added`.
    assert not any(a.model_id == "claude-opus-4-8" for a in r2.added)
    assert not any(c.model_id == "claude-iota-1" for c in r2.changed)

    # The Markdown summary itemizes *counts* only, never names (§7) -- one added, one
    # changed -- `render_refresh_json` (exercised in test_reports.py) is where the
    # named rows live.
    text = render_refresh(r2)
    assert "- added: 1" in text
    assert "- changed: 1" in text
    assert "claude-iota-1" not in text
    assert "claude-opus-4-8" not in text


# ------------------------------------------------------------------ source failure (§6.1, §9)


def test_source_fetch_failure_raises_sourceerror_and_nothing_written(cfg, tmp_path):
    def failing_aws(region: str):
        raise RuntimeError("network down")

    catalog_root = tmp_path / "catalog"
    with pytest.raises(SourceError, match="aws_price_list"):
        result = build(
            cfg,
            fetchers={"litellm": _litellm_fetcher(), "aws_price_list": failing_aws},
            built_on=BUILT_ON,
        )
        write_catalog(result, catalog_root)  # unreachable: build() already raised

    assert not catalog_root.exists()


# ------------------------------------------------------------------ write path (§6.1, §9)


def test_write_catalog_creates_four_files_and_load_catalog_reads_back(cfg, base_fetchers, tmp_path):
    result = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    catalog_root = tmp_path / "catalog"
    config_root = tmp_path / "config"

    write_catalog(result, catalog_root)

    version = result.version.version
    files = sorted(p.name for p in catalog_root.iterdir())
    assert files == sorted(
        [f"{version}.json", f"{version}.refresh.md", f"{version}.refresh.json", "index.json"]
    )

    # The refresh detail parses and carries the full lists the summary only counts.
    detail = json.loads((catalog_root / f"{version}.refresh.json").read_text())
    assert detail["version"] == version
    assert detail["is_noop"] is False

    index = json.loads((catalog_root / "index.json").read_text())
    assert index["versions"] == [version]

    catalog = load_catalog(root=catalog_root, config_root=config_root)
    assert catalog.version == version
    prices = catalog.for_org("acme")  # unconfigured org -> vendor channels
    match = prices.resolve("claude-sonnet-5")
    assert match.status == "matched"
    assert match.channel == "anthropic"


def test_write_catalog_is_idempotent_on_crash_recovered_version_file(cfg, base_fetchers, tmp_path):
    """A `<version>.json` already on disk with identical content (the "crashed after
    the version file but before the index" case, §6.1) must not be rejected -- only
    the still-missing files get written."""
    result = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    catalog_root = tmp_path / "catalog"
    catalog_root.mkdir()
    (catalog_root / f"{result.version.version}.json").write_text(result.version.model_dump_json())

    write_catalog(result, catalog_root)

    files = sorted(p.name for p in catalog_root.iterdir())
    assert files == sorted(
        [
            f"{result.version.version}.json",
            f"{result.version.version}.refresh.md",
            f"{result.version.version}.refresh.json",
            "index.json",
        ]
    )


def test_noop_result_is_not_written(cfg, base_fetchers, tmp_path):
    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    catalog_root = tmp_path / "catalog"
    write_catalog(r1, catalog_root)

    r2 = build(cfg, fetchers=base_fetchers, previous=r1.version, built_on=date(2026, 9, 26))
    assert r2.is_noop
    before = sorted(p.name for p in catalog_root.iterdir())
    write_catalog(r2, catalog_root)
    after = sorted(p.name for p in catalog_root.iterdir())
    assert before == after  # nothing new written for a no-op


def test_load_previous(cfg, base_fetchers, tmp_path):
    catalog_root = tmp_path / "catalog"
    assert load_previous(catalog_root) is None  # no catalog yet

    result = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    write_catalog(result, catalog_root)

    previous = load_previous(catalog_root)
    assert previous is not None
    assert previous.version == result.version.version


# ------------------------------------------------------------------ retirement (§3.5, §6.1 step 6, §6.6, §7)


def _gemini_removed_payload() -> dict:
    modified = deepcopy(LITELLM_PAYLOAD)
    del modified["gemini/gemini-2.5-flash"]
    return modified


def test_retirement_row_written_on_disappearance(cfg, base_fetchers):
    """v1 has `gemini-2.5-flash`; v2's LiteLLM payload drops it -- §3.5's "New
    retirement row" case: a row *is* still written for the key (unlike the old
    no-longer-published behavior, which wrote nothing), `source="retired"`,
    `retired_on = built_on - 1 day`, last known rates carried; `result.retired`
    records it, `is_new=True`."""
    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)

    r2 = build(
        cfg,
        fetchers={"litellm": _litellm_fetcher(_gemini_removed_payload()), "aws_price_list": _aws_fetcher()},
        previous=r1.version,
        built_on=date(2026, 9, 26),
    )

    entries = [r for r in r2.retired if r.model_id == "gemini-2-5-flash"]
    assert len(entries) == 1
    assert entries[0].channel == "gemini"
    assert entries[0].is_new
    assert entries[0].retired_on == date(2026, 9, 25)  # built_on - 1 day

    row = _row(r2.version.rates, "gemini", "gemini-2-5-flash")
    assert row.source == "retired"
    assert row.retired_on == date(2026, 9, 25)
    assert row.start_date == date(2026, 9, 26)
    assert row.input is not None  # last known rate carried, not dropped


def test_retirement_lookup_before_and_after_retired_on(cfg, base_fetchers, tmp_path):
    """§3.4's retired rule, exercised through a real catalog: the day before
    `retired_on` still prices at the last known rate (forward rule); the day after
    (and `retired_on` itself, per the `date > retired_on` rule) prices `None`."""
    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    r2 = build(
        cfg,
        fetchers={"litellm": _litellm_fetcher(_gemini_removed_payload()), "aws_price_list": _aws_fetcher()},
        previous=r1.version,
        built_on=date(2026, 9, 26),
    )
    catalog_root = tmp_path / "catalog"
    write_catalog(r1, catalog_root)
    write_catalog(r2, catalog_root)

    catalog = load_catalog(root=catalog_root, config_root=tmp_path / "config")
    prices = catalog.for_org("acme")
    match = prices.resolve("gemini-2.5-flash")
    assert match.status == "matched"

    retired_on = date(2026, 9, 25)
    assert prices.rate(match, "input", retired_on) is not None  # on retired_on itself: still priced
    assert prices.rate(match, "input", retired_on + timedelta(days=1)) is None  # after: retired


def test_retirement_carried_unchanged_and_v3_is_noop(cfg, base_fetchers):
    """§3.5: "Already-retired keys in `previous` still unpublished -> carried
    unchanged (keeps the version hash stable)." v3, built from the *same* v2 fetchers
    (gemini still absent, nothing else changed), must be a no-op against v2 -- the
    retirement row is copied byte-for-byte (same `start_date`/`retired_on`), not
    re-stamped with v3's `built_on`."""
    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    gemini_removed_fetchers = {
        "litellm": _litellm_fetcher(_gemini_removed_payload()),
        "aws_price_list": _aws_fetcher(),
    }
    r2 = build(cfg, fetchers=gemini_removed_fetchers, previous=r1.version, built_on=date(2026, 9, 26))
    r3 = build(cfg, fetchers=gemini_removed_fetchers, previous=r2.version, built_on=date(2026, 9, 27))

    assert not r2.is_noop
    assert r3.is_noop
    assert r3.version.version.rsplit("-", 1)[-1] == r2.version.version.rsplit("-", 1)[-1]

    entries = [r for r in r3.retired if r.model_id == "gemini-2-5-flash"]
    assert len(entries) == 1
    assert not entries[0].is_new
    assert entries[0].retired_on == date(2026, 9, 25)  # unchanged from v2, not built_on - 1 (9/26)


def test_retirement_republished_becomes_live_again(cfg, base_fetchers):
    """§3.5: "Retired key published again -> Normal row; the latest row has no
    `retired_on`, so the key is live again." v4 restores the LiteLLM payload v1 used
    -- `gemini-2.5-flash` must come back as an ordinary row, not appear in `retired`,
    and the whole build must not be a no-op (a real change from v3)."""
    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    gemini_removed_fetchers = {
        "litellm": _litellm_fetcher(_gemini_removed_payload()),
        "aws_price_list": _aws_fetcher(),
    }
    r2 = build(cfg, fetchers=gemini_removed_fetchers, previous=r1.version, built_on=date(2026, 9, 26))
    r3 = build(cfg, fetchers=gemini_removed_fetchers, previous=r2.version, built_on=date(2026, 9, 27))
    r4 = build(cfg, fetchers=base_fetchers, previous=r3.version, built_on=date(2026, 9, 28))

    assert not r4.is_noop
    assert not any(r.model_id == "gemini-2-5-flash" for r in r4.retired)
    row = _row(r4.version.rates, "gemini", "gemini-2-5-flash")
    assert row.source == "litellm"
    assert row.retired_on is None


# ------------------------------------------------------------------ unmapped surfaced (§3.3, §6.1)


def test_unmapped_entries_recorded(built):
    """The AWS fixture's Cohere product never matches the registry and has no
    recognizable vendor prefix (§5.2) -- it must surface in `unmapped`, not be
    silently dropped, and (unlike Claude Sonnet 4, a *real* official-source model in
    the same fixture, §5.2, covered by `test_official_source_model_promoted_and_priced`
    below) never becomes a registry model or a priced row."""
    reasons = {(u.source, u.source_key) for u in built.unmapped}
    assert ("aws_price_list", "Cohere Generate Model - Command (Amazon Bedrock Edition)") in reasons
    assert not any(r.model_id == "cohere-generate-model---command" for r in built.version.rates)


# ------------------------------------------------------------------ official-source models (§5.2, §6.1 step 2)


def test_official_source_model_promoted_and_priced(built):
    """§5.2: an AWS/Azure record whose reduced model string does not normalize to a
    LiteLLM model, but has a recognizable vendor prefix, is added to the registry and
    priced on whichever channel(s) publish it. Claude Sonnet 4 (real AWS Bedrock data,
    not in the LiteLLM fixture) -> registry entry vendor `anthropic`, priced on
    `aws_bedrock:us-east-1:regional` (and `:global`), unmatched on the `anthropic`
    vendor channel (the vendor API itself never sold it, per the design's own real
    case: "AWS publishes Claude 3 Haiku, ... that LiteLLM's vendor list has dropped")."""
    assert "claude-sonnet-4" in built.version.models
    assert built.version.models["claude-sonnet-4"].vendor == "anthropic"

    row = _row(built.version.rates, "aws_bedrock:us-east-1:regional", "claude-sonnet-4")
    assert row.source == "aws_price_list"
    assert row.input == pm(Decimal("3.00"))
    assert row.output == pm(Decimal("15.00"))
    # cache_write_1h absent from AWS's older scheme here -- the first §6.5 rule (billed as
    # input) does not fire because cache_write_5m is present; the second rule fills the
    # 1h write from the 5m rate.
    assert row.cache_write_1h == row.cache_write_5m
    assert row.derived == ["cache_write_1h_billed_as_5m"]

    row_global = _row(built.version.rates, "aws_bedrock:us-east-1:global", "claude-sonnet-4")
    assert row_global.source == "aws_price_list"

    assert not any(r.channel == "anthropic" and r.model_id == "claude-sonnet-4" for r in built.version.rates)


# ------------------------------------------------------------------ azure channel, if its fixture exists (§10.1)


@pytest.mark.skipif(not _HAS_AZURE_FIXTURE, reason="azure fixture not available")
def test_azure_channel_wins_from_azure_retail():
    azure_pages = json.loads(AZURE_FIXTURE.read_text())
    azure_items = [item for page in azure_pages for item in page["Items"]]

    def fetch_azure(region: str):
        return azure_items, {"fetched_at": "2026-09-25T09:00:00Z", "region": region}

    azure_cfg = ModelPricingConfig.model_validate(
        {
            "channels": [
                "aws_bedrock:us-east-1:regional",
                "aws_bedrock:us-east-1:global",
                "azure:eastus2:data_zone",
            ],
            "geo": {"us-east-1": "us", "eastus2": "us"},
        }
    )
    result = build(
        azure_cfg,
        fetchers={
            "litellm": _litellm_fetcher(),
            "aws_price_list": _aws_fetcher(),
            "azure_retail": fetch_azure,
        },
        built_on=BUILT_ON,
    )
    row = _row(result.version.rates, "azure:eastus2:data_zone", "gpt-5-6-sol")
    assert row.source == "azure_retail"
    assert row.input == pm(Decimal("4.40"))
