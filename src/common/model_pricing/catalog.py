"""Catalog schema, reading, and lookup (design §3, §7, §9).

This module owns the **shape** of a rate row and of a version file (§3.1, §3.3) as well
as reading them back (`load_catalog`) and looking prices up (`Catalog`, `OrgPrices`).
Source adapters (`sources/*.py`) and `build.py` import `RateRow`/`SourceRow`/`TOKEN_TYPES`
from here rather than the other way around, so the dependency graph stays one-directional:
`normalize.py`/`config.py` (leaves) <- `catalog.py` (schema + read path) <-
`sources/*.py` <- `build.py` (write path) <- `reports.py`/`cli_*.py`. `build.py` never
needs to be imported by anything upstream of it, which is what lets `sources/*.py` share
the row schema without a circular import.

Decimal rates round-trip as canonical strings (`format(Decimal, 'f')`, never
scientific notation and never a bare `Decimal.__str__()` -- see `decimals.to_rate_str`)
so the JSON file is stable and diffable (§3.3, §11).
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from common.model_pricing.config import (
    DEFAULT_ROOT as DEFAULT_CONFIG_ROOT,
    ChannelConfig,
    load_aliases_config,
    load_channel_config,
)
from common.model_pricing.decimals import to_rate_str
from common.model_pricing.errors import CatalogError, ConfigError
from common.model_pricing.normalize import Registry, ModelEntry, canon, normalize as normalize_id

#: The five normalized token types (§2.2), in the order every rate tuple/table uses.
TOKEN_TYPES: tuple[str, ...] = (
    "input",
    "output",
    "cache_read",
    "cache_write_5m",
    "cache_write_1h",
)

DEFAULT_CATALOG_ROOT = DEFAULT_CONFIG_ROOT / "catalog"


def _decimal_field_validator(v: Any) -> Decimal | None:
    # str() first: YAML/JSON floats carry a
    # binary fraction into money if handed to Decimal directly.
    return None if v is None else Decimal(str(v))


class RateRow(BaseModel):
    """One `(channel, model_id)` row (§3.1). Rates are `Decimal | None`; `None` means no
    price for that token type, never 0 (§2.2)."""

    model_config = ConfigDict(extra="forbid")

    channel: str
    model_id: str
    start_date: date
    retired_on: date | None = None

    input: Decimal | None = None
    output: Decimal | None = None
    cache_read: Decimal | None = None
    cache_write_5m: Decimal | None = None
    cache_write_1h: Decimal | None = None

    source: str
    source_key: str
    derived: list[str] = Field(default_factory=list)
    #: Present only when a non-LiteLLM source won this row's merge *and* LiteLLM has a
    #: row for the same key (§3.1) -- the losing LiteLLM rates, for the refresh report's
    #: diff. Not used by lookup math.
    litellm_rates: dict[str, str] | None = None

    @field_validator(
        "input", "output", "cache_read", "cache_write_5m", "cache_write_1h", mode="before"
    )
    @classmethod
    def _exact(cls, v: Any) -> Decimal | None:
        return _decimal_field_validator(v)

    @field_serializer(
        "input", "output", "cache_read", "cache_write_5m", "cache_write_1h", when_used="json"
    )
    def _serialize_rate(self, v: Decimal | None) -> str | None:
        return None if v is None else to_rate_str(v)

    def rate(self, token_type: str) -> Decimal | None:
        return getattr(self, token_type)

    def hash_fields(self) -> dict[str, Any]:
        """The subset of this row that participates in `hash8` (§3.3): `channel`,
        `model_id`, the five rates, `retired_on`. Excludes `start_date`, `source`,
        `source_key`, `derived`, `litellm_rates` -- a version that changes none of these
        served-price fields must hash identically to the last one, so `update` can tell
        "nothing changed" from "something changed" without a per-row diff (§3.4)."""
        out: dict[str, Any] = {"channel": self.channel, "model_id": self.model_id}
        for tt in TOKEN_TYPES:
            v = self.rate(tt)
            out[tt] = to_rate_str(v) if v is not None else None
        out["retired_on"] = self.retired_on.isoformat() if self.retired_on else None
        return out


class RegistryEntry(BaseModel):
    """`models[model_id]` in a version file (§3.3, §5.2)."""

    model_config = ConfigDict(extra="forbid")

    vendor: str
    names_seen: list[str]


class CatalogVersion(BaseModel):
    """One `<version>.json` file (§3.3)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    version: str
    built_on: date
    unit: str = "usd_per_token"
    sources: dict[str, dict[str, Any]] = Field(default_factory=dict)
    models: dict[str, RegistryEntry] = Field(default_factory=dict)
    rates: list[RateRow] = Field(default_factory=list)
    excluded: list[dict[str, Any]] = Field(default_factory=list)
    unmapped: list[dict[str, Any]] = Field(default_factory=list)

    def compute_hash8(self) -> str:
        """First 8 hex of SHA-256 over served prices only (§3.3): each row's
        `hash_fields()`, rows sorted by `(channel, model_id)`, canonical JSON."""
        rows = sorted(
            (r.hash_fields() for r in self.rates),
            key=lambda r: (r["channel"], r["model_id"]),
        )
        canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:8]


