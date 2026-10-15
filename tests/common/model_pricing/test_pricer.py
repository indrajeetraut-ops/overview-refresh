"""`Pricer` (#68 design §2.2, §4.1): the legacy `price_tokens` behaviours over the catalog.

A price is either right or absent: no partial answer, because a partial answer is
indistinguishable from a real one once stored.
"""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from common.model_pricing import Pricer, price_to_micros, split_cache_write
from common.model_pricing.errors import ConfigError

from .helpers import make_pricer, row

AT = datetime(2026, 10, 1, tzinfo=timezone.utc)
FULL = {"input": 5, "output": 25, "cache_read": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10}


@pytest.fixture(scope="module")
def real() -> Pricer:
    return Pricer.load(None, None)


# ------------------------------------------------------------------ real catalog

def test_worked_example_is_exactly_six(real):
    price = real.price_tokens("claude-sonnet-5", {"input": 1_000_000, "cache_write": 1_000_000},
                              "2026-10-01")
    assert price == Decimal("6.00")


def test_known_price_math(real):
    assert real.price_tokens("claude-opus-5", {"input": 1_000_000, "output": 1_000_000}, AT) \
        == Decimal("30.00")


def test_every_token_kind_contributes(real):
    price = real.price_tokens("claude-opus-5", {
        "input": 1_000_000, "output": 1_000_000, "cache_read": 1_000_000,
        "cache_write_5m": 1_000_000, "cache_write_1h": 1_000_000}, AT)
    assert price == Decimal("46.75")


def test_shipped_rates_reproduce_a_real_vendor_cost(real):
    price = real.price_tokens("claude-opus-5", {
        "input": 2, "output": 265, "cache_read": 22042, "cache_write_1h": 48630}, AT)
    assert price_to_micros(price) == 503956
    haiku = real.price_tokens("claude-haiku-4-5-20251001", {"input": 540, "output": 14}, AT)
    assert price_to_micros(haiku) == 610


def test_dated_haiku_id_resolves_to_registry_id(real):
    assert real.rates("claude-haiku-4-5-20251001", AT) == real.rates("claude-haiku-4-5", AT)
    assert real.rates("claude-haiku-4-5-20251001", AT) is not None


def test_missing_kinds_are_zero_not_an_error(real):
    assert real.price_tokens("claude-opus-5", {"output": 1_000_000}, AT) == Decimal("25.00")
    assert real.price_tokens("claude-opus-5", {}, AT) == Decimal(0)


def test_unknown_model_is_none(real):
    assert real.price_tokens("some-model-we-have-never-seen", {"input": 100}, AT) is None
    assert real.price_tokens(None, {"input": 100}, AT) is None
    assert real.price_tokens("", {"input": 100}, AT) is None
    assert real.rates("some-model-we-have-never-seen", AT) is None


def test_every_seen_model_prices_a_request(real):
    for model in ["claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                  "claude-sonnet-5", "claude-sonnet-4-6", "claude-fable-5",
                  "claude-haiku-4-5-20251001", "claude-sonnet-4-5-20250929"]:
        price = real.price_tokens(model, {"input": 1000, "output": 1000, "cache_read": 1000,
                                          "cache_write_1h": 1000}, AT)
        assert price and price > 0, model


def test_early_date_uses_earliest_row_not_none(real):
    """Design §4 #7: legacy returned None before the first block; the catalog's backward rule
    applies the first row."""
    early = real.price_tokens("claude-opus-5", {"input": 1_000_000}, date(2025, 1, 1))
    assert early == Decimal("5.00")


def test_cache_multipliers_where_the_catalog_follows_them(real):
    checked = 0
    for model_id, r in real.models(AT).items():
        if None in r.values() or not r["input"]:
            continue
        if (r["cache_write_5m"] == r["input"] * Decimal("1.25")
                and r["cache_write_1h"] == r["input"] * 2
                and r["cache_read"] == r["input"] / 10):
            checked += 1
    assert checked >= 1


def test_models_lists_anthropic_registry_ids(real):
    models = real.models(AT)
    assert {"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"} <= set(models)
    assert "claude-haiku-4-5-20251001" not in models
    assert models["claude-opus-5"]["input"] == Decimal("0.000005")


def test_load_with_no_org_and_unknown_org_use_vendor_channels():
    a = Pricer.load(None, None).rates("claude-opus-5", AT)
    b = Pricer.load("nobody.example", None).rates("claude-opus-5", AT)
    assert a == b


# ------------------------------------------------------------------ custom tables

