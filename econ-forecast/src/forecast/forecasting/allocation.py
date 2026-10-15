"""Joint agent/category shares and path summaries."""

import math
import numpy as np

TOKEN_TYPES = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")


def allocation(history):
    total = math.fsum(history.categories.values())
    if not total:
        return [], [], 0.0
    grouped = {}
    for (agent, cat), value in history.categories.items():
        grouped.setdefault(
            agent, {"agent_type": agent, **{c: 0.0 for c in TOKEN_TYPES}}
        )[cat] += value
    tokens = [grouped[k] for k in sorted(grouped)]
    shares = [
        {
            "agent_type": r["agent_type"],
            **{c: r[c] / total for c in TOKEN_TYPES},
        }
        for r in tokens
    ]
    return shares, tokens, total


def quantiles(paths):
    totals = np.sum(paths, axis=0)
    return float(np.mean(totals)), *(
        float(x) for x in np.quantile(totals, [0.1, 0.5, 0.9], method="linear")
    )
