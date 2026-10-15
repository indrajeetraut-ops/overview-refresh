"""Azure Retail Prices source adapter (design §5.3, §6.2-§6.4).

Azure Retail Prices lists every Foundry Models meter under one `productName` per
generation (`Azure OpenAI GPT5`); the model's actual version, and every other axis
(context length, tier, cache role, scope), all live in `meterName` as a run of
whitespace-separated tokens. `_parse_meter` reads that run left to right: tokens before
the first recognized keyword are the "leading version tokens" that combine with the
generation prefix into a model string (§5.3's `GPT5` + `5.6 sol` -> `gpt-5.6-sol`);
everything from the first keyword on is classified as context (`ShortCo`/`LongCo`),
tier (`Std`/`PP`/`Batch`), price type (`Inp`/`Opt`/`Cd Inp`/`Cd Wr`), or scope
(`Gl`/`DZ`), all case-insensitively (§6.3).

`fetch()` is the only network-touching function; `parse()` is pure and is what
`test_source_azure.py` calls directly on the captured fixture pages (§10.1).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from common.model_pricing.catalog import SourceRow, UnmappedEntry, UnmatchedCandidate
from common.model_pricing.decimals import per_million_to_per_token
from common.model_pricing.normalize import Registry, normalize

RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
_SERVICE_NAME = "Foundry Models"

#: `productName` this adapter reads: `"Azure OpenAI GPT<n>"` (§5.3). The trailing digits
#: are not carried into the model string -- the meter's own leading version tokens
#: supply the full version (`GPT5` + `5.6 sol` -> `gpt-5.6-sol`, not `gpt5-5.6-sol`);
#: the productName match only confirms this is a GPT-family listing at all. Anything
#: else (e.g. an image-generation product) is `unmapped` as a non-GPT product.
_PRODUCT_RE = re.compile(r"^Azure OpenAI GPT\d+$")

#: Kept vs. ignored context token (§6.3's "kept: ShortCo with Std or no tier token").
_CONTEXT_TOKENS = {"shortco": "shortco", "longco": "longco"}

#: Tier tokens dropped outright (§6.3): `PP` and `Batch`. `Std` is kept explicitly; the
#: absence of any tier token is *also* standard (§5.3/§6.3 "Std or no tier token").
_TIER_DROP_TOKENS = {"pp", "batch"}

#: `Gl` -> `global`, `DZ`/`Dz` -> `data_zone` (§6.4); matched case-insensitively.
_SCOPE_TOKENS = {"gl": "global", "dz": "data_zone"}

#: Single-token price types (§6.3).
_PRICE_SINGLE_TOKENS = {"inp": "input", "opt": "output"}

#: `Cd Inp` -> cache_read, `Cd Wr` -> cache_write_5m (§6.3): two-token combos, the first
#: token always `cd`.
_CD_SECOND_TOKENS = {"inp": "cache_read", "wr": "cache_write_5m"}

#: Trailing tokens that carry no meaning for this adapter (the unit and its label are
#: read from `unitOfMeasure`, not from the meterName text) but must still be recognized
#: so they do not get swept into the leading version tokens.
_TRAILING_TOKENS = {"1m", "1k", "tokens"}

_KNOWN_TOKENS = (
    set(_CONTEXT_TOKENS)
    | {"std"}
    | _TIER_DROP_TOKENS
    | set(_SCOPE_TOKENS)
    | set(_PRICE_SINGLE_TOKENS)
    | {"cd"}
    | _TRAILING_TOKENS
)

_UNIT_1M = "1M"


def fetch(
    region: str, *, _transport: httpx.BaseTransport | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Download every Foundry Models item for `region` (§6.2), following
    `NextPageLink` until it is null.

    `_transport`: injection seam for tests (an `httpx.MockTransport`); real callers
    leave it `None` and get the default network transport.
    """
    filt = (
        f"serviceName eq '{_SERVICE_NAME}' and armRegionName eq '{region}' "
        "and contains(productName, 'Azure OpenAI')"
    )
    items: list[dict[str, Any]] = []
    url: str | None = RETAIL_PRICES_URL
    params: dict[str, Any] | None = {"$filter": filt}
    with httpx.Client(timeout=30.0, transport=_transport) as client:
        while url:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            items.extend(payload.get("Items", []))
            # NextPageLink is already a full URL with its own query string -- no
            # params on the follow-up request.
            url = payload.get("NextPageLink") or None
            params = None

    provenance = {"fetched_at": datetime.now(timezone.utc).isoformat(), "region": region}
    return items, provenance


