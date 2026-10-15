"""Build the master rate table (design §6): acquire every source, extend the registry
with official-source models (§5.2), merge per `(channel, model_id)` with source
precedence, derive missing cache-write rates, validate, retire keys no source
publishes any more (§3.5), stamp, and version.

`build()` is pure -- it takes fetchers (network-touching, injectable so tests never
hit the network) and returns a `BuildResult`; it writes nothing. `write_catalog()` is
the only function here that touches disk, and only after a caller has decided the
result is worth keeping (`write_catalog` itself also refuses a no-op result).

Merge precedence (§4.1, §6.4) -- vendor channels have exactly one source; the three
cloud channel families each have a first (official) source with LiteLLM as fallback:

    anthropic / openai / gemini      -> litellm
    aws_bedrock:<region>:<scope>     -> aws_price_list -> litellm
    azure:<region>:<scope>           -> azure_retail   -> litellm
    gcp_vertex:<region>:<scope>      -> gcp_billing     -> litellm

A row's rates always come from exactly one source (never mixed, §6.4); `litellm_rates`
is recorded on the row only when a non-LiteLLM source won *and* LiteLLM has a row for
the same key, for the refresh report's diff (§3.1).
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from common.model_pricing.catalog import (
    DEFAULT_CATALOG_ROOT,
    TOKEN_TYPES,
    CatalogVersion,
    RateRow,
    RegistryEntry,
    SourceRow,
    UnmappedEntry,
    UnmatchedCandidate,
    _read_index,
    _read_version,
)
from common.model_pricing.config import ModelPricingConfig, channel_cloud, infer_vendor
from common.model_pricing.decimals import to_rate_str
from common.model_pricing.errors import CatalogError, SourceError
from common.model_pricing.normalize import ModelEntry, Registry, canon, norm
from common.model_pricing.reports import render_refresh, render_refresh_json
from common.model_pricing.sources import aws_price_list, azure_retail, gcp_billing, litellm

#: Real fetchers, keyed the same way as the injectable `fetchers` argument to
#: `build()`. Tests pass their own dict (fixture-backed, no network); production
#: callers (the `update` CLI, T9) pass nothing and get these.
DEFAULT_FETCHERS: dict[str, Callable[..., Any]] = {
    "litellm": litellm.fetch,
    "aws_price_list": aws_price_list.fetch,
    "azure_retail": azure_retail.fetch,
    "gcp_billing": gcp_billing.fetch,
}

#: Source precedence per channel family (§4.1, §6.4). A vendor channel has one
#: source; a cloud channel has its official list first, LiteLLM as fallback.
_CLOUD_PRECEDENCE: dict[str, tuple[str, str]] = {
    "aws_bedrock": ("aws_price_list", "litellm"),
    "azure": ("azure_retail", "litellm"),
    "gcp_vertex": ("gcp_billing", "litellm"),
}

#: Change vs. the previous version beyond this fraction is a warning (§6.6), never a
#: block.
_CHANGE_WARN_THRESHOLD = Decimal("0.5")


def _source_precedence(channel: str) -> tuple[str, ...]:
    """The ordered list of sources allowed to win `channel`'s rows (§4.1). A vendor
    channel (`anthropic`/`openai`/`gemini`) has just LiteLLM; a cloud channel has its
    official source first, LiteLLM as fallback."""
    cloud = channel_cloud(channel)
    if cloud is None:
        return ("litellm",)
    prefix, _region, _scope = cloud
    return _CLOUD_PRECEDENCE[prefix]


def _regions_for_prefix(cfg: ModelPricingConfig, prefix: str) -> list[str]:
    """Distinct regions among `cfg.channels` whose cloud prefix is `prefix`, in the
    order they first appear -- the regions this source's `fetch()` must be called for
    (§6.2: "per built region")."""
    seen: list[str] = []
    for ch in cfg.channels:
        cloud = channel_cloud(ch)
        if cloud and cloud[0] == prefix and cloud[1] not in seen:
            seen.append(cloud[1])
    return seen


def _call_fetch(fetchers: dict[str, Callable[..., Any]], name: str, *args: Any) -> Any:
    """Invoke one injected fetcher, upgrading any exception it raises to
    `SourceError` (§6.1: "Any enabled source fails -> update fails (exit 1); nothing
    produced") -- a `SourceError` a fetcher already raises (e.g. `gcp_billing.fetch`
    when enabled but unimplemented, §12) passes through unchanged rather than getting
    double-wrapped."""
    try:
        return fetchers[name](*args)
    except SourceError:
        raise
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any fetch failure fails the build
        raise SourceError(f"{name}: {exc}") from exc


def _merge_region_provenance(per_region: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Several regions' `fetch()` provenance dicts -> one `sources.<name>` block
    (§3.3's shape: `{..fields from one fetch.., "regions": [...]}`). Fields come from
    the first region fetched; only `region`/`regions` are replaced with the union
    list, since every region of one source shares the same provenance shape."""
    if not per_region:
        return {}
    first = next(iter(per_region.values()))
    merged = {k: v for k, v in first.items() if k not in ("region", "regions")}
    merged["regions"] = sorted(per_region.keys())
    return merged


