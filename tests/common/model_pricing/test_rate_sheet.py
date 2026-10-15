"""`rate_sheet()` (model-pricing design §4): segment building on top of the existing
`load_catalog` -> `for_org` -> `resolve`/`rates` lookup path.

Catalogs are built in `tmp_path` the same way `test_catalog.py` does -- `CatalogVersion`
objects constructed directly, written with a real `hash8`, plus an `index.json` -- no
network, no shared fixtures with the source-adapter tests.
"""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from common.model_pricing.catalog import (
    CatalogVersion,
    RateRow,
    RegistryEntry,
    load_catalog,
)
from common.model_pricing.decimals import per_million_to_per_token as pm
from common.model_pricing.rate_sheet import RateSheet, rate_sheet


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
    stub = CatalogVersion(version="pending", built_on=built_on, models=models or {}, rates=rates)
    version = f"{built_on.isoformat()}-{stub.compute_hash8()}"
    cv = stub.model_copy(update={"version": version})
    catalog_root.mkdir(parents=True, exist_ok=True)
    (catalog_root / f"{version}.json").write_text(cv.model_dump_json())
    return version


def _write_index(catalog_root: Path, versions: list[str]) -> None:
    (catalog_root / "index.json").write_text(json.dumps({"versions": versions}))


# ================================================================== fixtures


@pytest.fixture
def price_change_catalog(tmp_path):
    """One model, one price change mid-range: v1 built 2026-09-01 at $2/$10 per M, v2
    built 2026-10-15 at $3/$15 per M -- a `rate_sheet` over October must split into two
    segments at the v2 boundary."""
    catalog_root = tmp_path / "catalog"
    config_root = tmp_path / "config"

    v1_date = date(2026, 9, 1)
    v2_date = date(2026, 10, 15)

    v1 = _write_version(
        catalog_root,
        v1_date,
        rates=[_row("anthropic", "claude-x", v1_date, input=pm(2), output=pm(10))],
        models={"claude-x": RegistryEntry(vendor="anthropic", names_seen=["claude-x"])},
    )
    v2 = _write_version(
        catalog_root,
        v2_date,
        rates=[_row("anthropic", "claude-x", v2_date, input=pm(3), output=pm(15))],
    )
    _write_index(catalog_root, [v1, v2])
    catalog = load_catalog(root=catalog_root, config_root=config_root)
    return catalog


@pytest.fixture
def retired_catalog(tmp_path):
    """One model retired mid-range: priced from 2026-09-01, `retired_on` 2026-10-15 --
    a `rate_sheet` over October must show a priced segment then a null segment."""
    catalog_root = tmp_path / "catalog"
    config_root = tmp_path / "config"
    built_on = date(2026, 9, 1)

    version = _write_version(
        catalog_root,
        built_on,
        rates=[
            _row(
                "anthropic", "claude-sunset", built_on,
                input=pm(1), output=pm(5), retired_on=date(2026, 10, 15),
            )
        ],
        models={"claude-sunset": RegistryEntry(vendor="anthropic", names_seen=["claude-sunset"])},
    )
    _write_index(catalog_root, [version])
    return load_catalog(root=catalog_root, config_root=config_root)


@pytest.fixture
def ambiguous_catalog(tmp_path):
    """`gpt-4o` family: `gpt-4o`, `gpt-4o-2024-05-13` (different rates), `gpt-4o-2024-08-06`
    share canon `gpt-4o` with diverging rates -> ambiguous, same real case the design
    (§5.2) and `test_normalize.py` cite."""
    catalog_root = tmp_path / "catalog"
    config_root = tmp_path / "config"
    built_on = date(2026, 9, 1)

    version = _write_version(
        catalog_root,
        built_on,
        rates=[
            _row("openai", "gpt-4o", built_on, input=pm(5), output=pm(15)),
            _row("openai", "gpt-4o-2024-05-13", built_on, input=pm(5), output=pm(15)),
            _row("openai", "gpt-4o-2024-08-06", built_on, input=pm(3), output=pm(10)),
        ],
        models={
            "gpt-4o": RegistryEntry(vendor="openai", names_seen=["gpt-4o"]),
            "gpt-4o-2024-05-13": RegistryEntry(vendor="openai", names_seen=["gpt-4o-2024-05-13"]),
            "gpt-4o-2024-08-06": RegistryEntry(vendor="openai", names_seen=["gpt-4o-2024-08-06"]),
        },
    )
    _write_index(catalog_root, [version])
    return load_catalog(root=catalog_root, config_root=config_root)


# ================================================================== segments


def test_price_change_mid_range_splits_into_two_segments(price_change_catalog):
    sheet = rate_sheet(
        "acme", ["claude-x"], date(2026, 10, 1), date(2026, 10, 31), catalog=price_change_catalog
    )
    entry = sheet.models[0]
    assert entry.status == "matched"
    assert entry.model_id == "claude-x"
    segments = entry.segments
    assert len(segments) == 2

    assert segments[0].from_ == date(2026, 10, 1)
    assert segments[0].to == date(2026, 10, 14)
    assert segments[0].rates["input"] == "0.000002"
    assert segments[0].rates["output"] == "0.00001"

    assert segments[1].from_ == date(2026, 10, 15)
    assert segments[1].to == date(2026, 10, 31)
    assert segments[1].rates["input"] == "0.000003"
    assert segments[1].rates["output"] == "0.000015"


