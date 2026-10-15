"""AWS Price List source adapter (design §6.2-§6.4).

AWS publishes two `usagetype` naming schemes for the same rate kinds (a historical
artifact -- newer models use one, older models the other, and no model in the wild uses
both at once, see `test_source_aws.py`'s synthetic case for the one exception this
adapter must still handle correctly). Both are parsed; if a model somehow has rows from
both schemes with different rates for the same `(token_type, scope)`, the newer scheme
wins and a warning is recorded (§6.3) -- never a mix of the two.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from common.model_pricing.catalog import SourceRow, UnmappedEntry, UnmatchedCandidate
from common.model_pricing.decimals import per_million_to_per_token
from common.model_pricing.normalize import Registry, normalize

PRICE_LIST_URL = (
    "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/"
    "AmazonBedrockFoundationModels/current/{region}/index.json"
)

_SERVICE_SUFFIX = " (Amazon Bedrock Edition)"

#: Strip the region-code usagetype prefix (`"USE1-MP:USE1_"`, `"EUC1-MP:EUC1_"`, ...)
#: and the trailing `"-Units"` -- structural on every SKU, the specific abbreviation
#: varies by region and is not needed to read the rest of the string.
_USAGETYPE_PREFIX_RE = re.compile(r"^[^:]+:[^_]+_")
_USAGETYPE_SUFFIX_RE = re.compile(r"-Units$")

_NEWER_RE = re.compile(
    r"^(input|output|cache_read|cache_write)_tokens(_1h)?(_global)?_standard$"
)

_OLDER_MAP = {
    "InputTokenCount": "input",
    "OutputTokenCount": "output",
    "CacheReadInputTokenCount": "cache_read",
    "CacheWriteInputTokenCount": "cache_write_5m",
    "CacheWrite1hInputTokenCount": "cache_write_1h",
}


def fetch(region: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Download the AWS Bedrock price list for one region (§6.2)."""
    url = PRICE_LIST_URL.format(region=region)
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    provenance = {"publication_date": payload.get("publicationDate"), "version": payload.get("version")}
    return payload, provenance


def _model_string(servicename: str) -> str:
    """§5.3: `servicename` minus `" (Amazon Bedrock Edition)"`, lowercased, spaces ->
    `-`. `"Claude Sonnet 5 (Amazon Bedrock Edition)"` -> `"claude-sonnet-5"`."""
    s = servicename
    if s.endswith(_SERVICE_SUFFIX):
        s = s[: -len(_SERVICE_SUFFIX)]
    return s.lower().replace(" ", "-")


def _strip_usagetype(usagetype: str) -> str:
    s = _USAGETYPE_PREFIX_RE.sub("", usagetype, count=1)
    return _USAGETYPE_SUFFIX_RE.sub("", s)


def _classify(usagetype: str) -> tuple[str, str, str] | None:
    """`usagetype` -> `(scheme, token_type, scope)`, or `None` if it is not a
    standard-tier token-count dimension this adapter reads (batch, reserved, a
    non-standard tier such as `LatencyOptimized`, or anything else -- §6.3's "ignored"
    list; these are silently skipped, not recorded as unmapped, exactly like LiteLLM's
    `_above_200k_tokens` and friends)."""
    suffix = _strip_usagetype(usagetype)

    m = _NEWER_RE.match(suffix)
    if m:
        kind, h1, is_global = m.groups()
        token_type = ("cache_write_1h" if h1 else "cache_write_5m") if kind == "cache_write" else kind
        return "newer", token_type, ("global" if is_global else "regional")

    is_global = suffix.endswith("_Global")
    base = suffix[: -len("_Global")] if is_global else suffix
    token_type = _OLDER_MAP.get(base)
    if token_type:
        return "older", token_type, ("global" if is_global else "regional")

    return None


def _price_for_sku(terms_ondemand: dict[str, Any], sku: str) -> Decimal | None:
    """`terms["OnDemand"]` is keyed by SKU; each value is itself a dict of one or more
    `"<sku>.<offerTermCode>"` -> term objects (on-demand Bedrock pricing has exactly one
    term and one price dimension per SKU in practice, but this walks all of them rather
    than assume that)."""
    by_term = terms_ondemand.get(sku)
    if not by_term:
        return None
    for term in by_term.values():
        for dim in term.get("priceDimensions", {}).values():
            usd = dim.get("pricePerUnit", {}).get("USD")
            if usd is None:
                continue
            try:
                return Decimal(usd)
            except InvalidOperation:
                return None
    return None


