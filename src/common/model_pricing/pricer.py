"""`Pricer`: the legacy `price_tokens` call shapes over the model_pricing catalog (#68 design
§2.2, §3.2).

A `Pricer` is bound to one catalog version and one org. It resolves an observed model string
(`OrgPrices.resolve`), looks the dated rate up (`OrgPrices.rate`, with curated `history.yaml`
rows beneath the catalog) and sums tokens x rate. Unknown / ambiguous / retired models and
missing rates return `None`, never 0 and never a partial total.

    from common.model_pricing import Pricer
    pricer = Pricer.load(org_id="talium.ai", version=None)
    pricer.price_tokens("claude-sonnet-5", {"input": 1_000_000}, "2026-10-01")  # Decimal
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from common.model_pricing.catalog import (
    DEFAULT_CATALOG_ROOT,
    TOKEN_TYPES,
    MatchResult,
    OrgPrices,
    load_catalog,
)
from common.model_pricing.config import DEFAULT_ROOT as DEFAULT_CONFIG_ROOT
from common.model_pricing.errors import ConfigError
from common.msging.log import get_logger

log = get_logger("common.model_pricing.pricer")

#: Rates in `history.yaml` are quoted per million tokens (as the legacy file was); the
#: catalog stores per token.
TOKENS_PER_UNIT = Decimal(1_000_000)

DEFAULT_HISTORY_PATH = DEFAULT_CONFIG_ROOT / "history.yaml"

_HISTORY_FIELDS = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")


class HistoryRow(BaseModel):
    """One curated dated rate row for a date range the catalog does not cover. Rates are
    held per token (the file's per-million values are divided on load)."""

    model_config = ConfigDict(extra="forbid")

    channel: str
    model_id: str
    start_date: date
    input: Decimal | None = None
    output: Decimal | None = None
    cache_read: Decimal | None = None
    cache_write_5m: Decimal | None = None
    cache_write_1h: Decimal | None = None

    @field_validator(*_HISTORY_FIELDS, mode="before")
    @classmethod
    def _exact(cls, v: Any) -> Decimal | None:
        # str() first: YAML parses 5.00 as a float.
        return None if v is None else Decimal(str(v))

    def rate(self, token_type: str) -> Decimal | None:
        return getattr(self, token_type)

    def hash_fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "channel": self.channel,
            "model_id": self.model_id,
            "start_date": self.start_date.isoformat(),
        }
        for tt in TOKEN_TYPES:
            v = self.rate(tt)
            out[tt] = None if v is None else format(v.normalize(), "f")
        return out


class _HistoryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    rows: list[HistoryRow] = Field(default_factory=list)


