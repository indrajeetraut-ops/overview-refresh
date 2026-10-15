"""Integration-PR detection (docs/modules/pr-work-classification.md §5.8): an aggregate PR
(sprint→main, a promotion merge, a "PR of PRs") is not a unit of work -- its diff is huge and
classifying it is meaningless. Detected WITHOUT fetching that diff, from commit ownership
(§6.4), which this module already computes for the correlator (B2).

`eval_scope` (§3.1) already excludes most of these declaratively; this is the safety net for an
aggregate that still lands on an in-scope branch -- the residual case a branch-name filter alone
cannot catch (a `develop`→`main` merge on a repo where both happen to be in scope, say).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session as DbSession

from server.storage.queries import QueryCatalog

from .config import PrClassConfig
from .correlate import pick_owning_pr
from .scope import is_in_scope


def already_owned_ratio(s: DbSession, catalog: QueryCatalog, config: PrClassConfig,
                        pr: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """§5.8: the fraction of `pr`'s own `commit_shas` whose owning PR (§6.4 -- earliest-OPENED,
    `min(created_at)` with `merged_at` a secondary tiebreak, among in-scope PRs containing that
    sha) is a DIFFERENT PR. A commit this PR itself owns (no PR opened earlier contains it)
    does not count against it."""
    shas = pr.get("commit_shas") or []
    if not shas:
        return 0.0, {"total_commits": 0}

    pr_rows = list(s.execute(catalog.get("pr_class", "owning_prs_for_shas"), {"shas": shas}))
    repo_ids = list({row.repo_id for row in pr_rows} | {pr["repo_id"]})
    default_branches = {
        r.repo_id: r.default_branch
        for r in s.execute(catalog.get("pr_class", "repo_default_branches"), {"repo_ids": repo_ids})
    }

    owned_elsewhere = 0
    for sha in shas:
        candidates = [
            row for row in pr_rows
            if sha in (row.commit_shas or [])
            and is_in_scope(config.eval_scope, row.repo_id, default_branches.get(row.repo_id),
                            row.base_branch, row.branch)
        ]
        if not candidates:
            continue   # this PR itself is the (only) owner -- not "already owned"
        owner = pick_owning_pr([dict(c._mapping) for c in candidates])
        if owner["artifact_id"] != pr["artifact_id"]:
            owned_elsewhere += 1

    ratio = owned_elsewhere / len(shas)
    return ratio, {
        "already_owned_ratio": round(ratio, 3), "total_commits": len(shas),
        "already_owned_commits": owned_elsewhere,
    }


def detect_integration(s: DbSession, catalog: QueryCatalog, config: PrClassConfig,
                       pr: dict[str, Any]) -> dict[str, Any] | None:
    """Returns a `{label: "integration", confidence: 1.0, tier: 0, digest}`-shaped dict if `pr`
    clears the threshold, else `None`. Deliberately not a `Candidate` from `classify.py`: this
    check runs BEFORE tier 0/1/2 and, when it fires, is the final answer -- an aggregate's own
    title/labels/paths are not a more trustworthy signal than what its commit ownership already
    proves, so there is nothing to combine it with."""
    ratio, digest = already_owned_ratio(s, catalog, config, pr)
    if ratio >= config.integration.already_owned_ratio_threshold:
        return {"label": "integration", "confidence": 1.0, "tier": 0, "digest": digest}
    return None
