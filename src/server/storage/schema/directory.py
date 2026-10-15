"""identity_links / teams / team_members / role_assignments -- the org directory
(docs/design/identity-ingestion.md §3.1, implementation plan Track G1).

Owned here under storage.md §3.1's single-definition rule though designed in
`docs/design/identity-ingestion.md` (same arrangement as the `git_*` and `otel_*` tables).
The write path is `server.directory.sync` and nothing else (IDENT §3, decision 1).

No FKs (storage.md §3.1): a `team_members` row must not block on its `teams` row, and a
telemetry-derived `users` stub is enriched in place rather than re-parented -- the reconcile
layer (`server.directory.reconcile`), not a constraint, is what keeps identity single.
"""
from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, ts


class IdentityLink(Base):
    """`(provider, external_id) -> user_id` -- the reconcile spine (IDENT §3.1, decision 2).

    Append-only per user: a link is added, never rewritten (IDENT §3.2 evolution rules), so a
    CSV-seeded user and a later IdP-provisioned one collapse onto one `user_id` instead of
    duplicating. Every future provider is just a new `provider` namespace here -- no new table
    (IDENT §8).
    """

    __tablename__ = "identity_links"
    provider: Mapped[str] = mapped_column(primary_key=True)      # csv | github | telemetry | scim | ldap | oidc
    external_id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(index=True)


class Team(Base):
    """One node of the org tree. Hierarchy is an adjacency (`parent_team_id`); roll-ups walk
    it with a recursive CTE (IDENT §3, §4 "Hierarchy"). `org_id` is carried on every row so
    multi-tenant is a filter, not a rewrite (IDENT §7)."""

    __tablename__ = "teams"
    team_id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(index=True)
    name: Mapped[str]
    parent_team_id: Mapped[str | None] = mapped_column(index=True)   # null = root
    source_provider: Mapped[str]                                     # who asserted this team


class TeamMember(Base):
    """Many-to-many membership; a user may be on several teams (`users.primary_team_id` gives
    the default attribution, IDENT §7). Replaced wholesale for the synced set on each
    `sync()` (implementation plan G2)."""

    __tablename__ = "team_members"
    team_id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(primary_key=True)
    role_in_team: Mapped[str | None]                                 # lead | member | ... ; null = unspecified


class RoleAssignment(Base):
    """An app authorization grant the F2 role gate reads (IDENT §5). `role` stays
    app-assigned even once an IdP owns identity/membership (IDENT §7 clarifying Qs). The
    natural key is the whole tuple -- one grant of a role at a scope, regardless of how many
    times it is re-synced. Replaced wholesale for the synced set on each `sync()`."""

    __tablename__ = "role_assignments"
    user_id: Mapped[str] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(primary_key=True)              # dev | lead | admin
    scope_type: Mapped[str] = mapped_column(primary_key=True)        # org | team | repo
    scope_id: Mapped[str] = mapped_column(primary_key=True)          # org_id / team_id / repo_id

    __table_args__ = (
        # "users granted <role>" and "who can see team/repo X" -- the two directions the gate
        # walks. The composite PK's leading column already serves `WHERE user_id = ?`.
        Index("ix_role_assignments_scope", "scope_type", "scope_id"),
    )