def _skipped_entry(regions: list[str], enabled: bool) -> dict[str, Any]:
    """`sources.<name>` provenance when a cloud source's `fetch()` was never called
    because there was nothing for it to do -- no channel of its cloud configured, or
    the source explicitly disabled in `model_pricing.yaml` (§4.2). Mirrors
    `gcp_billing.fetch`'s own `{"status": "skipped", "reason": ...}` shape."""
    if not regions:
        return {"status": "skipped", "reason": "no channels configured"}
    assert not enabled
    return {"status": "skipped", "reason": "disabled in model_pricing.yaml"}


@dataclass(frozen=True)
class SourceDiff:
    """One `source_diff` entry (§3.1, §6.6, §7): a row where a non-LiteLLM source won
    and its rates differ from LiteLLM's for the same key."""

    channel: str
    model_id: str
    source: str
    winner_rates: dict[str, str]
    litellm_rates: dict[str, str]


@dataclass(frozen=True)
class Added:
    """A `(channel, model_id)` row present in this build but absent from `previous`
    (§6.6, §7) -- every rate the row was built with, for the refresh report's "Added"
    section. Always empty on the first build (`previous is None`): with nothing to diff
    against, every row would otherwise show up as "added", which is noise rather than
    signal (the report says "first version" instead, §7)."""

    channel: str
    model_id: str
    rates: dict[str, str]


@dataclass(frozen=True)
class Changed:
    """One rate that differs from the same `(channel, model_id)` row in `previous`
    (§6.6, §7) -- every differing rate, compared as `Decimal`, not only the ones that
    cross the >50% warning threshold (that check still runs separately and *additionally*
    raises a warning, §6.6). `pct` is the signed percent change (`None` when `old` is
    zero, where a percent change is undefined -- that case still gets a warning,
    §6.6's "from zero"). Always empty on the first build, same reasoning as `Added`."""

    channel: str
    model_id: str
    token_type: str
    old: str
    new: str
    pct: Decimal | None


@dataclass(frozen=True)
class Retired:
    """A `(channel, model_id)` priced in `previous`, absent from this build, and now
    carrying a retirement row (§3.5, §6.1 step 6) -- either freshly retired this build
    (`is_new`, `retired_on = built_on - 1 day`) or already retired and simply carried
    forward unchanged (§3.5: "keeps the version hash stable"). `last_rates` is the
    retirement row's own rates (the last known price, unaffected either way)."""

    channel: str
    model_id: str
    retired_on: date
    is_new: bool
    last_rates: dict[str, str]


@dataclass(frozen=True)
class BuildResult:
    """Everything one `build()` run produced (§6, §7) -- the finished `CatalogVersion`
    plus the report material `reports.render_refresh` (T8) needs and `write_catalog`
    persists. Immutable and disk-free: nothing here has been written anywhere."""

    version: CatalogVersion
    is_noop: bool
    warnings: list[str]
    source_diff: list[SourceDiff]
    retired: list[Retired]
    excluded: list[dict[str, Any]]
    unmapped: list[UnmappedEntry]
    #: `sources.<name>` provenance, same shape as `CatalogVersion.sources` (§3.3).
    source_provenance: dict[str, dict[str, Any]]
    #: `channel -> {"first": n, "fallback": n}` (§7): rows won by the channel's
    #: official source vs. rows that fell back to LiteLLM. Vendor channels only ever
    #: have `"first"` counts (their one source, LiteLLM, has no fallback, §4.1).
    channel_source_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    #: `(channel, model_id)` keys this build has that `previous` did not (§6.6, §7).
    #: Empty when `previous is None` (first build).
    added: list[Added] = field(default_factory=list)
    #: Every rate that differs from `previous`, one entry per changed token type
    #: (§6.6, §7) -- not just the ones crossing the >50% warning threshold. Empty when
    #: `previous is None` (first build).
    changed: list[Changed] = field(default_factory=list)
    #: `True` when this build had no `previous` catalog version to diff against --
    #: `added`/`changed`/`retired` are all necessarily empty in that case, and the
    #: refresh report says "first version" instead of empty sections (§7).
    is_first_build: bool = False
    #: When this `build()` call ran, UTC (§7's refresh report "run time"). Not part of
    #: `hash8` -- it describes the *build*, not the served prices, so the same prices
    #: rebuilt at a different moment must still hash identically (§3.3). Defaulted via
    #: `field(default_factory=...)`, same pattern as the other report-only fields
    #: above, but `build()` itself always sets it explicitly.
    run_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    #: The `previous` catalog version's own `version` string, or `None` on the first
    #: build (§7's refresh report header: "previous version (or first version)").
    previous_version: str | None = None


