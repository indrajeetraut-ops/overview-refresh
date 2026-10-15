"""`model_id` normalization (design §5).

Three pure string functions (`norm`, `strip_date`, `canon`), a registry builder that
groups LiteLLM's vendor-channel entries into models (§5.2), and `normalize()`, the one
function used both to map source model names during `update` and to resolve an observed
usage string during cost lookup (§5.3) -- same rules, same code path, so a model that
`update` mapped is guaranteed resolvable later.

No fuzzy, prefix, or substring matching anywhere here: `claude-sonnet-4` and
`claude-sonnet-4-5` price differently and must never collapse into each other.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

#: Region prefixes `norm()` strips (§5.1). Order does not matter for correctness -- they
#: are mutually exclusive as string prefixes (e.g. "us-gov." and "us." never both match
#: the same input) -- but is kept longest-first for readability.
_REGION_PREFIXES = ("us-gov.", "global.", "apac.", "us.", "eu.", "au.", "jp.")

#: Vendor prefixes `norm()` strips, checked after the region prefix (§5.1).
_VENDOR_PREFIXES = ("anthropic.", "openai.")

#: `strip_date()` suffix patterns (§5.1), tried in this order every pass; the loop
#: restarts from the top after any strip so stacked suffixes (date then version, as in
#: `claude-haiku-4-5-20251001-v1:0`) are fully unwound regardless of which pattern fires
#: first.
_DATE_SUFFIX_RES = (
    re.compile(r"-\d{4}-\d{2}-\d{2}$"),  # -YYYY-MM-DD
    re.compile(r"-\d{8}$"),  # -YYYYMMDD
    re.compile(r"@\d{8}$"),  # @YYYYMMDD
    re.compile(r"@default$"),
    re.compile(r"-v\d+:\d+$"),  # -vN:N
    re.compile(r"-v\d+$"),  # -vN
)


def norm(x: str) -> str:
    """Lowercase; drop text up to the last `/`; drop a leading region token then a
    leading vendor token; `.` -> `-` (§5.1).

    `us.anthropic.claude-sonnet-5` -> `claude-sonnet-5`; `gpt-5.6-sol` -> `gpt-5-6-sol`.
    """
    s = x.lower()
    if "/" in s:
        s = s.rsplit("/", 1)[1]
    for prefix in _REGION_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    for prefix in _VENDOR_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    return s.replace(".", "-")


def strip_date(x: str) -> str:
    """Repeatedly drop a trailing date or version suffix (§5.1).

    `claude-haiku-4-5-20251001-v1:0` -> `claude-haiku-4-5` (two suffixes stripped in
    turn: `-v1:0`, then `-20251001`).
    """
    s = x
    changed = True
    while changed:
        changed = False
        for pattern in _DATE_SUFFIX_RES:
            stripped = pattern.sub("", s)
            if stripped != s:
                s = stripped
                changed = True
                break
    return s


def canon(x: str) -> str:
    """`strip_date(norm(x))` (§5.1). `gpt-5.5-2026-04-24` -> `gpt-5-5`."""
    return strip_date(norm(x))


#: The five rates, in a fixed order, used only as an equality signature when deciding
#: whether two source keys in the same canon group are "the same model" (§5.2). Order
#: must match `catalog.TOKEN_TYPES` conceptually but is private to this comparison --
#: nothing else depends on the tuple's shape.
_RATE_FIELDS = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")


@dataclass(frozen=True)
class RegistryCandidate:
    """One LiteLLM vendor-channel entry (litellm_provider in anthropic/openai/gemini),
    ready for grouping into the registry (§5.2). `rates` holds whichever of
    `_RATE_FIELDS` the source published, `None` for the rest -- two candidates with the
    same `rates` tuple are "the same model" for grouping purposes."""

    key: str
    vendor: str
    rates: tuple[Decimal | None, ...]


@dataclass(frozen=True)
class ModelEntry:
    vendor: str
    names_seen: tuple[str, ...]


@dataclass
class Registry:
    """`registry[model_id] = {vendor, names_seen}` (§5.2) plus the set of canon strings
    recorded ambiguous (§5.3 step 3)."""

    models: dict[str, ModelEntry] = field(default_factory=dict)
    ambiguous: set[str] = field(default_factory=set)

    def __contains__(self, model_id: str) -> bool:
        return model_id in self.models

    def vendor_of(self, model_id: str) -> str:
        return self.models[model_id].vendor

    def candidates_for_canon(self, canon_str: str) -> list[str]:
        """`model_id`s sharing `canon_str` (§5.3 step 3) -- the ambiguous family a
        caller can report back to a user who needs to pick the exact observed model
        string (rate-sheet `candidates`, model-pricing design §4). Recomputed from
        `models` rather than cached, same reasoning as `catalog._build_normalize_registry`:
        it always agrees with the registry it describes."""
        return sorted(model_id for model_id in self.models if canon(model_id) == canon_str)


def build_registry(candidates: list[RegistryCandidate]) -> Registry:
    """Group LiteLLM vendor-channel entries by `canon` (§5.2).

    Within a canon group: identical `rates` across every candidate -> one model,
    `model_id = canon(key)`. Any difference -> each candidate becomes its own model,
    `model_id = norm(key)`, and the group's canon string is recorded ambiguous. Real
    case: `gpt-4o`, `gpt-4o-2024-05-13`, `gpt-4o-2024-08-06`, `gpt-4o-2024-11-20` share
    canon `gpt-4o` but `gpt-4o-2024-05-13` prices differently -> four models, `gpt-4o`
    (the ambiguous canon) recorded ambiguous -- and also, unavoidably, itself one of the
    four `model_id`s, since the bare key's own `norm()` is `gpt-4o` too. `normalize()`'s
    step order (§5.3) is what makes that coexistence resolve correctly: a direct
    `model_id` hit always wins before the ambiguous set is even consulted.
    """
    by_canon: dict[str, list[RegistryCandidate]] = defaultdict(list)
    for c in candidates:
        by_canon[canon(c.key)].append(c)

    models: dict[str, ModelEntry] = {}
    ambiguous: set[str] = set()

    for canon_key, group in by_canon.items():
        distinct_rates = {c.rates for c in group}
        if len(distinct_rates) == 1:
            names = tuple(sorted({c.key for c in group}))
            models[canon_key] = ModelEntry(vendor=group[0].vendor, names_seen=names)
            continue

        ambiguous.add(canon_key)
        for c in group:
            model_id = norm(c.key)
            if model_id in models:
                existing = models[model_id]
                models[model_id] = ModelEntry(
                    vendor=existing.vendor,
                    names_seen=tuple(sorted(set(existing.names_seen) | {c.key})),
                )
            else:
                models[model_id] = ModelEntry(vendor=c.vendor, names_seen=(c.key,))

    return Registry(models=models, ambiguous=ambiguous)


@dataclass(frozen=True)
class NormalizeResult:
    status: str  # "matched" | "ambiguous" | "unmatched"
    model_id: str | None
    step: str | None  # "alias" | "normalized" | "ambiguous" | "canonical" | None
    #: Other `model_id`s sharing the ambiguous canon (§5.3 step 3); empty unless
    #: `status == "ambiguous"`. Additive field -- does not change matching behaviour.
    candidates: tuple[str, ...] = ()


def normalize(
    x: str, registry: Registry, aliases: dict[str, str] | None = None
) -> NormalizeResult:
    """`x` -> a `model_id`, per §5.3's exact step order:

    1. `aliases[x]` exists -> that `model_id` (`alias`).
    2. `norm(x)` is a registry `model_id` -> it (`normalized`).
    3. `canon(x)` is a recorded-ambiguous canon -> **ambiguous**. Checked *before* step 4
       so an unseen dated variant of an ambiguous family (e.g. `gpt-4o-2025-06-01`, no
       exact registry entry) comes back ambiguous rather than silently matching the
       family's bare-key model -- but a direct hit at step 2 (e.g. `gpt-4o` itself, or
       `gpt-4o-2024-08-06`) already returned before this step runs.
    4. `canon(x)` is a registry `model_id` -> it (`canonical`).
    5. Else **unmatched**.
    """
    aliases = aliases or {}
    if x in aliases:
        return NormalizeResult("matched", aliases[x], "alias")

    n = norm(x)
    if n in registry:
        return NormalizeResult("matched", n, "normalized")

    c = canon(x)
    if c in registry.ambiguous:
        return NormalizeResult(
            "ambiguous", None, "ambiguous", candidates=tuple(registry.candidates_for_canon(c))
        )

    if c in registry:
        return NormalizeResult("matched", c, "canonical")

    return NormalizeResult("unmatched", None, None)