def load_history(path: str | Path = DEFAULT_HISTORY_PATH) -> list[HistoryRow]:
    """Rows of `history.yaml`, sorted by date, rates converted to per token. Absent file ->
    empty. Invalid file or duplicate `(channel, model_id, start_date)` -> `ConfigError`."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        parsed = _HistoryFile.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    rows: list[HistoryRow] = []
    seen: set[tuple[str, str, date]] = set()
    for row in parsed.rows:
        key = (row.channel, row.model_id, row.start_date)
        if key in seen:
            raise ConfigError(f"{path}: duplicate history row {key}")
        seen.add(key)
        per_token = row.model_copy(
            update={
                tt: (None if row.rate(tt) is None else row.rate(tt) / TOKENS_PER_UNIT)
                for tt in TOKEN_TYPES
            }
        )
        rows.append(per_token)
    rows.sort(key=lambda r: (r.start_date, r.channel, r.model_id))
    log.info("history_loaded", path=str(path), rows=len(rows))
    return rows


def _history_hash(history: list[HistoryRow]) -> str:
    rows = sorted(
        (r.hash_fields() for r in history),
        key=lambda r: (r["channel"], r["model_id"], r["start_date"]),
    )
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:8]


def as_date(at: date | datetime | str | None) -> date:
    if at is None:
        raise ValueError("price_tokens: `at` is required -- prices are dated")
    if isinstance(at, datetime):
        return at.date()
    if isinstance(at, str):
        return datetime.fromisoformat(at.replace("Z", "+00:00")).date()
    return at


def split_cache_write(tokens: dict, cache_write_detail: list | None = None) -> dict:
    """Resolve `cache_write` into its 5m/1h parts.

    Three cases, in order of how much the caller actually knows:

    1. `cache_write_detail` present (JSONL path) -- the vendor told us the split; use it.
    2. `cache_write_5m` / `cache_write_1h` given directly -- caller already resolved it.
    3. Only a combined `cache_write` (the OTel path -- `api_request` reports one number) --
       charged at the **1-hour** rate. Deliberately the more expensive of the two: an
       estimate that overstates is a number someone questions, one that understates is a
       number someone budgets against.
    """
    out = {k: Decimal(tokens.get(k) or 0) for k in ("input", "output", "cache_read")}
    out["cache_write_5m"] = Decimal(tokens.get("cache_write_5m") or 0)
    out["cache_write_1h"] = Decimal(tokens.get("cache_write_1h") or 0)

    if cache_write_detail:
        for entry in cache_write_detail:
            ttl = entry.get("ttl_s")
            amount = Decimal(entry.get("tokens") or 0)
            out["cache_write_1h" if ttl and int(ttl) >= 3600 else "cache_write_5m"] += amount
        return out

    combined = tokens.get("cache_write")
    if combined:
        out["cache_write_1h"] += Decimal(combined)
    return out


def price_to_micros(price: Decimal | None) -> int | None:
    """USD -> integer micros, the storage unit. Money never rides a float."""
    if price is None:
        return None
    return int((price * TOKENS_PER_UNIT).to_integral_value(rounding="ROUND_HALF_EVEN"))


class Pricer:
    """Version-bound, org-bound pricing handle (design §2.1)."""

    def __init__(self, prices: OrgPrices, history: list[HistoryRow], version: str):
        self._prices = prices
        self._history = sorted(history, key=lambda r: r.start_date)
        self._history_by_key: dict[tuple[str, str], list[HistoryRow]] = {}
        for r in self._history:
            self._history_by_key.setdefault((r.channel, r.model_id), []).append(r)
        self._version = version
        self._fingerprint = version + (
            "+h" + _history_hash(self._history) if self._history else ""
        )

    @classmethod
    def load(
        cls,
        org_id: str | None = None,
        version: str | None = None,
        *,
        root: str | Path = DEFAULT_CATALOG_ROOT,
        config_root: str | Path = DEFAULT_CONFIG_ROOT,
        history_path: str | Path | None = None,
    ) -> "Pricer":
        catalog = load_catalog(version, root, config_root)
        prices = catalog.for_org(org_id or "")
        history = load_history(DEFAULT_HISTORY_PATH if history_path is None else history_path)
        for r in history:
            if r.model_id not in catalog.registry.models:
                raise ConfigError(
                    f"history.yaml: model_id {r.model_id!r} is not a catalog registry id"
                )
        return cls(prices, history, catalog.version)

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def _rate_on(self, match: MatchResult, token_type: str, day: date) -> Decimal | None:
        """History row only for days before the key's first catalog row; otherwise the
        catalog's forward / backward / retired rules."""
        key = (match.channel, match.model_id)
        hist = self._history_by_key.get(key)
        if hist:
            catalog_rows = self._prices.rows_by_key.get(key)
            if catalog_rows and day < catalog_rows[0].start_date:
                eligible = [r for r in hist if r.start_date <= day]
                if eligible:
                    return eligible[-1].rate(token_type)
        return self._prices.rate(match, token_type, day)

    def resolve(self, model: str | None) -> MatchResult:
        """The org's `MatchResult` for an observed model string (status, registry `model_id`,
        channel). Empty / None resolves as unmatched. Diagnostics use this to say *why* a
        price moved (e.g. a dated id now normalising to a registry id)."""
        if not model:
            return self._prices.resolve("")
        return self._prices.resolve(model)

    def effective_from(self, model: str | None, at: date | datetime | str) -> date | None:
        """`start_date` of the row (history or catalog) that prices `model` on `at`; None if
        the model does not resolve. Before the key's first row the earliest row applies
        (design §4 #7), so this is that row's date."""
        day = as_date(at)
        match = self._match(model)
        if match is None:
            return None
        key = (match.channel, match.model_id)
        rows = self._prices.rows_by_key.get(key)
        if not rows:
            return None
        hist = self._history_by_key.get(key)
        if hist and day < rows[0].start_date:
            eligible = [r for r in hist if r.start_date <= day]
            if eligible:
                return eligible[-1].start_date
        candidates = [r for r in rows if r.start_date <= day]
        return (candidates[-1] if candidates else rows[0]).start_date

    def _match(self, model: str | None) -> MatchResult | None:
        if not model:
            return None
        match = self._prices.resolve(model)
        return match if match.status == "matched" else None

    def price_tokens(
        self,
        model: str | None,
        tokens: dict,
        at: date | datetime | str,
        cache_write_detail: list | None = None,
    ) -> Decimal | None:
        """USD for one request's tokens, or None if the model has no price on that date."""
        day = as_date(at)
        counts = split_cache_write(tokens, cache_write_detail)
        match = self._match(model)
        if match is None:
            return None
        total = Decimal(0)
        for tt in TOKEN_TYPES:
            count = counts.get(tt, Decimal(0))
            if not count:
                continue
            rate = self._rate_on(match, tt, day)
            if rate is None:
                return None
            total += count * rate
        return total

    def rates(self, model: str | None, at: date | datetime | str) -> dict[str, Decimal | None] | None:
        """USD per token by token type, or None if the model does not resolve."""
        day = as_date(at)
        match = self._match(model)
        if match is None:
            return None
        return {tt: self._rate_on(match, tt, day) for tt in TOKEN_TYPES}

    def models(self, at: date | datetime | str) -> dict[str, dict[str, Decimal | None]]:
        """`{model_id: rates}` for anthropic-vendor models on the org's resolved channel,
        registry ids; models retired on `at` (all rates None) are omitted."""
        day = as_date(at)
        registry = self._prices.registry
        out: dict[str, dict[str, Decimal | None]] = {}
        for model_id in registry.models:
            if registry.vendor_of(model_id) != "anthropic":
                continue
            match = self._prices.resolve(model_id)
            if match.status != "matched" or match.model_id != model_id:
                continue
            rates = {tt: self._rate_on(match, tt, day) for tt in TOKEN_TYPES}
            if all(v is None for v in rates.values()):
                continue
            out[model_id] = rates
        return out
