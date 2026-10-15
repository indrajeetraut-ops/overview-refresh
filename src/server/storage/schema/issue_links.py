"""pr_issue_links -- the PR<->issue linkage output (issue-tracker-ingestion.md §3.1, plan H3).

Owned here under storage.md §3.1's single-definition rule though designed in
`docs/design/issue-tracker-ingestion.md` and written by `src/server/ingest/git/apply.py` via
`linkage.resolve_links`. Entirely derived from a PR artifact's own fields (`closingIssuesReferences`,
commit messages, branch, title, body) -- nothing else writes into it incrementally, so its
upsert is a plain replace-on-conflict, the same posture as `session_git_links` (a logic fix is
a re-run, byte-identical result).

No FKs (storage.md §3.1): `pr_artifact_id` and `issue_artifact_id` are plain indexed columns
resolved against `git_artifacts` by the caller, never enforced -- a link can be recorded before
the issue it points at has been backfilled (`issue_artifact_id` stays NULL until then).
"""
from decimal import Decimal

from sqlalchemy import Index, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, ts


class PrIssueLink(Base):
    """One row per (PR artifact, issue ref). The PK is the tracker-agnostic issue key plus the
    PR, so re-resolving the same PR is idempotent by construction and a Jira id can never
    collide with a GitHub one (namespaced by `tracker`). `ix_pr_issue_links_issue` is the
    reverse index F4's feature grouping walks: issue ref -> its member PRs (plan H5)."""

    __tablename__ = "pr_issue_links"
    pr_artifact_id: Mapped[str] = mapped_column(primary_key=True)
    tracker: Mapped[str] = mapped_column(primary_key=True)          # "github" | "jira"
    project_key: Mapped[str] = mapped_column(primary_key=True)      # repo full_name/num · jira project key
    number: Mapped[int] = mapped_column(primary_key=True)           # issue / ticket number
    # Resolved entity, NULL for a referenced-but-not-pulled issue (a Jira ref today; a GitHub
    # issue whose backfill has not reached it). Filled by a later re-resolve or a read-time join.
    issue_artifact_id: Mapped[str | None] = mapped_column(index=True)
    method: Mapped[str]                                             # graphql|trailer|branch|title|body (§3.2)
    confidence: Mapped[Decimal] = mapped_column(Numeric)           # 1.0 graphql; <1 text heuristics
    created_at: Mapped[ts]

    __table_args__ = (
        Index("ix_pr_issue_links_issue", "tracker", "project_key", "number"),
    )