@dataclass(frozen=True)
class SourceRow:
    """A source adapter's output before merge/derive/validate/stamp (§6.1 steps 3-6):
    not yet a `RateRow` because it has no `start_date` (stamped at build time, §3.4) and
    has not survived the merge-precedence contest for its `(channel, model_id)` key."""

    channel: str
    model_id: str
    source: str
    source_key: str
    rates: dict[str, Decimal | None]
    retired_on: date | None = None
    derived: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnmappedEntry:
    """A source record whose model or price name could not be normalized (§3.3
    `unmapped`)."""

    source: str
    source_key: str
    reason: str


@dataclass(frozen=True)
class UnmatchedCandidate:
    """An AWS/Azure record whose reduced model string does not resolve against the
    registry built so far (§5.2 "Official-source models") -- not yet known to be truly
    unmapped. `build.py`'s promotion pass tries to infer a vendor from the model
    string's prefix (`vendor_prefixes`, §5.2) and, if it can, adds a new registry entry
    and turns this into a real priced `SourceRow`; if it can't (no known prefix, e.g.
    Cohere, or the string collides ambiguously with another official-source candidate),
    this ends up in `unmapped` instead, same as before this feature existed.

    Carries everything a `SourceRow` would once its `model_id` is known -- `channel`
    and `rates` are already fully resolved (scheme-conflict resolution for AWS, meter
    parsing for Azure, both already applied) by the adapter; only the registry lookup
    is deferred.
    """

    source: str
    model_string: str
    source_key: str
    channel: str
    rates: dict[str, Decimal | None]
    retired_on: date | None = None


def _index_path(root: Path) -> Path:
    return root / "index.json"


def _version_path(root: Path, version: str) -> Path:
    return root / f"{version}.json"


def _read_index(root: Path) -> list[str]:
    idx_path = _index_path(root)
    if not idx_path.exists():
        raise CatalogError(
            f"no catalog at {root} -- run `./utils/model_pricing.sh update` first"
        )
    try:
        data = json.loads(idx_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"{idx_path}: unreadable: {exc}") from exc
    versions = data.get("versions") if isinstance(data, dict) else None
    if not isinstance(versions, list) or not versions:
        raise CatalogError(f"{idx_path}: no versions listed")
    return versions


def _read_version(root: Path, version: str) -> CatalogVersion:
    path = _version_path(root, version)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"{path}: unreadable: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{path}: invalid JSON: {exc}") from exc
    try:
        cv = CatalogVersion.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise CatalogError(f"{path}: {exc}") from exc

    expected_suffix = cv.version.rsplit("-", 1)[-1]
    actual = cv.compute_hash8()
    if actual != expected_suffix:
        raise CatalogError(
            f"{path}: hash8 mismatch -- version says {expected_suffix!r}, "
            f"recomputed {actual!r} (file modified since it was written?)"
        )
    return cv


