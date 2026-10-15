"""Evaluation scope -- a configurable branch filter (docs/modules/pr-work-classification.md
§3.1). git-source acquires every PR; this gates only the EXPENSIVE work this module does --
correlation, classification, attribution, and rework -- so a promotion merge or a cherry-pick-
to-release never reaches the classifier. Shared by the correlator (B2) and the classifier
(B3-B5): both need the same in-scope test.
"""
from __future__ import annotations

import fnmatch

from .config import DEFAULT_BRANCH_SENTINEL, EvalScopeConfig


def _matches_any_glob(value: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(value, g) for g in globs)


def is_in_scope(config: EvalScopeConfig, repo_id: str, default_branch: str | None,
                base_branch: str | None, head_branch: str | None) -> bool:
    """A PR is in scope iff its base branch matches one of the configured (globbed) base
    branches for its repo, AND its head branch does not match an excluded pattern (promotion/
    port branches, §3.1). `"<default-branch>"` resolves per-repo to `git_repos.default_branch`
    -- a repo with no recorded default branch yet matches nothing rather than guessing."""
    override = config.per_repo.get(repo_id)
    base_branches = (
        override.base_branches if override and override.base_branches else config.base_branches
    )
    exclude_head = (
        override.exclude_head_branches
        if override and override.exclude_head_branches else config.exclude_head_branches
    )
    resolved = [default_branch if b == DEFAULT_BRANCH_SENTINEL else b for b in base_branches]
    resolved = [b for b in resolved if b is not None]
    if base_branch is None or not _matches_any_glob(base_branch, resolved):
        return False
    if head_branch and _matches_any_glob(head_branch, exclude_head):
        return False
    return True
