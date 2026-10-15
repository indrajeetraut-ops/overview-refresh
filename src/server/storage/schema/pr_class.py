"""session_git_links / pr_classification -- pr-work-classification.md's two derived tables
(design §8), owned here under storage.md §3.1's single-definition rule though designed and
written by `src/server/worker/pr_class/`.

Both tables are entirely computed BY this module from durable inputs (`git_artifacts`,
`sessions`) -- unlike git-source's tables, nothing else writes into them incrementally, so
their upserts are plain replace-on-conflict (design §7: "a logic fix is a re-run, byte-identical
result"). The one exception is `pr_classification.rework_ratio`, owned by the sibling rework
module (`docs/design/rework-measurement-method.md`, not built here) -- this module's own
classify pass always writes it `None` and must not clobber a value that module already joined
in, so its upsert preserves the existing value the same way git-source's A4 fix does for a
cross-module field (`config/queries/pr_class.yaml`'s `upsert_pr_classification`).

No FKs on the write path (storage.md §3.1): a plain indexed `artifact_id` column, resolved
against `git_artifacts` by the caller, never enforced in the schema.
"""
from decimal import Decimal

from sqlalchemy import Index, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, ts


class SessionGitLink(Base):
    """The correlation output (design §4, §8) -- one row per (session, artifact) link. A
    session commonly links to several artifacts (each commit it produced, and/or the owning
    PR); `PRIMARY KEY (session_id, artifact_id)` makes re-correlating the same pair idempotent
    by construction, and `(artifact_id)` is the reverse index a PR's attribution rollup walks
    ("which sessions fed this PR")."""

    __tablename__ = "session_git_links"
    session_id: Mapped[str] = mapped_column(primary_key=True)
    artifact_id: Mapped[str] = mapped_column(primary_key=True)
    method: Mapped[str]                                    # "trailer" | "sha" | "heuristic" (§4.2)
    confidence: Mapped[Decimal] = mapped_column(Numeric)   # 1.0 for trailer/sha; <1 for heuristic
    signals: Mapped[jsb]                                   # what matched: the sha, or file/time/author evidence
    created_at: Mapped[ts]

    __table_args__ = (
        Index("ix_session_git_links_artifact_id", "artifact_id"),
    )


class PrClassification(Base):
    """One row per classified work unit (design §5, §8) -- a merged PR, or a fallback
    merge-commit artifact for a repo with no PR workflow (§6.3). **Never holds code or
    prose** -- only labels, confidences, counts/ratios, and a non-reversible features digest
    (design §5.9)."""

    __tablename__ = "pr_classification"
    artifact_id: Mapped[str] = mapped_column(primary_key=True)
    change_type: Mapped[str] = mapped_column(index=True)   # feature|fix|refactor|docs|test|chore|revert|integration|unknown
    change_confidence: Mapped[Decimal] = mapped_column(Numeric)
    change_tier: Mapped[int]                                # which tier produced the winning label (0|1|2)
    artifact_class: Mapped[str] = mapped_column(index=True)  # "coding" | "documentation" | "mixed" (§5.4)
    is_revert: Mapped[bool]                                 # structural revert flag (§5.6)
    is_recall: Mapped[bool]                                 # closed AND not merged -- recall candidate (§5.6)
    recall_reason: Mapped[str | None]                       # opt-in enrichment; text itself never stored (§5.6)
    recall_confidence: Mapped[Decimal | None] = mapped_column(Numeric)
    module: Mapped[str | None] = mapped_column(index=True)  # config path->module map (§6.2)
    # Owned by the sibling rework module; this module's own writes always pass None here and
    # the query-layer COALESCE preserves whatever that module already joined in (§5.7).
    rework_ratio: Mapped[Decimal | None] = mapped_column(Numeric)
    features_digest: Mapped[jsb]                            # non-reversible counts/ratios -- never code/prose
    analyzer: Mapped[str]                                   # "heuristic" (v1) | "llm" (future, §5.5)
    classified_at: Mapped[ts]