def _union_registry_models(loaded: list[CatalogVersion]) -> dict[str, RegistryEntry]:
    """Union the `models` dict of every loaded version, oldest first (§5.2, §9).

    A `model_id` appearing in more than one version must carry the same vendor --
    vendors do not change under a stable `model_id` -- so a conflict raises
    `CatalogError` rather than silently picking one. `names_seen` unions across
    versions (a source may see new aliases for the same model over time).
    """
    merged: dict[str, RegistryEntry] = {}
    for cv in loaded:
        for model_id, entry in cv.models.items():
            existing = merged.get(model_id)
            if existing is None:
                merged[model_id] = entry
                continue
            if existing.vendor != entry.vendor:
                raise CatalogError(
                    f"model {model_id!r} has conflicting vendors across catalog "
                    f"versions: {existing.vendor!r} (earlier) vs {entry.vendor!r} "
                    f"({cv.version})"
                )
            if set(entry.names_seen) - set(existing.names_seen):
                merged[model_id] = RegistryEntry(
                    vendor=existing.vendor,
                    names_seen=sorted(set(existing.names_seen) | set(entry.names_seen)),
                )
    return merged


def _build_normalize_registry(models: dict[str, RegistryEntry]) -> Registry:
    """A `normalize.Registry` from a catalog's persisted `models` dict.

    The version file does not persist which `canon` groups were ambiguous (§3.3's
    `models` shape is just `{model_id: {vendor, names_seen}}`) -- but it does not need
    to: `build_registry` (§5.2) only ever produces *one* `model_id` per canon group
    when that group's rates agreed (`model_id = canon(key)`), and *several* distinct
    `model_id`s sharing a canon exactly when the group's rates disagreed (each
    `model_id = norm(key)`, and the canon recorded ambiguous). So a canon shared by
    two or more distinct registered `model_id`s -- recomputed here from the `model_id`
    strings themselves, which are already `norm()`-stable -- is precisely the
    ambiguous set `normalize()` step 3 (§5.3) needs. Re-derived on every catalog load
    rather than trusted from a stored flag, so it always agrees with the registry it
    describes.
    """
    by_canon: dict[str, set[str]] = defaultdict(set)
    for model_id in models:
        by_canon[canon(model_id)].add(model_id)
    ambiguous = {c for c, ids in by_canon.items() if len(ids) > 1}
    entries = {
        model_id: ModelEntry(vendor=entry.vendor, names_seen=tuple(entry.names_seen))
        for model_id, entry in models.items()
    }
    return Registry(models=entries, ambiguous=ambiguous)


