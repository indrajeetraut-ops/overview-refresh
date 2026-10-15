"""A snapshot-bound wrapper over `common.model_pricing.Pricer`, for Layer 2 (plan §5 P2).

`OrgPricer` wraps a `common.model_pricing.Pricer` bound to one catalog version. The org
dataset is self-contained: `pricing_files()` lists exactly the catalog files (the bound
version and every earlier one, with a trimmed `index.json`), `history.yaml` and the optional
`channel_config.yaml` / `aliases.yaml` that reproduce its prices, `cli_synth_org.py` copies
them under `<output>/pricing/` and `load_pricer()` loads from there (`pricing_dir`) -- a
version name alone never reproduces a dataset, and a later catalog update never reprices an
already-generated one.

Two names in the real `graph_fold.py` this ports from: `_price_channel(model_tokens, kind,
pricing, at) -> int | None` (one channel, sums each contributing model's Decimal price, rounds
to micros once) and `channel_cost_of(models, pricing, at) -> dict` (the public 3-channel
wrapper over it). Plan §5 P2 names its own port `channel_micros(models, at) -> dict` -- that
signature actually matches `channel_cost_of`, not `_price_channel` (the plan's own text
mis-attributes it), so `channel_micros` here is that dict-returning port, and `_price_channel`
below keeps the real function's own name for the single-channel helper it calls internally.
`test_parity_graph_fold.py` checks `channel_micros` against `graph_fold.channel_cost_of` byte
for byte.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import json

from common.model_pricing import Pricer, price_to_micros
from common.model_pricing.catalog import DEFAULT_CATALOG_ROOT
from common.model_pricing.config import DEFAULT_ROOT as DEFAULT_CONFIG_ROOT
from common.model_pricing.pricer import DEFAULT_HISTORY_PATH

from ..errors import ConfigError

#: Config files copied beside the catalog when present (absent = vendor channels / no aliases).
_OPTIONAL_CONFIG = ("channel_config.yaml", "aliases.yaml")


def load_pricer(version: str | None = None, org: str | None = None,
                pricing_dir: Path | None = None) -> Pricer:
    """The `common.model_pricing.Pricer` for an org config's pricing fields. `pricing_dir`
    None = the live catalog and config; set = a dataset's own `pricing/` snapshot (catalog in
    `catalog/`, `history.yaml` and the optional config files beside it) and nothing else is
    read. Org id `""` (vendor channels) unless `org` is set."""
    if pricing_dir is None:
        return Pricer.load(org, version)
    pricing_dir = Path(pricing_dir)
    return Pricer.load(org, version, root=pricing_dir / "catalog", config_root=pricing_dir,
                       history_path=pricing_dir / "history.yaml")


def pricing_files(version: str | None = None,
                  pricing_dir: Path | None = None) -> tuple[str, list[tuple[str, bytes]]]:
    """`(resolved_version, [(relative_path, content)])`: everything needed to reproduce the
    prices -- the bound catalog version and every earlier one in index order, a trimmed
    `index.json` listing exactly those, `history.yaml` (empty when absent) and the optional
    `channel_config.yaml` / `aliases.yaml`. Read from the live locations, or from
    `pricing_dir` when pricing from an existing snapshot."""
    if pricing_dir is None:
        root, config_root, history = DEFAULT_CATALOG_ROOT, DEFAULT_CONFIG_ROOT, DEFAULT_HISTORY_PATH
    else:
        root, config_root, history = Path(pricing_dir) / "catalog", Path(pricing_dir), Path(pricing_dir) / "history.yaml"
    try:
        versions = json.loads((root / "index.json").read_text(encoding="utf-8"))["versions"]
    except (OSError, ValueError, KeyError) as exc:
        raise ConfigError(f"pricing catalog unreadable at {root}: {exc}") from exc
    bound = version if version is not None else versions[-1]
    if bound not in versions:
        raise ConfigError(f"pricing_version {bound!r} is not in the catalog (have: {', '.join(versions)})")
    keep = versions[: versions.index(bound) + 1]
    files: list[tuple[str, bytes]] = [
        ("catalog/index.json", json.dumps({"versions": keep}, indent=2).encode() + b"\n")]
    for v in keep:
        files.append((f"catalog/{v}.json", (root / f"{v}.json").read_bytes()))
    files.append(("history.yaml", history.read_bytes() if history.is_file()
                  else b"schema_version: 1\nrows: []\n"))
    for name in _OPTIONAL_CONFIG:
        if (config_root / name).is_file():
            files.append((name, (config_root / name).read_bytes()))
    return bound, files


class OrgPricer:
    def __init__(self, pricing: Pricer):
        self._pricing = pricing

    @property
    def pricer(self) -> Pricer:
        return self._pricing

    def price_micros(self, model: str, tokens: dict, at: date | datetime) -> int:
        price = self._pricing.price_tokens(model, tokens, at)
        if price is None:
            raise ConfigError(f"model {model!r} has no price at {at}")
        return price_to_micros(price)

    def request_micros(self, model: str, tokens: dict, at: date | datetime,
                       cache_write_detail: list | None = None) -> int:
        """One request's price in integer micros, over `Pricer.price_tokens`. With
        `cache_write_detail` ([{ttl_s, tokens}, ...], as real payloads carry it) the writes
        are priced per lifetime and a combined `cache_write` in `tokens` is not added again."""
        price = self._pricing.price_tokens(model, tokens, at, cache_write_detail)
        if price is None:
            raise ConfigError(f"model {model!r} has no price at {at}")
        return price_to_micros(price)

    def _price_channel(self, models: dict[str, dict[str, int]], kind: str,
                       at: date | datetime) -> int | None:
        """Ports `graph_fold._price_channel(model_tokens, kind, pricing, at)` verbatim: `None`
        the instant any contributing model can't be priced for this kind/date, never a partial
        sum. Single-channel helper -- callers outside this module that need one channel's
        price (e.g. `content.py`'s carry/resume-cost computations, which mirror
        `graph_fold.py`'s own internal calls to this same helper) call it directly; the leading
        underscore matches the real function's own name, not an access restriction."""
        total = Decimal(0)
        for model, bucket in models.items():
            count = bucket.get(kind, 0)
            if not count:
                continue
            price = self._pricing.price_tokens(model, {kind: count}, at)
            if price is None:
                return None
            total += price
        return price_to_micros(total)

    def channel_micros(self, models: dict[str, dict[str, int]], at: date | datetime) -> dict:
        """Ports `graph_fold.channel_cost_of` exactly (D9: recache == cache_write; plan §5 P2's
        own `channel_micros(models: dict, at) -> dict` signature)."""
        return {
            "input": {"tokens": sum(m["input"] for m in models.values()),
                      "usd_micros": self._price_channel(models, "input", at)},
            "output": {"tokens": sum(m["output"] for m in models.values()),
                       "usd_micros": self._price_channel(models, "output", at)},
            "recache": {"tokens": sum(m["cache_write"] for m in models.values()),
                        "usd_micros": self._price_channel(models, "cache_write", at)},
        }

    def rewrite_recoverable_usd_micros(self, model: str, rewrite_tokens: int, context_before: int,
                                       cache_read_tokens: int, at: date | datetime) -> int | None:
        """Ports `graph_fold._rewrite_recoverable_usd_micros` verbatim (efficiency note §2,
        buckets 1-2): only context that was already cached and is being paid for again counts,
        priced as the difference between writing and reading those tokens."""
        rewritten = min(rewrite_tokens, max(0, context_before - cache_read_tokens))
        write = self._pricing.price_tokens(model, {"cache_write": rewritten}, at)
        read = self._pricing.price_tokens(model, {"cache_read": rewritten}, at)
        if write is None or read is None:
            return None
        return price_to_micros(max(Decimal(0), write - read))

    def cost_micros(self, models: dict[str, dict[str, int]], at: date | datetime) -> int:
        total = Decimal(0)
        for model, tokens in models.items():
            price = self._pricing.price_tokens(model, tokens, at)
            if price is None:
                raise ConfigError(f"model {model!r} has no price at {at}")
            total += price
        return price_to_micros(total)
