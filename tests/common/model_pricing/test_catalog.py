"""`load_catalog` and lookup (design §2.4, §3.1, §3.3, §3.4, §4.3, §5.3, §9).

Catalogs are built in `tmp_path` by constructing `CatalogVersion` objects directly
(rather than via `build.py`, which is a different task's write path) and writing them
out with a real `hash8`, exactly as `update` would, plus an `index.json`. No network,
no fixtures shared with the source-adapter tests.
"""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from common.model_pricing.catalog import (
    CatalogVersion,
    RateRow,
    RegistryEntry,
    TOKEN_TYPES,
    load_catalog,
)
from common.model_pricing.decimals import per_million_to_per_token as pm
from common.model_pricing.errors import CatalogError, ConfigError


# ------------------------------------------------------------------ test helpers


def _row(channel: str, model_id: str, start_date: date, **kwargs) -> RateRow:
    kwargs.setdefault("source", "test")
    kwargs.setdefault("source_key", model_id)
    return RateRow(channel=channel, model_id=model_id, start_date=start_date, **kwargs)


def _write_version(
    catalog_root: Path,
    built_on: date,
    rates: list[RateRow],
    models: dict[str, RegistryEntry] | None = None,
) -> str:
    """Build a `CatalogVersion` with a correct `hash8`, write it to `catalog_root`,
    and return its `version` string. Does not touch `index.json`."""
    stub = CatalogVersion(version="pending", built_on=built_on, models=models or {}, rates=rates)
    version = f"{built_on.isoformat()}-{stub.compute_hash8()}"
    cv = stub.model_copy(update={"version": version})
    catalog_root.mkdir(parents=True, exist_ok=True)
    (catalog_root / f"{version}.json").write_text(cv.model_dump_json())
    return version


def _write_index(catalog_root: Path, versions: list[str]) -> None:
    (catalog_root / "index.json").write_text(json.dumps({"versions": versions}))


def _write_channel_config(config_root: Path, orgs: dict) -> None:
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "channel_config.yaml").write_text(yaml.safe_dump({"orgs": orgs}))


# ================================================================== §3.4 lookup + version binding


@pytest.fixture
def two_version_catalog(tmp_path):
    """Two versions of one `anthropic`-channel table (design §3.4's own worked
    example, `claude-x`, plus three more keys exercising the other rules):

    - `claude-x`: priced in both versions, 3.00/15.00 -> 2.00/10.00 (the design's
      own §3.4 table, verbatim).
    - `claude-old`: priced only in v1 -- "no longer published", keeps its last price.
    - `claude-sunset`: priced only in v1, with `retired_on` -- retired after that date.
    - `claude-revived`: priced in both, v1 has `retired_on`, v2 does not -- the
      *latest* row decides retirement, not the row a date would otherwise resolve to.
    """
    catalog_root = tmp_path / "catalog"
    config_root = tmp_path / "config"

    v1_date = date(2026, 9, 25)
    v2_date = date(2026, 12, 1)

    v1 = _write_version(
        catalog_root,
        v1_date,
        rates=[
            _row("anthropic", "claude-x", v1_date, input=pm(3), output=pm(15)),
            _row("anthropic", "claude-old", v1_date, input=pm(1), output=pm(5)),
            _row(
                "anthropic", "claude-sunset", v1_date,
                input=pm(1), output=pm(5), retired_on=date(2026, 10, 1),
            ),
            _row(
                "anthropic", "claude-revived", v1_date,
                input=pm(1), output=pm(5), retired_on=date(2026, 10, 15),
            ),
        ],
        models={
            "claude-x": RegistryEntry(vendor="anthropic", names_seen=["claude-x"]),
            "claude-old": RegistryEntry(vendor="anthropic", names_seen=["claude-old"]),
            "claude-sunset": RegistryEntry(vendor="anthropic", names_seen=["claude-sunset"]),
            "claude-revived": RegistryEntry(vendor="anthropic", names_seen=["claude-revived"]),
        },
    )
    v2 = _write_version(
        catalog_root,
        v2_date,
        rates=[
            _row("anthropic", "claude-x", v2_date, input=pm(2), output=pm(10)),
            # claude-revived comes back with no retired_on -- un-retires the key.
            _row("anthropic", "claude-revived", v2_date, input=Decimal("0.0000015"), output=pm(7.5)),
        ],
    )
    _write_index(catalog_root, [v1, v2])
    return catalog_root, config_root, v1, v2


