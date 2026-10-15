"""git_installations / git_repos / git_artifacts / git_deliveries / git_pending_installs --
the git-source acquisition module's tables (git-source.md §9).

Owned here under storage.md §3.1's single-definition rule though designed in
`docs/modules/git-source.md` (same arrangement as `queue_jobs` and the `otel_*` tables).
This module owns these tables; the derived tables that link and classify
(`session_git_links`, `pr_classification`) belong to `pr-work-classification.md`, not here.

No FKs on the intake path (storage.md §3.1): a `git_artifacts` row never blocks on its
`git_repos`/`git_installations` row arriving first, since the mapper upserts all three in one
transaction but from payloads that can reference an installation/repo it has not seen yet.
"""
from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, jsl, ts


class GitInstallation(Base):
    """The `installation_id -> org_id` authority (git-source.md §6.1) and the account-type
    record (§5.4). One row per GitHub App installation; never row-deleted -- `state` marks a
    suspended or removed install so history it produced is retained."""

    __tablename__ = "git_installations"
    # autoincrement=False: this is GitHub's own numeric installation id, supplied by the
    # mapper -- never DB-generated (a single-column integer PK defaults to autoincrement
    # otherwise, which would silently ignore the id the caller provides).
    installation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    org_id: Mapped[str] = mapped_column(index=True)          # resolved (§6.1); default org until mapped
    account_login: Mapped[str]                                # the org or user the App is installed on
    account_type: Mapped[str]                                 # "Organization" | "User" (§6.3 relevance)
    repo_selection: Mapped[str]                                # "all" | "selected"
    state: Mapped[str] = mapped_column(index=True)             # "active" | "suspended" | "deleted"
    installed_at: Mapped[ts]
    updated_at: Mapped[ts]
    info: Mapped[jsb]


class GitRepo(Base):
    """Repo identity (§6.2) -- the collector's `owner/name -> repo_id` resolution table -- and
    the per-repo backfill checkpoint. One row per repo the App can see. `repo_id` is the
    numeric-GitHub-id form; distinct from `repositories.repo_id` (the JSONL/collector-derived
    `git_ident` scheme, `repositories.py`), which this table is designed to be adopted by
    once the identity phase (implementation plan A6) lands."""

    __tablename__ = "git_repos"
    repo_id: Mapped[str] = mapped_column(primary_key=True)     # "github:<repo_num>" (rename/transfer-stable)
    repo_num: Mapped[int] = mapped_column(BigInteger, index=True)
    full_name: Mapped[str] = mapped_column(index=True)         # "owner/name" LABEL, not a key (§6.2)
    owner: Mapped[str]
    default_branch: Mapped[str]
    private: Mapped[bool]
    installation_id: Mapped[int] = mapped_column(BigInteger, index=True)
    org_id: Mapped[str] = mapped_column(index=True)
    # Newest artifact updated_at already mapped; resumable, walks back past the 3-day
    # redelivery window (§8.3) -- gap-fill is the primary recovery path, not a convenience.
    backfill_cursor: Mapped[ts | None]
    backfill_state: Mapped[str]                                 # "pending" | "listing" | "detailing" | "done"
    first_seen_at: Mapped[ts]
    updated_at: Mapped[ts]
    info: Mapped[jsb]


class GitArtifact(Base):
    """The normalized fact (§5.3) -- one table discriminated by `kind`. Upsert
    **last-writer-wins on `updated_at`**: an older payload never overwrites a newer artifact,
    which combined with the deterministic `artifact_id` is what makes replay and re-backfill
    idempotent (arch design rule 3). This is the analyzer's (`pr-work-classification.md`)
    input."""

    __tablename__ = "git_artifacts"
    artifact_id: Mapped[str] = mapped_column(primary_key=True)  # "github:<repo_num>:<kind>:<key>"
    kind: Mapped[str]                                            # "pr" | "commit" | "review" | "check"
    repo_id: Mapped[str]
    org_id: Mapped[str]
    number: Mapped[int | None]                                   # PR number
    sha: Mapped[str | None] = mapped_column(index=True)          # THE SESSION-JOIN key (§5.3, §6.4)
    author_login: Mapped[str | None] = mapped_column(index=True)
    author_email: Mapped[str | None]                             # fallback join for user mapping (§6.3)
    # Resolved via users.github_user; null until mapped -- never guessed (§6.3): a wrong
    # attribution is worse than a missing one in a report a VP reads.
    user_id: Mapped[str | None] = mapped_column(index=True)
    is_bot: Mapped[bool]
    state: Mapped[str]                                           # kind-specific (§5.3)
    title: Mapped[str | None]                                    # metadata; never logged (§11)
    branch: Mapped[str | None]                                   # head ref
    base_branch: Mapped[str | None]
    created_at: Mapped[ts]
    updated_at: Mapped[ts]                                       # drives cursor and last-writer-wins
    closed_at: Mapped[ts | None]
    merged_at: Mapped[ts | None] = mapped_column(index=True)     # set only when a PR actually merged
    stats: Mapped[jsb]              # additions/deletions/changed_files/commits/review_count/comment_count
    files: Mapped[jsl]              # [{path, op, additions, deletions}] -- paths+counts only, never content
    parent_artifact_id: Mapped[str | None] = mapped_column(index=True)  # review/commit -> its PR
    labels: Mapped[jsl]                                          # grouping + tier-0 classification key (§5.5)
    milestone: Mapped[str | None]                                # sprint proxy (§5.5)
    linked_issue: Mapped[str | None]                             # "github:<repo_num>:issue:<n>" ref only (§5.5)
    merge_commit_sha: Mapped[str | None] = mapped_column(index=True)  # pr only · rework provenance anchor (§5.7)
    before_sha: Mapped[str | None]                               # pr only · base-branch tip before merge (§5.7)
    after_sha: Mapped[str | None]                                # pr only · base-branch tip after merge (§5.7)
    integration_method: Mapped[str | None]                       # pr only · "merge" | "squash" | "rebase" (§5.7)
    reverts_sha: Mapped[str | None] = mapped_column(index=True)  # links a revert to its original (§5.6)
    commit_shas: Mapped[jsl]        # pr only · the PR's own commit shas; owning-PR dedup membership (§5.7)
    info: Mapped[jsb]               # namespaced catch-all incl. github.is_revert (§5.6)

    __table_args__ = (
        # The claim/scope scan the mapper and the correlator both walk: "artifacts for this
        # repo, of this kind, updated since X."
        Index("ix_git_artifacts_repo_kind_updated", "repo_id", "kind", "updated_at"),
    )