def _rows_by_key(rows: list[SourceRow]) -> dict[tuple[str, str], SourceRow]:
    """Last-wins map of one source's rows by `(channel, model_id)`. A source adapter
    normally emits at most one row per key already; if it ever emitted two (not
    observed from any adapter today), the later one in parse order wins -- silently,
    since this is not one of the merge-precedence rules §6.4 defines behavior for."""
    return {(r.channel, r.model_id): r for r in rows}


def _litellm_rates_dict(row: SourceRow) -> dict[str, str]:
    """LiteLLM's rates for a row's key, formatted for `RateRow.litellm_rates` (§3.1):
    only the token types LiteLLM actually published, as canonical rate strings."""
    return {tt: to_rate_str(v) for tt in TOKEN_TYPES if (v := row.rates.get(tt)) is not None}


def _promote_official_source_models(
    candidates: list[UnmatchedCandidate],
    registry: Registry,
    vendor_prefixes: dict[str, str],
) -> tuple[Registry, dict[str, str], list[UnmatchedCandidate]]:
    """§5.2 "Official-source models": extend `registry` with AWS/Azure records whose
    reduced model string has a recognizable vendor prefix (`vendor_prefixes` +
    `config.infer_vendor`'s hardcoded `o<digit>` rule) but did not normalize against
    `registry` as LiteLLM alone built it.

    Grouped by `canon(model_string)`, mirroring §5.2's LiteLLM registry rule -- but
    *not* `normalize.build_registry`'s rate-equality "same model" check: an official-
    source model legitimately prices differently per channel it is on (AWS Bedrock
    regional vs global, say), which must never register as ambiguity the way two
    genuinely different LiteLLM dated snapshots pricing differently does. So "same
    model" here means "the same model string" (candidates are deduplicated by string
    first, collapsing repeats across channels); a canon shared by two or more
    *different* strings is the real §5.2 "ambiguous" case, resolved exactly like
    LiteLLM's own (`build_registry`): each such string still gets its own
    `model_id = norm(string)` and resolves normally -- the canon is only recorded
    ambiguous for some *other*, unseen string that might share it later (§5.3 step 3).

    Returns `(extended_registry, model_string_to_model_id, still_unmapped)`.
    `model_string_to_model_id` covers exactly the candidates that got a vendor
    (whether promoted solo or as part of an ambiguous group -- both resolve, §5.3).
    `still_unmapped` is every candidate with no recognizable vendor prefix (e.g.
    Cohere) -- `build.py`'s caller records these in `unmapped`, same as before this
    feature existed.
    """
    by_canon: dict[str, dict[str, str]] = defaultdict(dict)  # canon -> {model_string: vendor}
    still_unmapped: list[UnmatchedCandidate] = []
    seen_strings: set[str] = set()

    for c in candidates:
        if c.model_string in seen_strings:
            continue  # same model string already seen on another channel (§5.2)
        seen_strings.add(c.model_string)
        vendor = infer_vendor(c.model_string, vendor_prefixes)
        if vendor is None:
            still_unmapped.append(c)
            continue
        by_canon[canon(c.model_string)][c.model_string] = vendor

    models = dict(registry.models)
    ambiguous = set(registry.ambiguous)
    string_to_model_id: dict[str, str] = {}

    for canon_key, strings in by_canon.items():
        if len(strings) == 1:
            s, vendor = next(iter(strings.items()))
            models[canon_key] = ModelEntry(vendor=vendor, names_seen=(s,))
            string_to_model_id[s] = canon_key
            continue

        ambiguous.add(canon_key)
        for s, vendor in strings.items():
            model_id = norm(s)
            existing = models.get(model_id)
            if existing is None:
                models[model_id] = ModelEntry(vendor=vendor, names_seen=(s,))
            else:
                models[model_id] = ModelEntry(
                    vendor=existing.vendor,
                    names_seen=tuple(sorted(set(existing.names_seen) | {s})),
                )
            string_to_model_id[s] = model_id

    return Registry(models=models, ambiguous=ambiguous), string_to_model_id, still_unmapped


