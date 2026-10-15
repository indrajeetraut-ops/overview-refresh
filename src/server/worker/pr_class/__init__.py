"""PR work classification & attribution (docs/modules/pr-work-classification.md). Reads
git-source's `git_artifacts` and `sessions`/`session_info`; writes `session_git_links` and
`pr_classification`. Acquires nothing, stores no code -- see the module docstring in each
submodule for its slice of the design.
"""
from __future__ import annotations

from .config import PrClassConfig, load_config
from .correlate import correlate_session, git_correlate, make_git_correlate_handler
from .pr_classify import make_pr_classify_handler, pr_classify

__all__ = [
    "PrClassConfig", "correlate_session", "git_correlate", "load_config",
    "make_git_correlate_handler", "make_pr_classify_handler", "pr_classify",
]