@dataclass
class _Cell:
    scheme: str
    price_per_million: Decimal
    sku: str
    servicename: str


def parse(
    payload: dict[str, Any], region: str, registry: Registry
) -> tuple[list[SourceRow], list[UnmappedEntry], list[UnmatchedCandidate], list[str]]:
    """Products -> `SourceRow`s for `aws_bedrock:<region>:<regional|global>` (§6.4).

    Returns `(rows, unmapped, candidates, warnings)`. Grouping (dimension collection,
    older/newer-scheme conflict resolution) is keyed by the *reduced model string*
    (`_model_string(servicename)`), not by registry `model_id` -- the registry lookup
    is deferred to the very end, per product+scope, so it never influences how a
    product's own price dimensions are resolved (§5.2 "official-source models" needs
    the fully-resolved rates *before* knowing whether the model is in the registry).

    A product whose reduced model string does not normalize against `registry` is
    unmatched, not automatically `unmapped`: `build.py`'s promotion pass may still turn
    it into a registry entry (an AWS/Azure-only model, §5.2), so it comes back as an
    `UnmatchedCandidate` here. A truly non-vendor product (e.g. Cohere, no known vendor
    prefix) ends up in `build.py`'s own `unmapped` list once that pass gives up on it
    -- same end result as before this feature existed, just decided one level up.
    An `ambiguous` result (the model string's canon already collides with an existing
    ambiguous LiteLLM family) is recorded in `unmapped` immediately, same as `matched`
    is recorded as a row immediately: neither should wait on the promotion pass, which
    exists only to *extend* the registry, not to re-litigate what it already knows.
    """
    products = payload.get("products", {})
    terms = payload.get("terms", {}).get("OnDemand", {})

    # (model_string, scope, token_type) -> {"newer": _Cell, "older": _Cell}
    cells: dict[tuple[str, str, str], dict[str, _Cell]] = {}

    for sku, product in products.items():
        attrs = product.get("attributes", {})
        if attrs.get("regionCode") not in (None, region):
            continue
        servicename = attrs.get("servicename", "")
        usagetype = attrs.get("usagetype", "")
        classified = _classify(usagetype)
        if classified is None:
            continue

        price = _price_for_sku(terms, sku)
        if price is None:
            continue

        scheme, token_type, scope = classified
        model_string = _model_string(servicename)
        key = (model_string, scope, token_type)
        cell = _Cell(scheme=scheme, price_per_million=price, sku=sku, servicename=servicename)
        cells.setdefault(key, {})[scheme] = cell

    rows_by_model_scope: dict[tuple[str, str], dict[str, Any]] = {}
    warnings: list[str] = []

    for (model_string, scope, token_type), by_scheme in cells.items():
        newer = by_scheme.get("newer")
        older = by_scheme.get("older")
        if newer and older and newer.price_per_million != older.price_per_million:
            warnings.append(
                f"aws_price_list: {model_string} ({scope}) {token_type}: newer scheme "
                f"{newer.price_per_million} differs from older scheme "
                f"{older.price_per_million} -- newer wins"
            )
        chosen = newer or older
        assert chosen is not None

        entry = rows_by_model_scope.setdefault(
            (model_string, scope),
            {"rates": {}, "servicename": chosen.servicename, "sku": chosen.sku},
        )
        entry["rates"][token_type] = per_million_to_per_token(chosen.price_per_million)

    rows: list[SourceRow] = []
    unmapped: list[UnmappedEntry] = []
    candidates: list[UnmatchedCandidate] = []

    for (model_string, scope), entry in rows_by_model_scope.items():
        channel = f"aws_bedrock:{region}:{scope}"
        result = normalize(model_string, registry)

        if result.status == "matched":
            rows.append(
                SourceRow(
                    channel=channel,
                    model_id=result.model_id,
                    source="aws_price_list",
                    source_key=entry["servicename"],
                    rates=entry["rates"],
                )
            )
        elif result.status == "ambiguous":
            unmapped.append(
                UnmappedEntry(
                    source="aws_price_list",
                    source_key=entry["servicename"],
                    reason="model ambiguous",
                )
            )
        else:  # "unmatched" -- may still be promoted (§5.2), build.py decides
            candidates.append(
                UnmatchedCandidate(
                    source="aws_price_list",
                    model_string=model_string,
                    source_key=entry["servicename"],
                    channel=channel,
                    rates=entry["rates"],
                )
            )

    return rows, unmapped, candidates, warnings