class _MeterParse:
    __slots__ = ("version_tokens", "context", "tier_drop", "scope", "price_type")

    def __init__(
        self,
        version_tokens: list[str],
        context: str | None,
        tier_drop: bool,
        scope: str | None,
        price_type: str | None,
    ) -> None:
        self.version_tokens = version_tokens
        self.context = context
        self.tier_drop = tier_drop
        self.scope = scope
        self.price_type = price_type


def _parse_meter(meter_name: str) -> _MeterParse | None:
    """`meterName` -> its parsed axes, or `None` if it has no leading version tokens at
    all (e.g. an empty or malformed string). Tokens are matched case-insensitively
    (§6.3: `cd inp` == `Cd Inp`); `version_tokens` keeps original casing since it feeds
    the model string that `normalize()` itself lowercases."""
    tokens = meter_name.split()
    lower = [t.lower() for t in tokens]

    i = 0
    version_tokens: list[str] = []
    while i < len(lower) and lower[i] not in _KNOWN_TOKENS:
        version_tokens.append(tokens[i])
        i += 1
    if not version_tokens:
        return None

    rest = lower[i:]

    context = None
    for tok, name in _CONTEXT_TOKENS.items():
        if tok in rest:
            context = name
            break

    tier_drop = any(tok in _TIER_DROP_TOKENS for tok in rest)

    scope = None
    for tok, name in _SCOPE_TOKENS.items():
        if tok in rest:
            scope = name
            break

    price_type = None
    for idx, tok in enumerate(rest):
        if tok == "cd" and idx + 1 < len(rest) and rest[idx + 1] in _CD_SECOND_TOKENS:
            price_type = _CD_SECOND_TOKENS[rest[idx + 1]]
            break
        if tok in _PRICE_SINGLE_TOKENS:
            price_type = _PRICE_SINGLE_TOKENS[tok]
            break

    return _MeterParse(
        version_tokens=version_tokens,
        context=context,
        tier_drop=tier_drop,
        scope=scope,
        price_type=price_type,
    )


def _model_string(parsed: _MeterParse) -> str:
    """`GPT5` (from `productName`, already confirmed GPT-family by `_PRODUCT_RE` --
    its digits are not carried into the model string) + `5.6 sol` (leading meterName
    tokens) -> `gpt-5.6-sol` (§5.3). Case is left as-is; `normalize()` lowercases."""
    return "gpt-" + "-".join(parsed.version_tokens)