def test_retired_mid_range_produces_null_segment(retired_catalog):
    sheet = rate_sheet(
        "acme", ["claude-sunset"], date(2026, 10, 1), date(2026, 10, 31), catalog=retired_catalog
    )
    entry = sheet.models[0]
    assert entry.status == "matched"
    segments = entry.segments
    assert len(segments) == 2

    assert segments[0].from_ == date(2026, 10, 1)
    assert segments[0].to == date(2026, 10, 15)  # not yet past retired_on
    assert segments[0].rates["input"] == "0.000001"

    assert segments[1].from_ == date(2026, 10, 16)
    assert segments[1].to == date(2026, 10, 31)
    assert all(v is None for v in segments[1].rates.values())


def test_single_price_whole_range_is_one_segment(price_change_catalog):
    # Range entirely before the v2 price change -> one segment, not two.
    sheet = rate_sheet(
        "acme", ["claude-x"], date(2026, 9, 1), date(2026, 9, 30), catalog=price_change_catalog
    )
    entry = sheet.models[0]
    assert len(entry.segments) == 1
    assert entry.segments[0].from_ == date(2026, 9, 1)
    assert entry.segments[0].to == date(2026, 9, 30)


def test_segments_cover_range_inclusive_no_gaps(price_change_catalog):
    sheet = rate_sheet(
        "acme", ["claude-x"], date(2026, 10, 1), date(2026, 10, 31), catalog=price_change_catalog
    )
    segments = sheet.models[0].segments
    assert segments[0].from_ == date(2026, 10, 1)
    assert segments[-1].to == date(2026, 10, 31)
    for prev, nxt in zip(segments, segments[1:]):
        assert (nxt.from_ - prev.to).days == 1  # no gap, no overlap


# ================================================================== non-matched


def test_ambiguous_model_carries_candidates_no_segments(ambiguous_catalog):
    sheet = rate_sheet(
        "acme", ["gpt-4o-2025-06-01"], date(2026, 10, 1), date(2026, 10, 31),
        catalog=ambiguous_catalog,
    )
    entry = sheet.models[0]
    assert entry.status == "ambiguous"
    assert entry.segments is None
    assert entry.reason is not None
    assert set(entry.candidates) == {"gpt-4o", "gpt-4o-2024-05-13", "gpt-4o-2024-08-06"}
    assert entry.model_id is None
    assert entry.vendor is None
    assert entry.channel is None
    assert entry.match_step is None


def test_unmatched_model_has_empty_candidates_no_segments(ambiguous_catalog):
    sheet = rate_sheet(
        "acme", ["totally-unknown-model"], date(2026, 10, 1), date(2026, 10, 31),
        catalog=ambiguous_catalog,
    )
    entry = sheet.models[0]
    assert entry.status == "unmatched"
    assert entry.segments is None
    assert entry.candidates == []
    assert entry.reason is not None


# ================================================================== JSON round-trip


def test_json_round_trip_matches_design_example_shape(price_change_catalog, ambiguous_catalog):
    """One matched, one ambiguous model in the same sheet; the JSON dump follows §4's
    example field order and omits fields the other status doesn't use."""
    sheet = rate_sheet(
        "acme", ["claude-x"], date(2026, 10, 1), date(2026, 10, 14), catalog=price_change_catalog
    )
    raw = json.loads(sheet.to_json())

    assert list(raw.keys()) == ["org_id", "start", "end", "price_version", "models"]
    assert raw["org_id"] == "acme"
    assert raw["start"] == "2026-10-01"
    assert raw["end"] == "2026-10-14"
    assert raw["price_version"] == price_change_catalog.version

    matched = raw["models"][0]
    assert list(matched.keys()) == [
        "usage_model_id", "status", "model_id", "vendor", "channel", "match_step", "segments",
    ]
    assert matched["status"] == "matched"
    assert matched["segments"][0]["from"] == "2026-10-01"
    assert matched["segments"][0]["to"] == "2026-10-14"
    assert matched["segments"][0]["rates"] == {
        "input": "0.000002",
        "output": "0.00001",
        "cache_read": None,
        "cache_write_5m": None,
        "cache_write_1h": None,
    }

    ambiguous_sheet = rate_sheet(
        "acme", ["gpt-4o-2025-06-01"], date(2026, 10, 1), date(2026, 10, 14),
        catalog=ambiguous_catalog,
    )
    raw2 = json.loads(ambiguous_sheet.to_json())
    entry = raw2["models"][0]
    assert list(entry.keys()) == ["usage_model_id", "status", "reason", "candidates"]
    assert entry["status"] == "ambiguous"
    assert isinstance(entry["candidates"], list) and entry["candidates"]

    # Round-trips back into an equivalent RateSheet.
    rebuilt = RateSheet.model_validate(raw)
    assert rebuilt == sheet


# ================================================================== errors


def test_start_after_end_raises(price_change_catalog):
    with pytest.raises(ValueError):
        rate_sheet(
            "acme", ["claude-x"], date(2026, 10, 31), date(2026, 10, 1),
            catalog=price_change_catalog,
        )


def test_default_roots_do_not_depend_on_cwd():
    from common.model_pricing.catalog import DEFAULT_CATALOG_ROOT
    from common.model_pricing.config import DEFAULT_ROOT

    assert DEFAULT_ROOT.is_absolute()
    assert DEFAULT_CATALOG_ROOT == DEFAULT_ROOT / "catalog"
    assert (DEFAULT_ROOT.parent.parent / "src" / "common" / "model_pricing").is_dir()
