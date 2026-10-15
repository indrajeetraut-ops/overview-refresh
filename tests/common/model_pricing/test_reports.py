"""`reports.render_refresh` / `reports.render_mapping` (design §7, §10.1).

`render_refresh` is exercised against a real `BuildResult` from the same fixtures
`test_build.py` uses (fixture-driven, no network). `render_mapping` is exercised both
against hand-built `MatchResult`s (matched/unmatched/ambiguous, no rates) and against a
real `OrgPrices` built from a small in-memory catalog (with rates and source
provenance), matching the two ways the design describes it being called (§9: a bare
list of results, or the `match` CLI's `prices`+`date` form).
"""
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from common.model_pricing.build import SourceDiff, build
from common.model_pricing.catalog import (
    CatalogVersion,
    MatchResult,
    RateRow,
    RegistryEntry,
    load_catalog,
)
from common.model_pricing.config import ModelPricingConfig
from common.model_pricing.decimals import per_million_to_per_token as pm
from common.model_pricing.reports import (
    format_rate_per_million,
    render_mapping,
    render_refresh,
    render_refresh_json,
)

FIXTURES = Path(__file__).parent / "fixtures"
LITELLM_PAYLOAD = json.loads((FIXTURES / "litellm_subset.json").read_text())
AWS_PAYLOAD = json.loads((FIXTURES / "aws_us_east_1_subset.json").read_text())

BUILT_ON = date(2026, 9, 25)


@pytest.fixture(autouse=True)
def _no_gcp_key(monkeypatch):
    monkeypatch.delenv("GCP_BILLING_API_KEY", raising=False)


def _litellm_fetcher(payload=None):
    payload = LITELLM_PAYLOAD if payload is None else payload

    def fetch(commit=None):
        return payload, {"commit_sha": "d3462c65", "commit_date": "2026-09-24T18:08:09Z"}

    return fetch


def _aws_fetcher(payload=None):
    payload = AWS_PAYLOAD if payload is None else payload

    def fetch(region):
        return payload, {"publication_date": "2026-09-22T16:44:18Z", "version": "1"}

    return fetch


@pytest.fixture
def cfg():
    return ModelPricingConfig.model_validate(
        {
            "channels": ["aws_bedrock:us-east-1:regional", "aws_bedrock:us-east-1:global"],
            "geo": {"us-east-1": "us"},
        }
    )


@pytest.fixture
def base_fetchers():
    return {"litellm": _litellm_fetcher(), "aws_price_list": _aws_fetcher()}


# ================================================================== render_refresh (§7)


def test_render_refresh_header_and_summary_sections(cfg, base_fetchers):
    """The Markdown summary's header and `## Summary` tables/counts (§7) -- sources
    table, channels table, change counts, unmapped counts grouped by (source, reason),
    source-diff count."""
    result = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    text = render_refresh(result)

    assert text.startswith(f"# Refresh report — {result.version.version}")
    assert f"built_on: {BUILT_ON.isoformat()}" in text
    assert "run time (UTC):" in text
    assert "previous version: first version" in text
    assert "result: new version" in text

    assert "## Summary" in text
    assert "### Sources" in text
    assert "| source | status | provenance | rows won | unmapped |" in text
    assert "`litellm`" in text and "commit_sha: d3462c65" in text
    assert "`aws_price_list`" in text and "publication_date: 2026-09-22T16:44:18Z" in text

    assert "### Channels" in text
    assert "| channel | rows | first source | LiteLLM fallback |" in text
    assert "`aws_bedrock:us-east-1:regional`" in text

    # First build -> the added/changed/retired counts are replaced by a "first
    # version" note (§7), not shown as zero counts.
    assert "- first version (no previous catalog to diff against)" in text

    # AWS fixture's Cohere product cannot be mapped (no known vendor prefix, §5.2) ->
    # counted, grouped by (source, reason) -- never named (§7). Claude Sonnet 4, the
    # fixture's other AWS-only model, *is* promoted (§5.2) and so is not unmapped.
    assert "unmapped, by source and reason" in text
    assert "`aws_price_list`: unknown vendor prefix (1)" in text
    assert "Cohere" not in text

    assert "source diffs (winner != litellm): 0" in text