def test_lookup_table_backward_forward(two_version_catalog):
    """The exact §3.4 worked example: backward before the first version, forward
    within v1's span, forward into v2's span."""
    catalog_root, config_root, v1, v2 = two_version_catalog
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    prices = catalog.for_org("no-config-org")
    match = prices.resolve("claude-x")
    assert match.status == "matched"

    assert prices.rate(match, "input", date(2026, 7, 15)) == pm(3)
    assert prices.rate(match, "output", date(2026, 7, 15)) == pm(15)

    assert prices.rate(match, "input", date(2026, 10, 20)) == pm(3)
    assert prices.rate(match, "output", date(2026, 10, 20)) == pm(15)

    assert prices.rate(match, "input", date(2027, 1, 10)) == pm(2)
    assert prices.rate(match, "output", date(2027, 1, 10)) == pm(10)


def test_model_absent_from_newer_version_keeps_last_price(two_version_catalog):
    catalog_root, config_root, _, _ = two_version_catalog
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    prices = catalog.for_org("no-config-org")
    match = prices.resolve("claude-old")
    assert match.status == "matched"
    # Long after v2 was built; claude-old was never republished, so v1's row still applies.
    assert prices.rate(match, "input", date(2027, 1, 10)) == pm(1)


def test_retired_rule(two_version_catalog):
    catalog_root, config_root, _, _ = two_version_catalog
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    prices = catalog.for_org("no-config-org")
    match = prices.resolve("claude-sunset")
    assert match.status == "matched"  # resolve() does not look at dates
    assert prices.rate(match, "input", date(2026, 9, 30)) == pm(1)
    assert prices.rate(match, "input", date(2026, 10, 1)) == pm(1)  # not yet past retired_on
    assert prices.rate(match, "input", date(2026, 10, 2)) is None  # retired


def test_retired_rule_uses_latest_row_not_resolved_row(two_version_catalog):
    """`claude-revived`: v1's row is retired 2026-10-15; v2 republishes it with no
    `retired_on`. The *latest* row (v2) has no `retired_on`, so the key is never
    retired, even on a date that only v1's row covers."""
    catalog_root, config_root, _, _ = two_version_catalog
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    prices = catalog.for_org("no-config-org")
    match = prices.resolve("claude-revived")

    # Before v1's start -> backward -> v1's row, not retired.
    assert prices.rate(match, "input", date(2026, 9, 20)) == pm(1)
    # After v1's retired_on but before v2 starts -> forward candidate is still v1's
    # row, but the latest row overall (v2) has no retired_on, so it applies.
    assert prices.rate(match, "input", date(2026, 11, 1)) == pm(1)
    # Once v2 starts, its own row applies.
    assert prices.rate(match, "input", date(2026, 12, 15)) == Decimal("0.0000015")


def test_pinning_an_earlier_version_ignores_later_versions(two_version_catalog):
    catalog_root, config_root, v1, v2 = two_version_catalog
    catalog = load_catalog(version=v1, root=catalog_root, config_root=config_root)
    assert catalog.version == v1
    assert catalog.loaded_versions == (v1,)

    prices = catalog.for_org("no-config-org")
    match = prices.resolve("claude-x")
    # v2's 2.00 must never be visible: even a date only v2 covers still gets v1's price.
    assert prices.rate(match, "input", date(2027, 1, 10)) == pm(3)

    # claude-revived's v2 (un-retiring) row was never loaded, so it is retired again.
    revived = prices.resolve("claude-revived")
    assert prices.rate(revived, "input", date(2026, 12, 15)) is None


# ================================================================== §2.4 example


