"""Attribution and rollup (docs/modules/pr-work-classification.md §6, §8): cost per change-
type, per artifact-class, per grouping key, and rework cost netted in. **Deduped per owning-PR
happens upstream, at correlation time (B2, §6.4)** -- a commit riding several PRs never gets
more than one session_git_links row to begin with. What THIS module additionally guards
against is a different double-count: one SESSION touching several PRs that happen to share the
same dimension value (two "fix" PRs, say) must still contribute its cost only once to that
dimension's bucket -- every `rollup_by_*` query in `config/queries/pr_class.yaml` does this via
an inner `DISTINCT (session_id, dimension_value)` before summing
`session_info.cost_effective_usd_micros` (otel_trueup.py's ledger -- populated for every
session regardless of datasource, including `jsonl_only`; `session_info.cost_usd` is a
documented dead column the JSONL fold never writes, see `fold/driver.py`).

ROI proxy (§6.1's `value(work shipped) / cost`) is deliberately **not computed**: this system
has no signal for "value shipped" to divide by (no revenue/business-value input exists
anywhere upstream), so a number here would be fabricated, not honest. Revisit if/when such a
signal exists.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from server.storage.queries import QueryCatalog

#: The dimensions `rollup_by` can group on -- each maps to the query.yaml name and the column
#: the query returns that value under.
_DIMENSION_QUERIES = {
    "change_type": ("rollup_by_change_type", "change_type"),
    "artifact_class": ("rollup_by_artifact_class", "artifact_class"),
    "module": ("rollup_by_module", "module"),
    "repo": ("rollup_by_repo", "repo_id"),
    "user": ("rollup_by_user", "user_id"),
    "label": ("rollup_by_label", "label"),
}


def rollup_by(s: DbSession, catalog: QueryCatalog, dimension: str) -> list[dict[str, Any]]:
    """Cost + session count grouped by one dimension (`"change_type"`, `"artifact_class"`,
    `"module"`, `"repo"`, `"user"`, or `"label"`) -- every plausible grouping key git-source
    already captures (§6.2), so this is always a `GROUP BY`, never a re-collection."""
    if dimension not in _DIMENSION_QUERIES:
        raise ValueError(f"unknown rollup dimension {dimension!r}; one of {sorted(_DIMENSION_QUERIES)}")
    query_name, value_col = _DIMENSION_QUERIES[dimension]
    rows = s.execute(catalog.get("pr_class", query_name))
    return [
        {"value": getattr(r, value_col), "session_count": r.session_count,
         "total_cost_usd": r.total_cost_usd}
        for r in rows
    ]


def rework_cost_total(s: DbSession, catalog: QueryCatalog) -> Decimal | None:
    """Σ (attributed PR cost × rework_ratio) (§6.1). `None` -- not `0` -- until the sibling
    rework module has computed at least one PR's `rework_ratio`, so a caller can tell "no
    rework data yet" from "computed, and it nets to zero."""
    row = s.execute(catalog.get("pr_class", "rework_cost_total")).first()
    return row[0] if row else None


def coverage_by_confidence_band(s: DbSession, catalog: QueryCatalog) -> dict[str, int]:
    """Correlation coverage bucketed by match confidence (§7): `"exact"` (trailer/sha, 1.0),
    `"high"` (>=0.7, presently unreachable -- the heuristic's own floor is 0.3, §4.2's own
    "flagged lower-confidence"), `"low"` (heuristic, <0.7)."""
    rows = s.execute(catalog.get("pr_class", "correlation_coverage_by_confidence_band"))
    return {r.confidence_band: r.link_count for r in rows}


def refresh_all_session_pr_lists(s: DbSession, catalog: QueryCatalog) -> int:
    """Bulk re-derive `session_info.pr_list` for every session that has ANY correlation link --
    `correlate_session` already refreshes one session inline right after (re-)linking it; this
    is for a backfill/repair pass (a classifier logic fix changed `change_type` for existing
    links, say, and `pr_list`'s denormalized copy needs to catch up)."""
    ids = [r[0] for r in s.execute(text("SELECT DISTINCT session_id FROM session_git_links"))]
    for session_id in ids:
        s.execute(catalog.get("pr_class", "refresh_session_pr_list"), {"session_id": session_id})
    return len(ids)