def test_render_refresh_no_per_record_names(cfg, base_fetchers):
    """§7: the summary never names an individual unmapped record, model, or channel
    row -- only tables and counts. Exercised on a build with real added/changed/
    retired/source-diff activity so this isn't vacuously true."""
    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)

    modified = deepcopy(LITELLM_PAYLOAD)
    modified["claude-sonnet-5"]["input_cost_per_token"] *= 3  # +200%, > 50% threshold
    del modified["gemini/gemini-2.5-flash"]
    modified["claude-iota-1"] = {
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
        "litellm_provider": "anthropic",
        "mode": "chat",
    }

    r2 = build(
        cfg,
        fetchers={"litellm": _litellm_fetcher(modified), "aws_price_list": _aws_fetcher()},
        previous=r1.version,
        built_on=date(2026, 9, 26),
    )
    assert r2.added and r2.changed and r2.retired  # activity to hide

    text = render_refresh(r2)
    assert "- added: 1" in text
    assert "- changed: 1" in text
    assert "- retired: 1" in text
    # The header + `## Summary` (added/changed/retired counts, unmapped counts) never
    # name a record. `## Errors` is a separate promise (one line per excluded row /
    # warning, §7) -- the >50% change warning legitimately names `claude-sonnet-5`
    # there, so only added/retired names (which raise no warning) are checked against
    # the *whole* text.
    summary_text = text.split("## Errors")[0]
    for name in ("claude-iota-1", "claude-sonnet-5", "gemini-2-5-flash"):
        assert name not in summary_text
    for name in ("claude-iota-1", "gemini-2-5-flash"):
        assert name not in text


def test_render_refresh_source_diff_count_only(cfg, base_fetchers):
    """`BuildResult.source_diff` is only populated when the winning (non-litellm)
    source's rates actually differ from litellm's for the same key -- this fixture
    pair happens to agree, so exercise the rendering directly against a synthetic
    `BuildResult` instead of depending on a fixture producing a real diff. The
    Markdown summary shows only the count; `render_refresh_json` carries the detail
    (`test_render_refresh_json_has_full_detail_and_parses` below)."""
    result = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    with_diff = result.__class__(
        version=result.version,
        is_noop=result.is_noop,
        warnings=result.warnings,
        source_diff=[
            SourceDiff(
                channel="aws_bedrock:us-east-1:regional",
                model_id="claude-sonnet-5",
                source="aws_price_list",
                winner_rates={"input": "0.0000022"},
                litellm_rates={"input": "0.000002"},
            )
        ],
        retired=result.retired,
        excluded=result.excluded,
        unmapped=result.unmapped,
        source_provenance=result.source_provenance,
        channel_source_counts=result.channel_source_counts,
        run_at=result.run_at,
        previous_version=result.previous_version,
        is_first_build=result.is_first_build,
    )
    text = render_refresh(with_diff)
    assert "source diffs (winner != litellm): 1" in text
    assert "claude-sonnet-5" not in text
    assert "0.0000022" not in text


def test_render_refresh_errors_none_when_clean(cfg, base_fetchers):
    """`## Errors` says "None" when there is nothing to report (§7) -- this fixture
    pair produces no excluded rows and no warnings."""
    result = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    assert result.excluded == [] and result.warnings == []

    text = render_refresh(result)
    assert "## Errors" in text
    errors_section = text.split("## Errors")[1]
    assert "- None" in errors_section


def test_render_refresh_excluded_and_warnings_listed(cfg, base_fetchers):
    """Excluded rows and warnings each render as one line under `## Errors` (§7)."""
    result = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    with_errors = result.__class__(
        version=result.version,
        is_noop=result.is_noop,
        warnings=["azure_retail: something odd happened"],
        source_diff=result.source_diff,
        retired=result.retired,
        excluded=[
            {
                "channel": "openai",
                "model_id": "gpt-broken",
                "source": "litellm",
                "reason": "input or output rate is negative",
            }
        ],
        unmapped=result.unmapped,
        source_provenance=result.source_provenance,
        channel_source_counts=result.channel_source_counts,
        run_at=result.run_at,
        previous_version=result.previous_version,
        is_first_build=result.is_first_build,
    )
    text = render_refresh(with_errors)
    errors_section = text.split("## Errors")[1]
    assert "- None" not in errors_section
    assert "gpt-broken" in errors_section and "negative" in errors_section
    assert "azure_retail: something odd happened" in errors_section