@pytest.fixture
def example_catalog(tmp_path):
    """The §2.4 worked example: talium.ai runs Claude Sonnet 5 on Bedrock us-east-1
    regional and Codex (`gpt-5.6-sol`) on the OpenAI API. Also prices Haiku 4.5 on the
    `anthropic` vendor channel only (not on Bedrock), to exercise no-fallback."""
    catalog_root = tmp_path / "catalog"
    config_root = tmp_path / "config"
    built_on = date(2026, 9, 25)

    _write_index(
        catalog_root,
        [
            _write_version(
                catalog_root,
                built_on,
                rates=[
                    _row(
                        "aws_bedrock:us-east-1:regional", "claude-sonnet-5", built_on,
                        input=pm(Decimal("2.20")), output=pm(11),
                        cache_read=pm(Decimal("0.22")), cache_write_5m=pm(Decimal("2.75")),
                        cache_write_1h=pm(Decimal("4.40")),
                    ),
                    _row(
                        "anthropic", "claude-sonnet-5", built_on,
                        input=pm(2), output=pm(10),
                        cache_read=pm(Decimal("0.20")), cache_write_5m=pm(Decimal("2.50")),
                        cache_write_1h=pm(4),
                    ),
                    _row(
                        "anthropic", "claude-haiku-4-5", built_on,
                        input=pm(Decimal("1.10")), output=pm(Decimal("5.50")),
                    ),
                    _row(
                        "openai", "gpt-5-6-sol", built_on,
                        input=pm(4), output=pm(20),
                        cache_read=pm(Decimal("0.40")), cache_write_5m=pm(5),
                    ),
                ],
                models={
                    "claude-sonnet-5": RegistryEntry(
                        vendor="anthropic", names_seen=["claude-sonnet-5"]
                    ),
                    "claude-haiku-4-5": RegistryEntry(
                        vendor="anthropic", names_seen=["claude-haiku-4-5"]
                    ),
                    "gpt-5-6-sol": RegistryEntry(vendor="openai", names_seen=["gpt-5.6-sol"]),
                },
            )
        ],
    )
    _write_channel_config(
        config_root, {"talium.ai": {"anthropic": "aws_bedrock:us-east-1:regional"}}
    )
    return catalog_root, config_root, built_on


def test_example_talium_ai_bedrock_sonnet(example_catalog):
    catalog_root, config_root, built_on = example_catalog
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    prices = catalog.for_org("talium.ai")

    match = prices.resolve("claude-sonnet-5")
    assert match.status == "matched"
    assert match.model_id == "claude-sonnet-5"
    assert match.vendor == "anthropic"
    assert match.channel == "aws_bedrock:us-east-1:regional"
    assert match.step == "normalized"
    assert prices.rate(match, "input", built_on) == pm(Decimal("2.20"))

    # The Bedrock-prefixed observed string normalizes to the same model_id/channel.
    match2 = prices.resolve("us.anthropic.claude-sonnet-5")
    assert match2.status == "matched"
    assert match2.model_id == "claude-sonnet-5"
    assert match2.channel == "aws_bedrock:us-east-1:regional"
    assert prices.rate(match2, "input", built_on) == pm(Decimal("2.20"))


def test_example_talium_ai_openai_codex_default_channel(example_catalog):
    catalog_root, config_root, built_on = example_catalog
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    prices = catalog.for_org("talium.ai")

    match = prices.resolve("gpt-5.6-sol")
    assert match.status == "matched"
    assert match.model_id == "gpt-5-6-sol"
    assert match.vendor == "openai"
    assert match.channel == "openai"  # not configured for talium.ai -> vendor default
    assert prices.rate(match, "input", built_on) == pm(4)
    assert prices.rate(match, "cache_write_1h", built_on) is None  # never priced, not 0


def test_example_unmatched_model(example_catalog):
    catalog_root, config_root, _ = example_catalog
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    prices = catalog.for_org("talium.ai")
    match = prices.resolve("claude-sonnet")
    assert match.status == "unmatched"
    assert match.model_id is None


def test_org_without_config_uses_vendor_channel(example_catalog):
    catalog_root, config_root, built_on = example_catalog
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    prices = catalog.for_org("some-other-org")  # not in channel_config.yaml at all
    match = prices.resolve("claude-sonnet-5")
    assert match.status == "matched"
    assert match.channel == "anthropic"
    assert prices.rate(match, "input", built_on) == pm(2)


def test_no_fallback_across_channels(example_catalog):
    """Haiku 4.5 is priced on `anthropic` but not on Bedrock. talium.ai's config
    routes anthropic through Bedrock, so Haiku must come back unmatched -- never
    silently priced from the vendor channel."""
    catalog_root, config_root, _ = example_catalog
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    prices = catalog.for_org("talium.ai")
    match = prices.resolve("claude-haiku-4-5")
    assert match.status == "unmatched"
    assert match.model_id == "claude-haiku-4-5"  # normalize succeeded
    assert match.channel == "aws_bedrock:us-east-1:regional"
    assert "no price on channel aws_bedrock:us-east-1:regional" in match.reason

    # The same model, for an org that buys Anthropic directly, matches fine.
    other = catalog.for_org("some-other-org").resolve("claude-haiku-4-5")
    assert other.status == "matched"
    assert other.channel == "anthropic"


