"""`utils/fit_prices.py`: the exact solver and the pure fit-vs-catalog comparison (no DB)."""
import importlib.util
import random
from datetime import date
from decimal import Decimal
from pathlib import Path

from common.model_pricing import Pricer

_spec = importlib.util.spec_from_file_location(
    "fit_prices", Path(__file__).resolve().parents[2] / "utils" / "fit_prices.py")
fit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fit)

AT = date(2026, 10, 1)


def _synthetic(model_rates_per_m, n=12, seed=3):
    """Requests with varied token mixes, costed exactly at the given $/M rates (micros)."""
    rng = random.Random(seed)
    rows = [[rng.randint(1, 5000), rng.randint(1, 3000), rng.randint(0, 90000),
             rng.randint(0, 40000)] for _ in range(n)]
    costs = [sum(t * r for t, r in zip(row, model_rates_per_m)) for row in rows]
    return rows, costs


def test_solve_recovers_known_rates_exactly():
    rows, costs = _synthetic([5, 25, 0.5, 10])
    rates = fit.solve(rows, [round(c) for c in costs])
    assert [round(r, 2) for r in rates] == [5.0, 25.0, 0.5, 10.0]


def test_solve_returns_none_when_a_kind_never_varies():
    rows = [[1, 1, 0, 0]] * 8
    assert fit.solve(rows, [30] * 8) is None


def test_compare_against_the_catalog_gives_ratio_one_per_kind():
    pricer = Pricer.load(None, None)
    rows, costs = _synthetic([5, 25, 0.5, 10])
    rates = fit.solve(rows, costs)
    comparisons = fit.compare("claude-opus-5", rates, pricer, AT)
    assert [c["kind"] for c in comparisons] == list(fit.KINDS)
    for c in comparisons:
        assert round(c["ratio"], 3) == Decimal("1.000"), c
    assert comparisons[3]["catalog"] == Decimal("10")           # cache_write -> 1h rate


def test_compare_flags_a_price_difference():
    pricer = Pricer.load(None, None)
    rows, costs = _synthetic([10, 50, 1, 20])                    # exactly 2x opus-5
    comparisons = fit.compare("claude-opus-5", fit.solve(rows, costs), pricer, AT)
    assert all(round(c["ratio"], 3) == Decimal("2.000") for c in comparisons)


def test_compare_an_unknown_model_has_no_catalog_rate():
    pricer = Pricer.load(None, None)
    comparisons = fit.compare("no-such-model", [1.0, 2.0, 3.0, 4.0], pricer, AT)
    assert all(c["catalog"] is None and c["ratio"] is None for c in comparisons)
    assert "no-such-model" in fit.render(comparisons, 6, 0.0)


def test_docstring_no_longer_mentions_the_legacy_table():
    assert "config/pricing" not in fit.__doc__