def parse(
    items: list[dict[str, Any]], region: str, registry: Registry
) -> tuple[list[SourceRow], list[UnmappedEntry], list[UnmatchedCandidate], list[str]]:
    """Items -> `SourceRow`s for `azure:<region>:<global|data_zone>` (§6.4).

    Returns `(rows, unmapped, candidates, warnings)`. `LongCo`/`Batch`/`PP` meters are
    silently skipped (kept out of `unmapped`), exactly like AWS's `*Batch*` and
    LiteLLM's `*_above_200k_tokens` (§6.3's "ignored" lists are not failures). A
    non-GPT product, an unparseable meter, or a supported meter published in a unit
    other than `1M` -> `unmapped` with a reason immediately (none of these are about
    registry matching). A `(model_string, channel)` group missing `input` or `output`
    once every kept meter has been read -> `unmapped` too (§6.4: "a row needs input
    and output to be emitted") -- checked before the registry lookup below, since an
    incomplete group can never become a valid row or candidate either way.

    Registry matching is deferred to the very end, once each group's rates are fully
    resolved, exactly like `aws_price_list.parse` (§5.2 "official-source models" needs
    the resolved rates before knowing whether the model is in the registry): `matched`
    -> a row; `ambiguous` -> `unmapped` immediately (already a known-but-ambiguous
    LiteLLM family, not a new model); `unmatched` -> an `UnmatchedCandidate`, which
    `build.py`'s promotion pass may still turn into a registry entry and a row.
    """
    # (model_string, channel) -> {"rates": {token_type: Decimal}, "source_key": str}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    unmapped: list[UnmappedEntry] = []
    warnings: list[str] = []
    seen_unmapped: set[str] = set()

    def _unmap(product_name: str, meter_name: str, reason: str) -> None:
        key = f"{product_name} | {meter_name}"
        if key in seen_unmapped:
            return
        seen_unmapped.add(key)
        unmapped.append(UnmappedEntry(source="azure_retail", source_key=key, reason=reason))

    for item in items:
        if item.get("armRegionName") != region:
            continue

        product_name = item.get("productName", "")
        meter_name = item.get("meterName", "")

        if not _PRODUCT_RE.match(product_name):
            _unmap(product_name, meter_name, "non-GPT product")
            continue

        parsed = _parse_meter(meter_name)
        if parsed is None or parsed.context is None or parsed.price_type is None or parsed.scope is None:
            _unmap(product_name, meter_name, "unparseable meter")
            continue

        if parsed.context != "shortco":
            continue  # LongCo -- ignored, not unmapped (§6.3)
        if parsed.tier_drop:
            continue  # PP / Batch -- ignored, not unmapped (§6.3)

        if item.get("unitOfMeasure") != _UNIT_1M:
            _unmap(product_name, meter_name, f"unit {item.get('unitOfMeasure')!r} not supported")
            continue

        try:
            price = Decimal(str(item.get("retailPrice")))
        except InvalidOperation:
            _unmap(product_name, meter_name, "invalid retailPrice")
            continue

        model_string = _model_string(parsed)
        channel = f"azure:{region}:{parsed.scope}"
        group_key = (model_string, channel)
        rate = per_million_to_per_token(price)

        group = groups.get(group_key)
        if group is None:
            group = {"rates": {}, "source_key": meter_name}
            groups[group_key] = group

        prev = group["rates"].get(parsed.price_type)
        if prev is not None and prev != rate:
            warnings.append(
                f"azure_retail: {model_string} ({channel}) {parsed.price_type}: "
                f"conflicting rates {prev} vs {rate} from meter {meter_name!r} -- "
                "keeping the first"
            )
        else:
            group["rates"][parsed.price_type] = rate

    rows: list[SourceRow] = []
    candidates: list[UnmatchedCandidate] = []
    for (model_string, channel), group in groups.items():
        rates = group["rates"]
        if rates.get("input") is None or rates.get("output") is None:
            # Generic reason text (matches litellm/aws_price_list's own `f"model
            # {result.status}"`, §7): `model_string`/`channel` move into `source_key`
            # (alongside the bare meter name), keeping `reason` a stable, groupable
            # category rather than a per-record string.
            unmapped.append(
                UnmappedEntry(
                    source="azure_retail",
                    source_key=f"{group['source_key']} [{model_string}, {channel}]",
                    reason="missing input or output rate",
                )
            )
            continue

        result = normalize(model_string, registry)
        if result.status == "matched":
            rows.append(
                SourceRow(
                    channel=channel,
                    model_id=result.model_id,
                    source="azure_retail",
                    source_key=group["source_key"],
                    rates=rates,
                )
            )
        elif result.status == "ambiguous":
            unmapped.append(
                UnmappedEntry(
                    source="azure_retail", source_key=group["source_key"], reason="model ambiguous"
                )
            )
        else:  # "unmatched" -- may still be promoted (§5.2), build.py decides
            candidates.append(
                UnmatchedCandidate(
                    source="azure_retail",
                    model_string=model_string,
                    source_key=group["source_key"],
                    channel=channel,
                    rates=rates,
                )
            )

    return rows, unmapped, candidates, warnings