def test_rates_convenience_returns_all_token_types(example_catalog):
    catalog_root, config_root, built_on = example_catalog
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    prices = catalog.for_org("talium.ai")
    match = prices.resolve("claude-sonnet-5")
    rates = prices.rates(match, built_on)
    assert set(rates) == set(TOKEN_TYPES)
    assert rates["input"] == pm(Decimal("2.20"))
    assert rates["output"] == pm(11)
    assert rates["cache_read"] == pm(Decimal("0.22"))


def test_mapping_report_failures_first(example_catalog):
    catalog_root, config_root, _ = example_catalog
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    prices = catalog.for_org("talium.ai")
    report = prices.mapping_report(["claude-sonnet-5", "claude-sonnet", "gpt-5.6-sol"])
    assert [r.status for r in report] == ["unmatched", "matched", "matched"]
    assert report[0].usage_model_id == "claude-sonnet"
    assert {r.usage_model_id for r in report[1:]} == {"claude-sonnet-5", "gpt-5.6-sol"}


# ================================================================== errors


def test_configured_channel_not_priced_raises_config_error(tmp_path):
    catalog_root = tmp_path / "catalog"
    config_root = tmp_path / "config"
    built_on = date(2026, 9, 25)
    version = _write_version(
        catalog_root,
        built_on,
        rates=[_row("anthropic", "claude-sonnet-5", built_on, input=pm(2), output=pm(10))],
        models={"claude-sonnet-5": RegistryEntry(vendor="anthropic", names_seen=["claude-sonnet-5"])},
    )
    _write_index(catalog_root, [version])
    # aws_bedrock:us-east-1:regional is never priced in this catalog.
    _write_channel_config(
        config_root, {"talium.ai": {"anthropic": "aws_bedrock:us-east-1:regional"}}
    )

    catalog = load_catalog(root=catalog_root, config_root=config_root)
    with pytest.raises(ConfigError):
        catalog.for_org("talium.ai")


def test_tampered_version_file_raises_catalog_error(tmp_path):
    catalog_root = tmp_path / "catalog"
    built_on = date(2026, 9, 25)
    version = _write_version(
        catalog_root,
        built_on,
        rates=[_row("anthropic", "claude-x", built_on, input=pm(3), output=pm(15))],
        models={"claude-x": RegistryEntry(vendor="anthropic", names_seen=["claude-x"])},
    )
    _write_index(catalog_root, [version])

    # Mutate a served price without touching the version string / hash8.
    path = catalog_root / f"{version}.json"
    data = json.loads(path.read_text())
    data["rates"][0]["input"] = "0.000009"
    path.write_text(json.dumps(data))

    with pytest.raises(CatalogError):
        load_catalog(root=catalog_root)


def test_unknown_version_raises_catalog_error(tmp_path):
    catalog_root = tmp_path / "catalog"
    built_on = date(2026, 9, 25)
    version = _write_version(
        catalog_root,
        built_on,
        rates=[_row("anthropic", "claude-x", built_on, input=pm(3), output=pm(15))],
    )
    _write_index(catalog_root, [version])
    with pytest.raises(CatalogError):
        load_catalog(version="2099-01-01-deadbeef", root=catalog_root)


def test_no_index_raises_catalog_error(tmp_path):
    catalog_root = tmp_path / "catalog-does-not-exist"
    with pytest.raises(CatalogError):
        load_catalog(root=catalog_root)


def test_vendor_conflict_across_versions_raises_catalog_error(tmp_path):
    catalog_root = tmp_path / "catalog"
    v1_date = date(2026, 9, 25)
    v2_date = date(2026, 12, 1)
    v1 = _write_version(
        catalog_root,
        v1_date,
        rates=[_row("anthropic", "claude-x", v1_date, input=pm(3), output=pm(15))],
        models={"claude-x": RegistryEntry(vendor="anthropic", names_seen=["claude-x"])},
    )
    # Same model_id, different vendor in a later version -- should never happen from a
    # real build, but load_catalog must not silently pick one.
    v2 = _write_version(
        catalog_root,
        v2_date,
        rates=[_row("openai", "claude-x", v2_date, input=pm(3), output=pm(15))],
        models={"claude-x": RegistryEntry(vendor="openai", names_seen=["claude-x"])},
    )
    _write_index(catalog_root, [v1, v2])
    with pytest.raises(CatalogError):
        load_catalog(root=catalog_root)