@dataclass(frozen=True)
class MatchResult:
    """One observed model string resolved for one org (§9, §2.4 steps 1-3, §7 mapping
    report). Never constructed for a raised error -- an unresolvable or ambiguous
    model is a *result* (`status`), not an exception (§9, `errors.py`)."""

    usage_model_id: str
    org_id: str
    model_id: str | None
    vendor: str | None
    channel: str | None
    status: str  # "matched" | "ambiguous" | "unmatched"
    step: str | None  # "alias" | "normalized" | "canonical" | None
    reason: str | None
    #: Other `model_id`s sharing the ambiguous canon (§5.3 step 3, rate-sheet
    #: `candidates`, model-pricing design §4); empty unless `status == "ambiguous"`.
    #: Additive field -- does not change matching behaviour.
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrgPrices:
    """The rate table as one org sees it: `resolve()` applies §2.4 steps 1-3 (model_id,
    vendor, channel) plus the non-dated part of step 4 (a row must exist for the
    resolved `(channel, model_id)` in *some* loaded version); `rate()` applies the
    dated part of step 4 -- forward/backward/retired (§3.4) -- and step 5's per-rate
    lookup. Never mutated after `Catalog.for_org` builds it."""

    org_id: str
    registry: Registry
    aliases: dict[str, str]
    channel_config: ChannelConfig
    #: `(channel, model_id) -> rows`, every loaded version's rows for that key, sorted
    #: ascending by `start_date` (§3.4 forward/backward rely on that order). Shared,
    #: read-only, across every org's `OrgPrices` from the same `Catalog`.
    rows_by_key: dict[tuple[str, str], tuple[RateRow, ...]]

    def resolve(self, x: str) -> MatchResult:
        """§2.4 steps 1-3 plus the non-dated half of step 4. Never raises: an
        unresolvable, ambiguous, or unpriced-on-this-channel model comes back as a
        `MatchResult` whose `status` says so, so a caller pricing a whole day of usage
        can carry on past one bad model string."""
        result = normalize_id(x, self.registry, self.aliases)

        if result.status == "unmatched":
            return MatchResult(
                usage_model_id=x,
                org_id=self.org_id,
                model_id=None,
                vendor=None,
                channel=None,
                status="unmatched",
                step=None,
                reason=f"model id not recognized: {x!r}",
            )
        if result.status == "ambiguous":
            return MatchResult(
                usage_model_id=x,
                org_id=self.org_id,
                model_id=None,
                vendor=None,
                channel=None,
                status="ambiguous",
                step=None,
                reason=(
                    f"ambiguous model id: {canon(x)!r} matches several dated "
                    "variants that price differently"
                ),
                candidates=result.candidates,
            )

        model_id = result.model_id
        assert model_id is not None  # matched implies a model_id (normalize.py contract)
        vendor = self.registry.vendor_of(model_id)
        channel = self.channel_config.channel_for(self.org_id, vendor)

        if (channel, model_id) not in self.rows_by_key:
            # §2.4 step 4, no-fallback: the org buys `vendor` through `channel` and no
            # other; a price on some other channel never substitutes (§11).
            return MatchResult(
                usage_model_id=x,
                org_id=self.org_id,
                model_id=model_id,
                vendor=vendor,
                channel=channel,
                status="unmatched",
                step=result.step,
                reason=f"no price on channel {channel}",
            )

        return MatchResult(
            usage_model_id=x,
            org_id=self.org_id,
            model_id=model_id,
            vendor=vendor,
            channel=channel,
            status="matched",
            step=result.step,
            reason=None,
        )

    def rate(self, match: MatchResult, token_type: str, on_date: date) -> Decimal | None:
        """The rate for `token_type` in effect on `on_date` (§3.4). `None` if `match`
        is not `matched`, the key has no rows (should not happen once `resolve`
        returned `matched`, but never trusted blindly), the model is retired as of
        `on_date`, or the row in effect simply lacks that rate (§2.2)."""
        if match.status != "matched" or match.channel is None or match.model_id is None:
            return None
        rows = self.rows_by_key.get((match.channel, match.model_id))
        if not rows:
            return None

        # Retired: the *latest* row for the key, not the row `on_date` would resolve
        # to, decides retirement (§3.4) -- a later row with no `retired_on` un-retires
        # the key for every date, including ones between an earlier row's
        # `retired_on` and the later row's `start_date`.
        latest = rows[-1]
        if latest.retired_on is not None and on_date > latest.retired_on:
            return None

        candidates = [r for r in rows if r.start_date <= on_date]
        row = max(candidates, key=lambda r: r.start_date) if candidates else rows[0]
        return row.rate(token_type)

    def rates(self, match: MatchResult, on_date: date) -> dict[str, Decimal | None]:
        """`rate()` for every normalized token type (§2.2), as a convenience for
        pricing a whole usage row at once."""
        return {tt: self.rate(match, tt, on_date) for tt in TOKEN_TYPES}

    def mapping_report(self, ids: list[str]) -> list[MatchResult]:
        """`resolve()` for each of `ids`, failures (`unmatched`/`ambiguous`) first,
        original relative order preserved within each group (§7)."""
        return sorted(
            (self.resolve(x) for x in ids),
            key=lambda m: m.status == "matched",
        )


