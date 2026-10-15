"""Deterministic integer allocation and independent relative weight jitter."""
from fractions import Fraction
from math import fsum, isfinite
from typing import Callable, Mapping
from random import Random


def allocate_integer(total: int, weights: Mapping[str, float]) -> dict[str, int]:
    """Conserve total using largest remainders with lexical tie breaking."""
    if type(total) is not int or total < 0:
        raise ValueError("total must be a nonnegative integer")
    if not weights or any(not isinstance(key, str) for key in weights):
        raise ValueError("weights must be a nonempty string-keyed mapping")
    fractions = {}
    for key in sorted(weights):
        value = weights[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
            raise ValueError("weights must be finite nonnegative numbers")
        fractions[key] = Fraction(str(value))
    denominator = sum(fractions.values())
    if denominator <= 0:
        raise ValueError("weights must have a positive sum")
    quotas = {key: total * value / denominator for key, value in fractions.items()}
    counts = {key: quota.numerator // quota.denominator for key, quota in quotas.items()}
    order = sorted(counts, key=lambda key: (-(quotas[key] - counts[key]), key))
    for key in order[:total - sum(counts.values())]:
        counts[key] += 1
    return counts


def vary_weights(weights: Mapping[str, float], variation_pct: float,
                 rng_by_key: Callable[[str], Random]) -> dict[str, float]:
    """Perturb positive weights only; sorted summation is independent of mapping order."""
    if isinstance(variation_pct, bool) or not isfinite(variation_pct) or not 0 <= variation_pct < 100:
        raise ValueError("variation must be between zero inclusive and 100 exclusive")
    varied = {
        key: (0.0 if weights[key] == 0 else weights[key] * (1 + rng_by_key(key).uniform(-variation_pct / 100, variation_pct / 100)))
        for key in sorted(weights)
    }
    total = fsum(varied.values())
    if total <= 0 or not isfinite(total) or any(value < 0 for value in varied.values()):
        raise ValueError("weights must have a finite positive sum")
    return {key: value / total for key, value in varied.items()}
