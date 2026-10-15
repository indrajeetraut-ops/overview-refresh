"""Test helper: tiny catalogs (index + version files) and channel config in `tmp_path`,
so tests can build custom price tables for `Pricer` (#68)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from common.model_pricing.catalog import CatalogVersion, RateRow, RegistryEntry
from common.model_pricing.pricer import HistoryRow, Pricer


def pm(x) -> str:
    """USD per million -> a per-token decimal string (RateRow parses via Decimal(str()))."""
    from decimal import Decimal
    return format(Decimal(str(x)) / Decimal(1_000_000), "f")


def row(model_id: str, start: date, *, channel: str = "anthropic", per_million: dict | None = None,
        retired_on: date | None = None) -> RateRow:
    """A catalog row; `per_million` maps token type -> USD per million (omitted = None)."""
    rates = {k: pm(v) for k, v in (per_million or {}).items()}
    return RateRow(channel=channel, model_id=model_id, start_date=start, retired_on=retired_on,
                   source="test", source_key=model_id, **rates)


def write_version(root: Path, built_on: date, rates: list[RateRow],
                  models: dict[str, str] | None = None) -> str:
    """Write one version file with a real hash8; `models` maps model_id -> vendor
    (default: every rate row's model_id, vendor anthropic)."""
    if models is None:
        models = {r.model_id: "anthropic" for r in rates}
    registry = {m: RegistryEntry(vendor=v, names_seen=[m]) for m, v in models.items()}
    stub = CatalogVersion(version="pending", built_on=built_on, models=registry, rates=rates)
    version = f"{built_on.isoformat()}-{stub.compute_hash8()}"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{version}.json").write_text(stub.model_copy(update={"version": version}).model_dump_json())
    return version


def write_index(root: Path, versions: list[str]) -> None:
    (root / "index.json").write_text(json.dumps({"versions": versions}))


def make_catalog(tmp_path: Path, rates: list[RateRow], *, built_on: date = date(2026, 1, 1),
                 models: dict[str, str] | None = None, channel_config: str | None = None
                 ) -> tuple[Path, Path, str]:
    """Single-version catalog. Returns `(catalog_root, config_root, version)`; `config_root`
    holds `channel_config.yaml` when given (absent = vendor channels)."""
    root = tmp_path / "catalog"
    config_root = tmp_path / "config"
    config_root.mkdir(parents=True, exist_ok=True)
    version = write_version(root, built_on, rates, models)
    write_index(root, [version])
    if channel_config is not None:
        (config_root / "channel_config.yaml").write_text(channel_config)
    return root, config_root, version


def make_pricer(tmp_path: Path, rates: list[RateRow], *, org_id: str | None = None,
                history_yaml: str | None = None, **kw) -> Pricer:
    root, config_root, _ = make_catalog(tmp_path, rates, **kw)
    history_path = tmp_path / "history.yaml"
    if history_yaml is not None:
        history_path.write_text(history_yaml)
    return Pricer.load(org_id, None, root=root, config_root=config_root, history_path=history_path)
