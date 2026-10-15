"""Canonical decimal-string formatting for rates (design §3.3, §11).

One function, used by every source adapter and by `catalog.RateRow` -- so the same
dollars-per-million figure always produces the same on-disk string, whichever source it
came from. Not in the design's §10 file list (which enumerates the *package's* modules,
not every private helper); it is split out because `catalog.py`, every `sources/*.py`
adapter, and `build.py`'s derive step all need it, and duplicating it risks the two
copies drifting (one using `str(Decimal)`, which silently flips to scientific notation
for small magnitudes -- see the module docstring below).
"""
from decimal import Decimal

#: Every published rate is dollars per one million tokens; the table stores USD per one
#: token (§3.3 `"unit": "usd_per_token"`).
TOKENS_PER_UNIT = Decimal(1_000_000)


def to_rate_str(rate: Decimal) -> str:
    """`Decimal` USD-per-token -> its canonical JSON string.

    `format(d, 'f')` is load-bearing: plain `str(Decimal("0.20") / Decimal(1_000_000))`
    is `"2E-7"` (scientific notation) for small magnitudes, which does not match a single
    string in the design's own worked examples (§3.2, §3.3 -- e.g. `"cache_read":
    "0.00000022"`). The `'f'` format spec forces fixed-point regardless of exponent.

    `normalize()` first drops trailing zeros: AWS publishes `2.2000000000`, LiteLLM
    `2.2e-06`; both are the same price and must produce the same string, or the version
    hash and the diffs would change with the source's formatting rather than its price.
    """
    return format(rate.normalize(), "f")


def per_million_to_per_token(dollars_per_million: Decimal | str | float | int) -> Decimal:
    """A source's dollars-per-million figure -> USD per token, exactly.

    `Decimal(str(x))` first, same reasoning as the catalog's `_exact` validator:
    a float carries its binary fraction into money if handed to `Decimal` directly, and
    every source (LiteLLM's JSON floats, AWS/Azure's decimal strings) must go through
    this one conversion so a `2.20` from any of them becomes the same `Decimal`.
    """
    d = dollars_per_million if isinstance(dollars_per_million, Decimal) else Decimal(
        str(dollars_per_million)
    )
    return d / TOKENS_PER_UNIT