def test_render_refresh_previous_version_and_run_at(cfg, base_fetchers):
    """The header's "previous version" line names the actual previous version string
    (not "first version") once there is one, and a custom `run_at` (§6/§7's
    "injectable for tests") shows up verbatim."""
    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    fixed_run_at = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
    r2 = build(
        cfg,
        fetchers=base_fetchers,
        previous=r1.version,
        built_on=date(2026, 9, 26),
        run_at=fixed_run_at,
    )
    assert r2.previous_version == r1.version.version
    assert r2.is_noop  # unchanged fixtures -> no-op; header still reports correctly

    text = render_refresh(r2)
    assert f"previous version: {r1.version.version}" in text
    assert "run time (UTC): 2026-09-26T12:00:00Z" in text
    assert "result: no-op" in text


def test_render_refresh_json_has_full_detail_and_parses(cfg, base_fetchers):
    """`render_refresh_json` (§7's "Refresh detail") carries everything the Markdown
    summary counts but never names: full `added`/`changed`/`retired`/`unmapped`
    lists, `Changed.pct` as a fixed 2-decimal string, and parses as JSON."""
    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)

    modified = deepcopy(LITELLM_PAYLOAD)
    modified["claude-opus-4-8"]["input_cost_per_token"] = 5.5e-06  # +10%
    del modified["gemini/gemini-2.5-flash"]
    modified["claude-iota-1"] = {
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
        "litellm_provider": "anthropic",
        "mode": "chat",
    }
    r2 = build(
        cfg,
        fetchers={"litellm": _litellm_fetcher(modified), "aws_price_list": _aws_fetcher()},
        previous=r1.version,
        built_on=date(2026, 9, 26),
    )

    text = render_refresh_json(r2)
    data = json.loads(text)  # must parse

    assert data["version"] == r2.version.version
    assert data["previous_version"] == r1.version.version
    assert data["is_first_build"] is False
    assert data["is_noop"] is False

    added_ids = {a["model_id"] for a in data["added"]}
    assert "claude-iota-1" in added_ids

    changed = next(c for c in data["changed"] if c["model_id"] == "claude-opus-4-8")
    assert changed["token_type"] == "input"
    assert changed["pct"] == "10.00"  # fixed 2-decimal string, not to_rate_str

    retired_ids = {r["model_id"] for r in data["retired"]}
    assert "gemini-2-5-flash" in retired_ids
    retired_entry = next(r for r in data["retired"] if r["model_id"] == "gemini-2-5-flash")
    assert retired_entry["is_new"] is True
    assert retired_entry["retired_on"] == "2026-09-25"  # built_on - 1 day

    unmapped_reasons = {u["reason"] for u in data["unmapped"]}
    assert unmapped_reasons  # non-empty: real per-record detail, unlike the summary
    unmapped_keys = {u["source_key"] for u in data["unmapped"]}
    assert any("Cohere" in k for k in unmapped_keys)


def test_render_refresh_json_stable_key_order(cfg, base_fetchers):
    result = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    data = json.loads(render_refresh_json(result))
    assert list(data.keys()) == [
        "version",
        "built_on",
        "run_at",
        "previous_version",
        "is_first_build",
        "is_noop",
        "sources",
        "channel_source_counts",
        "added",
        "changed",
        "retired",
        "source_diff",
        "unmapped",
        "excluded",
        "warnings",
    ]


def test_render_refresh_noop_result(cfg, base_fetchers):
    r1 = build(cfg, fetchers=base_fetchers, built_on=BUILT_ON)
    r2 = build(cfg, fetchers=base_fetchers, previous=r1.version, built_on=date(2026, 9, 26))
    assert r2.is_noop
    text = render_refresh(r2)
    assert "result: no-op" in text


# ================================================================== render_mapping (§7, §9)


def _mr(usage_model_id, status, model_id=None, vendor=None, channel=None, step=None, reason=None):
    return MatchResult(
        usage_model_id=usage_model_id,
        org_id="acme",
        model_id=model_id,
        vendor=vendor,
        channel=channel,
        status=status,
        step=step,
        reason=reason,
    )


