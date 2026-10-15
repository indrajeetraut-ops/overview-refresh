"""Verify the model_pricing catalog against vendor-reported costs.

Not a service and not a test -- a developer utility. LiteLLM (via the `common.model_pricing`
catalog) is the price authority; this tool does not write prices anywhere. It fits per-model
rates from the requests the vendor itself priced and prints, per model and token kind, the
fitted rate next to the catalog's rate and their ratio -- a verification that the catalog
describes what the vendor actually billed:

    python utils/fit_prices.py                      # every model with enough data
    python utils/fit_prices.py --model claude-opus-5
    python utils/fit_prices.py --dsn postgresql+psycopg://postgres@localhost:5433/econ
    python utils/fit_prices.py --org acme --version 2026-09-25-d3e5c412

**Why fit rather than look up.** `otel_api_requests` holds, for every request the vendor
priced, both the exact token counts and the exact cost. Four token kinds and one cost is one
linear equation per request; a few dozen requests with varied token mixes over-determine the
four rates. On real data the fit reproduces the price list to the micro, because that is the
arithmetic the vendor itself ran. A ratio of 1.000 means catalog and vendor agree; anything
else is a price change, a discount, a batch tier or a catalog gap (the same signal
`otel_trueup`'s drift check raises continuously).

The solver stays exact (`Fraction`), so every fitted number can be re-derived by hand. The
fitted `cache_write` rate is the 1-hour rate -- OTel reports one combined write figure, which
the pricing path also charges at 1h -- so it is compared with the catalog's `cache_write_1h`.
Catalog rates are looked up at the date of the model's latest priced request, on the channel
the `--org` buys the model through (vendor channels when unset).
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict
from datetime import date
from decimal import Decimal
from fractions import Fraction

from sqlalchemy import create_engine, text

from common.model_pricing import Pricer

#: Column order of the fit. Matches TOKEN_KINDS minus the TTL split, which OTel does not report.
KINDS = ("input", "output", "cache_read", "cache_write")

#: Fewer observations than unknowns cannot determine the rates; a handful more gives the
#: residual something to be small against.
MIN_ROWS = 6

QUERY = text("""
    SELECT model, input_tokens, output_tokens, cache_read_tokens,
           cache_creation_tokens, cost_usd_micros, event_ts, received_at
    FROM otel_api_requests
    WHERE cost_usd_micros IS NOT NULL AND model IS NOT NULL
      AND (CAST(:model AS text) IS NULL OR model = CAST(:model AS text))
""")


def solve(rows: list[list[int]], costs: list[int]) -> list[float] | None:
    """Least squares over the normal equations, in exact `Fraction` arithmetic.

    Fractions rather than floats, and hand-rolled rather than numpy: the whole point is a
    number someone can check, and adding a numerical-linear-algebra dependency to a four-by-four
    solve would be the tail wagging the dog.
    """
    n = len(KINDS)
    matrix = [
        [sum(Fraction(rows[k][i]) * rows[k][j] for k in range(len(rows))) for j in range(n)]
        + [sum(Fraction(rows[k][i]) * costs[k] for k in range(len(rows)))]
        for i in range(n)
    ]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(matrix[r][col]))
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        if matrix[col][col] == 0:
            return None                      # a token kind never varied: underdetermined
        for row in range(n):
            if row != col and matrix[row][col] != 0:
                factor = matrix[row][col] / matrix[col][col]
                matrix[row] = [matrix[row][j] - factor * matrix[col][j] for j in range(n + 1)]
    return [float(matrix[i][n] / matrix[i][i]) for i in range(n)]


#: Fitted kind -> the catalog token type it is compared with.
_CATALOG_KIND = {"input": "input", "output": "output", "cache_read": "cache_read",
                 "cache_write": "cache_write_1h"}


def compare(model: str, fitted: list[float], pricer, at: date) -> list[dict]:
    """Fitted rate vs catalog rate per kind, both in USD per million tokens (the fit's unit:
    micros per token). Pure: no DB. `ratio` is fitted / catalog, `None` when the catalog has
    no rate for that kind or the model does not resolve."""
    rates = pricer.rates(model, at)
    out = []
    for kind, fit in zip(KINDS, fitted):
        catalog = None
        if rates is not None and rates.get(_CATALOG_KIND[kind]) is not None:
            catalog = rates[_CATALOG_KIND[kind]] * 1_000_000
        ratio = None if not catalog else Decimal(str(fit)) / catalog
        out.append({"model": model, "kind": kind, "fitted": fit,
                    "catalog": catalog, "ratio": ratio})
    return out


def render(comparisons: list[dict], n: int, max_residual: float) -> str:
    lines = [f"### {comparisons[0]['model']}  (n={n}, max residual {max_residual:.3f} micros)",
             "", "| kind | fitted $/M | catalog $/M | ratio |", "|---|---|---|---|"]
    for c in comparisons:
        catalog = "-" if c["catalog"] is None else f"{c['catalog']:.4f}"
        ratio = "-" if c["ratio"] is None else f"{c['ratio']:.3f}"
        lines.append(f"| {c['kind']} | {c['fitted']:.4f} | {catalog} | {ratio} |")
    return "\n".join(lines)


def max_residual(rates: list[float], rows: list[list[int]], costs: list[int]) -> float:
    return max(abs(sum(rows[k][i] * rates[i] for i in range(len(KINDS))) - costs[k])
               for k in range(len(rows)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dsn", default=os.environ.get(
        "TALIUM_DB_DSN", "postgresql+psycopg://postgres@localhost:5433/econ"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--org", default=None, help="org id for channel selection (default: vendor channels)")
    parser.add_argument("--version", default=None, help="catalog version (default: current)")
    args = parser.parse_args()

    pricer = Pricer.load(args.org, args.version)
    by_model: dict[str, list[list[int]]] = defaultdict(list)
    latest: dict[str, date] = {}
    with create_engine(args.dsn).connect() as conn:
        for row in conn.execute(QUERY, {"model": args.model}):
            by_model[row.model].append([
                row.input_tokens or 0, row.output_tokens or 0,
                row.cache_read_tokens or 0, row.cache_creation_tokens or 0,
                row.cost_usd_micros,
            ])
            when = (row.event_ts or row.received_at).date()
            latest[row.model] = max(latest.get(row.model, when), when)

    if not by_model:
        print("no vendor-priced requests found -- nothing to fit")
        return 1

    print(f"## Fit vs catalog {pricer.fingerprint}\n")
    for model, data in sorted(by_model.items()):
        rows, costs = [d[:4] for d in data], [d[4] for d in data]
        if len(data) < MIN_ROWS:
            print(f"- {model}: only {len(data)} priced request(s); need >= {MIN_ROWS}")
            continue
        rates = solve(rows, costs)
        if rates is None or any(r < 0 for r in rates):
            # A negative rate is the fit telling you the inputs are collinear (e.g. every
            # request had the same token mix), not that the vendor pays you.
            print(f"- {model}: underdetermined -- token mixes too uniform to separate kinds")
            continue
        print(render(compare(model, rates, pricer, latest[model]), len(rows),
                     max_residual(rates, rows, costs)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