def test_dated_model_two_rows_forward_rule(tmp_path):
    from .helpers import make_catalog, write_version, write_index
    from datetime import date as d
    root, cfg, _ = make_catalog(tmp_path, [row("m", d(2026, 1, 1), per_million={"input": 10})])
    v2 = write_version(root, d(2026, 6, 1), [row("m", d(2026, 6, 1), per_million={"input": 30})])
    import json
    idx = json.loads((root / "index.json").read_text())["versions"] + [v2]
    write_index(root, idx)
    p = Pricer.load(None, None, root=root, config_root=cfg, history_path=tmp_path / "none.yaml")
    t = {"input": 1_000_000}
    assert p.price_tokens("m", t, date(2026, 5, 31)) == Decimal("10")
    assert p.price_tokens("m", t, date(2026, 6, 1)) == Decimal("30")
    assert p.price_tokens("m", t, date(2027, 1, 1)) == Decimal("30")


def test_partial_rate_refuses_whole_request(tmp_path):
    p = make_pricer(tmp_path, [row("m", date(2026, 1, 1), per_million={"input": 10})])
    assert p.price_tokens("m", {"input": 1_000_000}, AT) == Decimal("10")
    assert p.price_tokens("m", {"input": 1_000_000, "output": 5}, AT) is None