class GitArtifactEvent(Base):
    """Append-only PR state-transition history (Track churn/reopen). `git_artifacts.state` is
    current-state-only (last-writer-wins on `updated_at`), so a PR cycling
    open->closed->reopened leaves no trace there -- this table records one row per observed
    transition instead, which is what a reopen *count* needs.

    Written by `apply.py` from a `pull_request` webhook's `MapResult.pr_state_event`; the pure
    mapper emits the descriptor, this persists it. Idempotent under delivery replay via
    `source_key = "<artifact_id>:<action>:<occurred_at>"` -- two deliveries carrying the same
    (PR, action, updated_at) ARE the same transition, so a UNIQUE conflict is a no-op (no
    `delivery_id` needs threading through `apply_map_result`'s many call sites to dedup).

    FK-free like every intake table (storage.md §3.1): an event never blocks on its
    `git_artifacts` row."""

    __tablename__ = "git_artifact_events"
    event_id: Mapped[int] = mapped_column(primary_key=True)      # DB-generated serial
    artifact_id: Mapped[str] = mapped_column(index=True)          # the PR: github:<repo>:pr:<n>
    action: Mapped[str]                                           # "opened" | "reopened" | "closed" | "merged"
    to_state: Mapped[str]                                         # resulting git_artifacts.state
    occurred_at: Mapped[ts]                                       # the PR payload's updated_at
    source_key: Mapped[str] = mapped_column(unique=True)          # replay-idempotency key
    recorded_at: Mapped[ts]


class GitDelivery(Base):
    """The raw landing, dedup ledger, and replay buffer (§5.2). Pruned after the retention
    window (§10.3) once `status` is `done` or `unmapped`; `pending`/`failed` rows are kept
    for redelivery or a mapper fix-and-replay. Replaces the POC's JSONL sink."""

    __tablename__ = "git_deliveries"
    delivery_id: Mapped[str] = mapped_column(primary_key=True)  # X-GitHub-Delivery UUID; insert-if-absent
    event: Mapped[str]                                           # X-GitHub-Event
    action: Mapped[str | None]
    installation_id: Mapped[int | None] = mapped_column(BigInteger)
    repo_num: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[jsb]                                         # the delivery body verbatim -- the replay source
    status: Mapped[str]                                          # "pending" | "done" | "unmapped" | "failed"
    attempts: Mapped[int]
    received_at: Mapped[ts]
    mapped_at: Mapped[ts | None]

    __table_args__ = (
        # The claim scan and the retention prune both walk this (§9).
        Index("ix_git_deliveries_status_received", "status", "received_at"),
    )


class GitPendingInstall(Base):
    """The `state`-carrying setup-callback's one piece of durable state (git-source.md §6.1
    mechanism 1): a customer clicks "Connect GitHub" while authenticated as `org_id`, we send
    them to GitHub with a signed `state` token, and GitHub's redirect back names an
    `installation_id` this table has never seen before the reconcile/webhook confirms it. This
    row is the bridge between "we know which org clicked" (the callback, now) and "we know the
    install is real" (the signed `installation.created` webhook or a reconcile pass, later) --
    the redirect's `installation_id` alone is spoofable (§6.1's own caveat), so it binds a
    *pending* org only, consumed (deleted) the moment `git_installations` actually adopts it."""

    __tablename__ = "git_pending_installs"
    installation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    org_id: Mapped[str]
    created_at: Mapped[ts]