def derive_cache_write_rates(rates: dict[str, Decimal | None]) -> list[str]:
    """§6.5 derive, in place; returns the `derived` flags it applied.

    1. No cache-write rate at all -> both billed as `input` (`cache_write_billed_as_input`).
    2. Only `cache_write_5m` published -> `cache_write_1h` takes the 5m rate, the vendor's
       one write price (`cache_write_1h_billed_as_5m`).

    The reverse (only `cache_write_1h` published) is deliberately not derived, and no
    published rate is ever changed."""
    if rates["cache_write_5m"] is None and rates["cache_write_1h"] is None:
        rates["cache_write_5m"] = rates["input"]
        rates["cache_write_1h"] = rates["input"]
        return ["cache_write_billed_as_input"]
    if rates["cache_write_5m"] is not None and rates["cache_write_1h"] is None:
        rates["cache_write_1h"] = rates["cache_write_5m"]
        return ["cache_write_1h_billed_as_5m"]
    return []


def build(
    cfg: ModelPricingConfig,
    fetchers: dict[str, Callable[..., Any]] | None = None,
    previous: CatalogVersion | None = None,
    built_on: date | None = None,
    litellm_commit: str | None = None,
    run_at: datetime | None = None,
) -> BuildResult:
    """Run one `update` (§6.1), purely: acquire -> registry -> normalize -> merge ->
    derive -> validate -> stamp -> version. Writes nothing; call `write_catalog` on
    the result to persist it.

    `fetchers` overrides individual entries of `DEFAULT_FETCHERS` (tests typically
    override `"litellm"`, `"aws_price_list"`, `"azure_retail"`; any source not
    overridden -- e.g. `gcp_billing` when no test needs it -- keeps its default
    fetcher, which is safe to call unconfigured: `gcp_billing.fetch` makes no network
    call unless `GCP_BILLING_API_KEY` is set, §6.2).

    `previous` (typically `load_previous(root)`) gates the >50% change check and
    retirement (§3.5, §6.6) -- both skipped when `None` (first build).

    `run_at` (UTC): when this build ran, for the refresh report header (§7). Injectable
    for deterministic tests; defaults to `datetime.now(timezone.utc)`. Never part of
    `hash8` (§3.3).

    Raises `SourceError` if any enabled source's fetch fails (§6.1) -- nothing is
    produced in that case; the caller must not call `write_catalog`.
    """
    fetchers = {**DEFAULT_FETCHERS, **(fetchers or {})}
    built_on = built_on or date.today()
    run_at = run_at or datetime.now(timezone.utc)

    # ---------------------------------------------------------------- 1. Acquire (§6.2)
    litellm_payload, litellm_prov = _call_fetch(fetchers, "litellm", litellm_commit)

    aws_regions = _regions_for_prefix(cfg, "aws_bedrock")
    aws_enabled = bool(cfg.sources.aws_price_list.enabled) and bool(aws_regions)
    aws_payload_by_region: dict[str, Any] = {}
    aws_prov_by_region: dict[str, dict[str, Any]] = {}
    if aws_enabled:
        for region in aws_regions:
            payload, prov = _call_fetch(fetchers, "aws_price_list", region)
            aws_payload_by_region[region] = payload
            aws_prov_by_region[region] = prov

    azure_regions = _regions_for_prefix(cfg, "azure")
    azure_enabled = bool(cfg.sources.azure_retail.enabled) and bool(azure_regions)
    azure_payload_by_region: dict[str, Any] = {}
    azure_prov_by_region: dict[str, dict[str, Any]] = {}
    if azure_enabled:
        for region in azure_regions:
            items, prov = _call_fetch(fetchers, "azure_retail", region)
            azure_payload_by_region[region] = items
            azure_prov_by_region[region] = prov

    # gcp_billing is always "acquired" (§6.1 step 1: "fetch every enabled source") --
    # its own toggle (auto/true/false) decides whether that does anything; disabled or
    # auto-with-no-key makes no network call and returns a "skipped" provenance (§6.2).
    gcp_payload, gcp_prov = _call_fetch(fetchers, "gcp_billing", cfg.sources.gcp_billing)

    # ---------------------------------------------------------------- 2. Registry (§5.2)
    registry, litellm_rows, litellm_unmapped = litellm.parse(litellm_payload, cfg)

    # ---------------------------------------------------------------- 3. Normalize (§5.3, §6.3-§6.4)
    aws_rows: list[SourceRow] = []
    aws_unmapped: list[UnmappedEntry] = []
    aws_warnings: list[str] = []
    aws_candidates: list[UnmatchedCandidate] = []
    for region, payload in aws_payload_by_region.items():
        rows, unmapped, candidates, warnings = aws_price_list.parse(payload, region, registry)
        aws_rows += rows
        aws_unmapped += unmapped
        aws_candidates += candidates
        aws_warnings += warnings

    azure_rows: list[SourceRow] = []
    azure_unmapped: list[UnmappedEntry] = []
    azure_warnings: list[str] = []
    azure_candidates: list[UnmatchedCandidate] = []
    for region, items in azure_payload_by_region.items():
        rows, unmapped, candidates, warnings = azure_retail.parse(items, region, registry)
        azure_rows += rows
        azure_unmapped += unmapped
        azure_candidates += candidates
        azure_warnings += warnings

    gcp_rows, gcp_unmapped, gcp_warnings = gcp_billing.parse(gcp_payload, registry)

    # ------------------------------------------- 2b. Official-source models (§5.2, §6.1 step 2)
    # Every AWS/Azure record whose reduced model string didn't normalize against the
    # LiteLLM-only registry above gets one more chance: a recognizable vendor prefix
    # (`claude`/`gpt`/`gemini`/`o<digit>`) promotes it to a real registry entry and a
    # priced row, on whichever channel(s) actually published it -- must run *after*
    # both adapters (it needs every candidate first) and *before* the merge loop below
    # (which needs `registry` to build `models`, and the promoted rows to be in
    # `aws_rows`/`azure_rows` so they take part in merge precedence normally).
    registry, promoted_model_ids, still_unmapped = _promote_official_source_models(
        aws_candidates + azure_candidates, registry, cfg.vendor_prefixes
    )
    for c in aws_candidates + azure_candidates:
        model_id = promoted_model_ids.get(c.model_string)
        if model_id is None:
            continue
        row = SourceRow(
            channel=c.channel,
            model_id=model_id,
            source=c.source,
            source_key=c.source_key,
            rates=c.rates,
            retired_on=c.retired_on,
        )
        (aws_rows if c.source == "aws_price_list" else azure_rows).append(row)
    for c in still_unmapped:
        entry = UnmappedEntry(source=c.source, source_key=c.source_key, reason="unknown vendor prefix")
        (aws_unmapped if c.source == "aws_price_list" else azure_unmapped).append(entry)

    source_maps: dict[str, dict[tuple[str, str], SourceRow]] = {
        "litellm": _rows_by_key(litellm_rows),
        "aws_price_list": _rows_by_key(aws_rows),
        "azure_retail": _rows_by_key(azure_rows),
        "gcp_billing": _rows_by_key(gcp_rows),
    }

    all_keys = sorted(
        {key for smap in source_maps.values() for key in smap},
        key=lambda k: (k[0], k[1]),
    )

    # ---------------------------------------------------------------- previous-version lookup (§6.6)
    previous_map: dict[tuple[str, str], RateRow] = {}
    if previous is not None:
        for row in previous.rates:
            previous_map[(row.channel, row.model_id)] = row

    # ---------------------------------------------------------------- 4. Merge (§6.4) + 5. Derive (§6.5) + validate (§6.6)
    final_rows: list[RateRow] = []
    excluded: list[dict[str, Any]] = []
    validation_warnings: list[str] = []
    source_diff: list[SourceDiff] = []
    changed: list[Changed] = []
    channel_source_counts: dict[str, dict[str, int]] = {}

    for channel, model_id in all_keys:
        precedence = _source_precedence(channel)
        winner_source: str | None = None
        winner_row: SourceRow | None = None
        for source_name in precedence:
            candidate = source_maps[source_name].get((channel, model_id))
            if candidate is None:
                continue
            if candidate.rates.get("input") is not None and candidate.rates.get("output") is not None:
                winner_source = source_name
                winner_row = candidate
                break
        if winner_row is None or winner_source is None:
            continue  # no source has both input and output for this key -> no row (§6.4)

        rates: dict[str, Decimal | None] = {tt: winner_row.rates.get(tt) for tt in TOKEN_TYPES}
        #: The winner's rates exactly as published, before §6.5 derive fills in any
        #: missing cache-write rate from `input` -- the source_diff comparison below
        #: must use this, not the post-derive `rates`, or a rate the winning source
        #: never actually published (only derived) would falsely register as a "diff"
        #: against LiteLLM (fixed false positive: Azure `gpt-5-5`).
        raw_rates: dict[str, Decimal | None] = dict(rates)
        derived: list[str] = []

        derived.extend(derive_cache_write_rates(rates))

        litellm_row = source_maps["litellm"].get((channel, model_id))
        litellm_rates: dict[str, str] | None = None
        if winner_source != "litellm" and litellm_row is not None:
            litellm_rates = _litellm_rates_dict(litellm_row)
            # Compare only token types LiteLLM actually published, using the winner's
            # *raw* (pre-derive) rates: a token type LiteLLM never published cannot be
            # "diffed" against it, and a rate the winner only has because it was
            # derived (never actually published by the winning source) must not be
            # compared either (§6.6: "first source != litellm"). Decimal equality, not
            # string equality: the same dollar figure can round-trip through different
            # sources with a different number of trailing zeros (AWS's per-million
            # division vs. LiteLLM's JSON float), which must not register as a "diff".
            compared_token_types = [tt for tt in TOKEN_TYPES if litellm_row.rates.get(tt) is not None]
            differs = any(
                raw_rates.get(tt) != litellm_row.rates.get(tt) for tt in compared_token_types
            )
            if differs:
                source_diff.append(
                    SourceDiff(
                        channel=channel,
                        model_id=model_id,
                        source=winner_source,
                        winner_rates={
                            tt: to_rate_str(v)
                            for tt in TOKEN_TYPES
                            if (v := raw_rates.get(tt)) is not None
                        },
                        litellm_rates=litellm_rates,
                    )
                )

        # §6.6 validate: input/output missing/negative -> excluded (missing already
        # ruled out by the merge-selection loop above).
        if rates["input"] < 0 or rates["output"] < 0:  # type: ignore[operator]
            excluded.append(
                {
                    "channel": channel,
                    "model_id": model_id,
                    "source": winner_source,
                    "reason": "input or output rate is negative",
                }
            )
            continue

        for tt in ("cache_read", "cache_write_5m", "cache_write_1h"):
            v = rates[tt]
            if v is not None and v < 0:
                rates[tt] = None
                validation_warnings.append(
                    f"{channel}/{model_id}: {tt} was negative ({v}), dropped to None"
                )

        if previous is not None:
            prev_row = previous_map.get((channel, model_id))
            if prev_row is not None:
                for tt in TOKEN_TYPES:
                    old = prev_row.rate(tt)
                    new = rates[tt]
                    if old is None or new is None:
                        continue
                    # Decimal equality, not string equality -- same reasoning as the
                    # source_diff check above: a round-trip through formatting must
                    # never register as a "change" (§6.6).
                    if old == new:
                        continue
                    if old == 0:
                        # Percent change is undefined from zero; still recorded as a
                        # change (§7), just with no `pct`.
                        changed.append(
                            Changed(
                                channel=channel,
                                model_id=model_id,
                                token_type=tt,
                                old=to_rate_str(old),
                                new=to_rate_str(new),
                                pct=None,
                            )
                        )
                        validation_warnings.append(
                            f"{channel}/{model_id}: {tt} changed {old} -> {new} (from zero)"
                        )
                        continue
                    signed_pct = (new - old) / abs(old) * 100
                    changed.append(
                        Changed(
                            channel=channel,
                            model_id=model_id,
                            token_type=tt,
                            old=to_rate_str(old),
                            new=to_rate_str(new),
                            pct=signed_pct,
                        )
                    )
                    # >50% change is a warning in addition to being listed above
                    # (§6.6) -- magnitude only, direction is already in `pct`.
                    if abs(signed_pct) > _CHANGE_WARN_THRESHOLD * 100:
                        validation_warnings.append(
                            f"{channel}/{model_id}: {tt} changed {old} -> {new} "
                            f"({abs(signed_pct):.1f}% > 50%)"
                        )

        cache_read = rates["cache_read"]
        input_rate = rates["input"]
        output_rate = rates["output"]
        if cache_read is not None and input_rate is not None and cache_read >= input_rate:
            validation_warnings.append(
                f"{channel}/{model_id}: cache_read ({cache_read}) >= input ({input_rate})"
            )
        if output_rate is not None and input_rate is not None and output_rate < input_rate:
            validation_warnings.append(
                f"{channel}/{model_id}: output ({output_rate}) < input ({input_rate})"
            )

        row = RateRow(
            channel=channel,
            model_id=model_id,
            start_date=built_on,
            retired_on=winner_row.retired_on,
            source=winner_source,
            source_key=winner_row.source_key,
            derived=derived,
            litellm_rates=litellm_rates,
            **{tt: rates[tt] for tt in TOKEN_TYPES},
        )
        final_rows.append(row)

        bucket = channel_source_counts.setdefault(channel, {"first": 0, "fallback": 0})
        bucket["first" if winner_source == precedence[0] else "fallback"] += 1

    # ---------------------------------------------------------------- 6. Retire (§3.5, §6.1 step 6, §6.6, §7)
    retired: list[Retired] = []
    if previous is not None:
        published_keys = {(r.channel, r.model_id) for r in final_rows}
        for (channel, model_id), prev_row in previous_map.items():
            if (channel, model_id) in published_keys:
                continue  # still published (possibly re-published after a retirement) -- not retired

            if prev_row.retired_on is not None:
                # Already retired -- by an earlier build's retirement row, or by a
                # source's own `deprecation_date` (§3.4's unified rule: either way,
                # `retired_on` already governs every future lookup correctly). Carried
                # into this version's `rates` byte-identical -- NOT re-stamped with
                # today's `built_on` -- so `hash8` stays stable across a run where
                # nothing further changed (§3.5: "keeps the version hash stable"; a
                # v2->v3 build with the same absence must be a no-op vs v2).
                final_rows.append(prev_row)
                is_new = False
                row_for_report = prev_row
            else:
                # Newly retired this build (§6.1 step 7): a fresh row, last known
                # rates, `retired_on = built_on - 1 day` (retired as of the day before
                # this build first saw the absence).
                retirement_row = RateRow(
                    channel=channel,
                    model_id=model_id,
                    start_date=built_on,
                    retired_on=built_on - timedelta(days=1),
                    source="retired",
                    source_key=prev_row.source_key,
                    derived=prev_row.derived,
                    litellm_rates=prev_row.litellm_rates,
                    **{tt: prev_row.rate(tt) for tt in TOKEN_TYPES},
                )
                final_rows.append(retirement_row)
                is_new = True
                row_for_report = retirement_row

            retired.append(
                Retired(
                    channel=channel,
                    model_id=model_id,
                    retired_on=row_for_report.retired_on,  # type: ignore[arg-type]
                    is_new=is_new,
                    last_rates={
                        tt: to_rate_str(v)
                        for tt in TOKEN_TYPES
                        if (v := row_for_report.rate(tt)) is not None
                    },
                )
            )
        retired.sort(key=lambda r: (r.channel, r.model_id))

    changed.sort(key=lambda c: (c.channel, c.model_id, c.token_type))

    # ---------------------------------------------------------------- added (§6.6, §7)
    added: list[Added] = []
    if previous is not None:
        for row in final_rows:
            if (row.channel, row.model_id) in previous_map:
                continue
            added.append(
                Added(
                    channel=row.channel,
                    model_id=row.model_id,
                    rates={
                        tt: to_rate_str(v) for tt in TOKEN_TYPES if (v := row.rate(tt)) is not None
                    },
                )
            )
        added.sort(key=lambda a: (a.channel, a.model_id))

    # ---------------------------------------------------------------- sources provenance (§3.3)
    source_provenance: dict[str, dict[str, Any]] = {"litellm": litellm_prov}
    source_provenance["aws_price_list"] = (
        _merge_region_provenance(aws_prov_by_region)
        if aws_prov_by_region
        else _skipped_entry(aws_regions, bool(cfg.sources.aws_price_list.enabled))
    )
    source_provenance["azure_retail"] = (
        _merge_region_provenance(azure_prov_by_region)
        if azure_prov_by_region
        else _skipped_entry(azure_regions, bool(cfg.sources.azure_retail.enabled))
    )
    source_provenance["gcp_billing"] = gcp_prov

    # ---------------------------------------------------------------- models + unmapped (§3.3, §5.2)
    models: dict[str, RegistryEntry] = {
        model_id: RegistryEntry(vendor=entry.vendor, names_seen=sorted(entry.names_seen))
        for model_id, entry in registry.models.items()
    }
    all_unmapped: list[UnmappedEntry] = (
        litellm_unmapped + aws_unmapped + azure_unmapped + gcp_unmapped
    )
    unmapped_dicts = [
        {"source": u.source, "source_key": u.source_key, "reason": u.reason} for u in all_unmapped
    ]

    all_warnings = aws_warnings + azure_warnings + gcp_warnings + validation_warnings

    # ---------------------------------------------------------------- 7. Stamp + 8. Version (§3.3, §3.4)
    final_rows.sort(key=lambda r: (r.channel, r.model_id))
    stub = CatalogVersion(
        version="pending",
        built_on=built_on,
        sources=source_provenance,
        models=models,
        rates=final_rows,
        excluded=excluded,
        unmapped=unmapped_dicts,
    )
    hash8 = stub.compute_hash8()
    version_str = f"{built_on.isoformat()}-{hash8}"
    version = stub.model_copy(update={"version": version_str})

    is_noop = previous is not None and previous.compute_hash8() == hash8

    return BuildResult(
        version=version,
        is_noop=is_noop,
        warnings=all_warnings,
        source_diff=source_diff,
        retired=retired,
        excluded=excluded,
        unmapped=all_unmapped,
        source_provenance=source_provenance,
        channel_source_counts=channel_source_counts,
        added=added,
        changed=changed,
        is_first_build=previous is None,
        run_at=run_at,
        previous_version=previous.version if previous is not None else None,
    )