@dataclass(frozen=True)
class Catalog:
    """A bound catalog version plus every earlier version's rows and registry entries
    (§3.4, §9) -- the price history up to and including the bound version, never past
    it (version binding, §9). Built once by `load_catalog`; every view derived from it
    (`for_org`, and in turn `OrgPrices.resolve`/`rate`) reads only what was loaded
    here."""

    #: The bound version's own `version` string (`<date>-<hash8>`).
    version: str
    #: The bound version's `sources` provenance block (§3.3).
    sources: dict[str, dict[str, Any]]
    #: Where `for_org` reads `channel_config.yaml` / `aliases.yaml` from (§4.3, §5.3).
    config_root: Path
    #: Every version actually read, oldest first, ending with the bound version --
    #: for error messages and for reproducing "what was loaded" (§9).
    loaded_versions: tuple[str, ...]
    #: The union registry across every loaded version (§5.2, §9), with ambiguity
    #: re-derived (`_build_normalize_registry`).
    registry: Registry
    #: `(channel, model_id) -> rows`, union of every loaded version's `rates`, sorted
    #: ascending by `start_date`.
    rows_by_key: dict[tuple[str, str], tuple[RateRow, ...]]

    def for_org(self, org_id: str) -> OrgPrices:
        """`OrgPrices` for `org_id`, applying channel config (§4.3): absent file, org
        not listed, or vendor not listed -> the vendor channel. Raises `ConfigError`
        if a channel configured for `org_id` is not priced in any loaded version --
        checked eagerly here rather than deferred to the first `resolve()`, so a
        misconfigured org fails loudly at the point its config is applied."""
        channel_config = load_channel_config(self.config_root / "channel_config.yaml")
        aliases_config = load_aliases_config(self.config_root / "aliases.yaml")

        priced_channels = {channel for channel, _ in self.rows_by_key}
        for vendor, channel in channel_config.orgs.get(org_id, {}).items():
            if channel not in priced_channels:
                raise ConfigError(
                    f"channel_config.yaml: org {org_id!r} vendor {vendor!r} -> "
                    f"channel {channel!r}, which is not priced in any loaded "
                    f"catalog version (loaded: {', '.join(self.loaded_versions)})"
                )

        return OrgPrices(
            org_id=org_id,
            registry=self.registry,
            aliases=dict(aliases_config.aliases),
            channel_config=channel_config,
            rows_by_key=self.rows_by_key,
        )


def load_catalog(
    version: str | None = None,
    root: str | Path = DEFAULT_CATALOG_ROOT,
    config_root: str | Path = DEFAULT_CONFIG_ROOT,
) -> Catalog:
    """Read one catalog version and every earlier version (index order, §3.4, §9).

    `version=None` binds to the last entry in `index.json` (the current version) at
    load time. Every loaded file's `hash8` is verified (`_read_version`); a version
    requested by name that is not in the index is a `CatalogError`, same as no index
    or an unreadable file (`_read_index`/`_read_version`). Versions listed *after* the
    bound one are never read (version binding).
    """
    root = Path(root)
    config_root = Path(config_root)

    all_versions = _read_index(root)
    bound = version if version is not None else all_versions[-1]
    if bound not in all_versions:
        raise CatalogError(
            f"unknown catalog version {bound!r} (have: {', '.join(all_versions)})"
        )

    to_load = all_versions[: all_versions.index(bound) + 1]
    loaded = [_read_version(root, v) for v in to_load]

    registry_models = _union_registry_models(loaded)
    registry = _build_normalize_registry(registry_models)

    rows_by_key: dict[tuple[str, str], list[RateRow]] = defaultdict(list)
    for cv in loaded:
        for row in cv.rates:
            rows_by_key[(row.channel, row.model_id)].append(row)
    sorted_rows_by_key = {
        key: tuple(sorted(rows, key=lambda r: r.start_date))
        for key, rows in rows_by_key.items()
    }

    bound_cv = loaded[-1]
    return Catalog(
        version=bound_cv.version,
        sources=bound_cv.sources,
        config_root=config_root,
        loaded_versions=tuple(to_load),
        registry=registry,
        rows_by_key=sorted_rows_by_key,
    )