def test_retired_model_is_none_and_all_none_rates(tmp_path):
    p = make_pricer(tmp_path, [row("m", date(2026, 1, 1), per_million={"input": 10},
                                   retired_on=date(2026, 6, 1))])
    assert p.price_tokens("m", {"input": 5}, date(2026, 5, 1)) is not None
    assert p.price_tokens("m", {"input": 5}, date(2026, 7, 1)) is None
    assert p.rates("m", date(2026, 7, 1)) == {k: None for k in
                                              ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")}
    assert "m" in p.models(date(2026, 5, 1))
    assert "m" not in p.models(date(2026, 7, 1))


def test_ambiguous_model_is_none(tmp_path):
    rows = [row("claude-x-20250101", date(2026, 1, 1), per_million={"input": 1}),
            row("claude-x-20250202", date(2026, 1, 1), per_million={"input": 2})]
    p = make_pricer(tmp_path, rows)
    assert p.price_tokens("claude-x", {"input": 5}, AT) is None


def test_models_excludes_other_vendors_and_rowless_channels(tmp_path):
    rows = [row("claude-a", date(2026, 1, 1), per_million={"input": 1}),
            row("gpt-z", date(2026, 1, 1), channel="openai", per_million={"input": 1})]
    p = make_pricer(tmp_path, rows, models={"claude-a": "anthropic", "gpt-z": "openai"})
    assert set(p.models(AT)) == {"claude-a"}


def test_channel_config_selects_org_channel(tmp_path):
    ch = "aws_bedrock:us-east-1:global"
    rows = [row("claude-a", date(2026, 1, 1), per_million={"input": 1}),
            row("claude-a", date(2026, 1, 1), channel=ch, per_million={"input": 2})]
    cc = f"orgs:\n  acme:\n    anthropic: {ch}\n"
    acme = make_pricer(tmp_path / "a", rows, org_id="acme", channel_config=cc)
    other = make_pricer(tmp_path / "b", rows, org_id="other", channel_config=cc)
    assert acme.price_tokens("claude-a", {"input": 1_000_000}, AT) == Decimal("2")
    assert other.price_tokens("claude-a", {"input": 1_000_000}, AT) == Decimal("1")


# ------------------------------------------------------------------ cache split

def test_cache_write_detail_drives_the_split(real):
    detail = [{"ttl_s": 300, "tokens": 1_000_000}, {"ttl_s": 3600, "tokens": 1_000_000}]
    assert real.price_tokens("claude-opus-5", {}, AT, cache_write_detail=detail) == Decimal("16.25")


def test_combined_cache_write_charged_at_1h(real):
    combined = real.price_tokens("claude-opus-5", {"cache_write": 1_000_000}, AT)
    assert combined == Decimal("10.00")
    assert combined == real.price_tokens("claude-opus-5", {"cache_write_1h": 1_000_000}, AT)


def test_split_cache_write_shapes():
    assert split_cache_write({"cache_write": 10})["cache_write_1h"] == 10
    both = split_cache_write({}, [{"ttl_s": 300, "tokens": 3}, {"ttl_s": 3600, "tokens": 7}])
    assert (both["cache_write_5m"], both["cache_write_1h"]) == (3, 7)


# ------------------------------------------------------------------ plumbing

def test_at_accepts_datetime_date_iso_with_z(real):
    t = {"input": 1_000_000}
    for at in (AT, date(2026, 10, 1), "2026-10-01T12:00:00Z", "2026-10-01"):
        assert real.price_tokens("claude-opus-5", t, at) == Decimal("5.00")


def test_at_none_raises(real):
    with pytest.raises(ValueError):
        real.price_tokens("claude-opus-5", {"input": 1}, None)


def test_rates_are_exact_decimals(real):
    assert isinstance(real.rates("claude-opus-5", AT)["input"], Decimal)
    assert real.price_tokens("claude-opus-5", {"cache_read": 3}, AT) == Decimal("0.0000015")


def test_micros_conversion_rounds_half_even():
    assert price_to_micros(Decimal("1.0000005")) == 1000000
    assert price_to_micros(Decimal("1.0000015")) == 1000002
    assert price_to_micros(None) is None


# ------------------------------------------------------------------ history

HIST = """schema_version: 1
rows:
  - channel: anthropic
    model_id: m
    start_date: 2026-01-01
    input: 3.0
    output: 15
"""


def _hist_pricer(tmp_path, hist=HIST):
    return make_pricer(tmp_path, [row("m", date(2026, 3, 1), per_million={"input": 2, "output": 10})],
                       history_yaml=hist)


def test_history_wins_before_catalog_and_catalog_from_first_row(tmp_path):
    p = _hist_pricer(tmp_path)
    t = {"input": 1_000_000, "output": 1_000_000}
    assert p.price_tokens("m", t, date(2026, 2, 28)) == Decimal("18")
    assert p.price_tokens("m", t, date(2026, 3, 1)) == Decimal("12")
    assert p.price_tokens("m", t, date(2026, 6, 1)) == Decimal("12")
    # before the history's first row: catalog backward rule (first row)
    assert p.price_tokens("m", t, date(2025, 6, 1)) == Decimal("12")


def test_history_missing_rate_is_none_not_catalog_fallthrough(tmp_path):
    p = _hist_pricer(tmp_path)
    assert p.price_tokens("m", {"cache_read": 5}, date(2026, 2, 1)) is None


def test_history_unknown_registry_model_is_config_error(tmp_path):
    with pytest.raises(ConfigError):
        _hist_pricer(tmp_path, HIST.replace("model_id: m", "model_id: nope"))


def test_history_duplicate_and_extra_keys_rejected(tmp_path):
    dup = HIST + HIST.split("rows:\n")[1]
    with pytest.raises(ConfigError):
        _hist_pricer(tmp_path, dup)
    with pytest.raises(ConfigError):
        _hist_pricer(tmp_path / "x", HIST + "    bogus: 1\n")


def test_fingerprint_catalog_version_and_history_hash(tmp_path):
    base = make_pricer(tmp_path / "a", [row("m", date(2026, 3, 1), per_million={"input": 2})])
    assert "+h" not in base.fingerprint
    with_h = _hist_pricer(tmp_path / "b")
    assert "+h" in with_h.fingerprint
    # 3.0 and 3 hash alike
    same = _hist_pricer(tmp_path / "c", HIST.replace("input: 3.0", "input: 3"))
    assert same.fingerprint.split("+h")[1] == with_h.fingerprint.split("+h")[1]
    changed = _hist_pricer(tmp_path / "d", HIST.replace("input: 3.0", "input: 4"))
    assert changed.fingerprint != with_h.fingerprint


def test_different_catalogs_different_fingerprints(tmp_path, real):
    thin = make_pricer(tmp_path / "t", [row("m", date(2026, 3, 1), per_million={"input": 2})])
    tenx = make_pricer(tmp_path / "x", [row("m", date(2026, 3, 1), per_million={"input": 20})])
    assert thin.fingerprint != real.fingerprint
    assert thin.fingerprint != tenx.fingerprint


# ------------------------------------------------------------------ effective_from

def test_effective_from_is_the_start_date_of_the_row_in_effect(tmp_path):
    p = _hist_pricer(tmp_path)
    assert p.effective_from("m", date(2026, 2, 1)) == date(2026, 1, 1)    # history row
    assert p.effective_from("m", date(2026, 3, 1)) == date(2026, 3, 1)    # catalog row
    assert p.effective_from("m", date(2025, 1, 1)) == date(2026, 3, 1)    # earliest-row rule
    assert p.effective_from("nope", date(2026, 3, 1)) is None
    assert p.effective_from(None, date(2026, 3, 1)) is None


def test_effective_from_real_catalog(real):
    assert real.effective_from("claude-haiku-4-5-20251001", AT) == date(2026, 9, 25)


def test_resolve_exposes_the_match(real):
    m = real.resolve("claude-haiku-4-5-20251001")
    assert (m.status, m.model_id, m.channel) == ("matched", "claude-haiku-4-5", "anthropic")
    assert real.resolve("no-such-model").status == "unmatched"
    assert real.resolve(None).status == "unmatched"