def load_previous(root: str | Path = DEFAULT_CATALOG_ROOT) -> CatalogVersion | None:
    """The latest `CatalogVersion` at `root`, or `None` if no catalog exists yet
    (first build, §6.6's "skipped on the first build"). Reuses `catalog`'s own
    index/version readers so "what counts as the current version" is defined in
    exactly one place."""
    root = Path(root)
    # Only a missing index means "no catalog yet". An unreadable or empty index is a
    # broken catalog: treating it as a first build would rewrite index.json with just
    # the new version and silently drop every earlier price (§3.4 lookups use them).
    if not (root / "index.json").exists():
        return None
    versions = _read_index(root)
    return _read_version(root, versions[-1])


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` via a temp file + `os.replace` (same directory, so the
    replace is atomic on POSIX) -- a reader never sees a partially written file."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_version_file(path: Path, version: CatalogVersion) -> None:
    """Write `<version>.json`, or -- if it already exists (crash recovery: a previous
    `write_catalog` call wrote the version file but died before the index append) --
    verify it is the same content and leave it alone (§6.1's atomic-write step)."""
    if path.exists():
        existing = CatalogVersion.model_validate_json(path.read_text(encoding="utf-8"))
        if existing.compute_hash8() != version.compute_hash8():
            raise CatalogError(
                f"{path} already exists with different content (hash8 "
                f"{existing.compute_hash8()!r} vs {version.compute_hash8()!r}) -- "
                "refusing to overwrite a committed catalog version"
            )
        return
    _atomic_write_text(path, version.model_dump_json(indent=2))


def _append_index(root: Path, version_str: str) -> None:
    index_path = root / "index.json"
    # An existing but unreadable index is never replaced: that would drop history.
    versions = _read_index(root) if index_path.exists() else []
    if version_str in versions:
        return
    versions.append(version_str)
    _atomic_write_text(index_path, json.dumps({"versions": versions}, indent=2) + "\n")


def write_catalog(result: BuildResult, root: str | Path = DEFAULT_CATALOG_ROOT) -> None:
    """Persist a `BuildResult` (§6.1 step 7): `<version>.json`, then
    `<version>.refresh.md`, then `<version>.refresh.json`, then append `index.json` --
    in that order, each written atomically (temp file + `os.replace`), so a crash
    between files leaves the catalog readable and the next `write_catalog` call resumes
    from wherever it stopped (`_write_version_file`'s crash-recovery check; the refresh
    markdown/JSON and index append are each idempotent -- skipped/no-op if already
    present).

    A no-op result (`result.is_noop`) is never written (§6.1: "unchanged -> no-op
    (exit 0)") -- nothing changed, so there is nothing new to commit.
    """
    if result.is_noop:
        return

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    version_str = result.version.version
    version_path = root / f"{version_str}.json"
    _write_version_file(version_path, result.version)

    refresh_path = root / f"{version_str}.refresh.md"
    if not refresh_path.exists():
        _atomic_write_text(refresh_path, render_refresh(result))

    refresh_json_path = root / f"{version_str}.refresh.json"
    if not refresh_json_path.exists():
        _atomic_write_text(refresh_json_path, render_refresh_json(result))

    _append_index(root, version_str)