def test_render_mapping_md_failures_first_no_prices():
    results = [
        _mr("claude-sonnet-5", "matched", model_id="claude-sonnet-5", vendor="anthropic",
            channel="anthropic", step="normalized"),
        _mr("totally-unknown", "unmatched", reason="model id not recognized: 'totally-unknown'"),
        _mr("gpt-4o-2025-06-01", "ambiguous", reason="ambiguous model id: 'gpt-4o' matches several"),
    ]
    text = render_mapping(results, fmt="md")

    lines = [l for l in text.splitlines() if l.startswith("|")]
    header, sep, *rows = lines
    assert "usage_model_id" in header
    assert "reason" in header

    # Failures (unmatched/ambiguous) must come before the matched row.
    statuses_in_order = []
    for row in rows:
        if "totally-unknown" in row:
            statuses_in_order.append("unmatched")
        elif "gpt-4o-2025-06-01" in row:
            statuses_in_order.append("ambiguous")
        elif "claude-sonnet-5" in row:
            statuses_in_order.append("matched")
    assert statuses_in_order[-1] == "matched"
    assert set(statuses_in_order[:-1]) == {"unmatched", "ambiguous"}

    # No prices given -> rate columns render as "-".
    matched_row = next(r for r in rows if "claude-sonnet-5" in r)
    assert matched_row.count("| - ") >= 5 or matched_row.count("-") >= 5


def test_render_mapping_json_no_prices():
    results = [
        _mr("claude-sonnet-5", "matched", model_id="claude-sonnet-5", vendor="anthropic",
            channel="anthropic", step="normalized"),
        _mr("totally-unknown", "unmatched", reason="model id not recognized: 'totally-unknown'"),
    ]
    text = render_mapping(results, fmt="json")
    data = json.loads(text)
    assert len(data) == 2
    assert data[0]["status"] == "unmatched"  # failures first
    assert data[1]["status"] == "matched"
    assert data[1]["model_id"] == "claude-sonnet-5"
    assert data[1]["rates"] == {tt: None for tt in data[1]["rates"]}


def test_render_mapping_invalid_format_raises():
    with pytest.raises(ValueError):
        render_mapping([], fmt="yaml")


# ------------------------------------------------------------------ with real OrgPrices (rates/source)


def _write_version(catalog_root: Path, built_on: date, rates, models=None) -> str:
    stub = CatalogVersion(version="pending", built_on=built_on, models=models or {}, rates=rates)
    version = f"{built_on.isoformat()}-{stub.compute_hash8()}"
    cv = stub.model_copy(update={"version": version})
    catalog_root.mkdir(parents=True, exist_ok=True)
    (catalog_root / f"{version}.json").write_text(cv.model_dump_json())
    return version


def _write_index(catalog_root: Path, versions) -> None:
    (catalog_root / "index.json").write_text(json.dumps({"versions": versions}))


@pytest.fixture
def small_catalog(tmp_path):
    catalog_root = tmp_path / "catalog"
    config_root = tmp_path / "config"
    built_on = date(2026, 9, 25)
    version = _write_version(
        catalog_root,
        built_on,
        rates=[
            RateRow(
                channel="anthropic", model_id="claude-sonnet-5", start_date=built_on,
                input=pm(Decimal("2.00")), output=pm(Decimal("10.00")),
                cache_read=pm(Decimal("0.20")), cache_write_5m=pm(Decimal("2.50")),
                cache_write_1h=pm(Decimal("4.00")),
                source="litellm", source_key="claude-sonnet-5",
            ),
        ],
        models={"claude-sonnet-5": RegistryEntry(vendor="anthropic", names_seen=["claude-sonnet-5"])},
    )
    _write_index(catalog_root, [version])
    return catalog_root, config_root


def test_render_mapping_with_prices_shows_rates_and_source(small_catalog):
    catalog_root, config_root = small_catalog
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    prices = catalog.for_org("acme")

    results = prices.mapping_report(["claude-sonnet-5", "totally-unknown"])
    on_date = date(2026, 10, 1)
    text = render_mapping(results, fmt="md", prices=prices, date=on_date)

    assert "litellm" in text
    assert "claude-sonnet-5" in text
    assert format_rate_per_million(pm(Decimal("2.00"))) in text  # "2"
    assert format_rate_per_million(pm(Decimal("10.00"))) in text  # "10"

    data = json.loads(render_mapping(results, fmt="json", prices=prices, date=on_date))
    matched = next(e for e in data if e["status"] == "matched")
    assert matched["source"] == "litellm"
    assert matched["rates"]["input"] == "2"
    assert matched["rates"]["output"] == "10"

    unmatched = next(e for e in data if e["status"] == "unmatched")
    assert unmatched["source"] is None
    assert unmatched["rates"] == {tt: None for tt in unmatched["rates"]}


def test_format_rate_per_million():
    assert format_rate_per_million(None) == "-"
    assert format_rate_per_million(pm(Decimal("2.20"))) == "2.2"
    assert format_rate_per_million(pm(Decimal("0.22"))) == "0.22"
    assert format_rate_per_million(pm(Decimal("0"))) == "0"
