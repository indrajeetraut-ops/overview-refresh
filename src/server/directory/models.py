"""The provider-facing contract: `DirectoryRecord` and `TeamRecord` are the *only* things a
provider produces, and the sole input to `sync()` (docs/design/identity-ingestion.md §3.2).

Framework-free dataclasses -- directly testable, and what makes a later SCIM/LDAP/OIDC
provider a pure adapter (IDENT §8): it emits these, nothing else.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import DirectoryValidationError

_STATUS = ("active", "disabled")
_ROLES = ("dev", "lead", "admin")
_SCOPE_TYPES = ("org", "team", "repo")


@dataclass(frozen=True)
class ExternalKey:
    """One `(provider, external_id)` pair -- an entry in the reconcile spine (IDENT §3.1)."""

    provider: str
    external_id: str

    def __post_init__(self) -> None:
        if not self.provider or not self.external_id:
            raise DirectoryValidationError("external key needs both provider and external_id")


@dataclass(frozen=True)
class TeamMembership:
    """A user's membership in one team, as declared on a `DirectoryRecord`."""

    team_id: str
    role_in_team: str | None = None


@dataclass(frozen=True)
class RoleGrant:
    """One app authz grant: `role` at (`scope_type`, `scope_id`) -- read by the F2 gate."""

    role: str
    scope_type: str
    scope_id: str

    def __post_init__(self) -> None:
        if self.role not in _ROLES:
            raise DirectoryValidationError(f"role {self.role!r} not in {_ROLES}")
        if self.scope_type not in _SCOPE_TYPES:
            raise DirectoryValidationError(f"scope_type {self.scope_type!r} not in {_SCOPE_TYPES}")
        if not self.scope_id:
            raise DirectoryValidationError("role grant needs a scope_id")


@dataclass(frozen=True)
class DirectoryRecord:
    """One person, as asserted by one provider. `external_keys` is the reconcile spine and
    must be non-empty; everything else is optional and precedence-gated on write."""

    external_keys: tuple[ExternalKey, ...]
    email: str | None = None
    display_name: str | None = None
    status: str = "active"
    teams: tuple[TeamMembership, ...] = ()
    roles: tuple[RoleGrant, ...] = ()
    source_provider: str = "csv"

    def __post_init__(self) -> None:
        if not self.external_keys:
            raise DirectoryValidationError("a DirectoryRecord needs at least one external key")
        if self.status not in _STATUS:
            raise DirectoryValidationError(f"status {self.status!r} not in {_STATUS}")
        if not self.source_provider:
            raise DirectoryValidationError("source_provider is required")

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "DirectoryRecord":
        """Build from the wire shape (IDENT §3.2). Unknown keys are ignored so a newer
        provider can send more without breaking this one."""
        keys = tuple(
            ExternalKey(k["provider"], k["external_id"]) for k in d.get("external_keys", [])
        )
        teams = tuple(
            TeamMembership(t["team_id"], t.get("role_in_team")) for t in d.get("teams", [])
        )
        roles = tuple(
            RoleGrant(r["role"], r["scope_type"], r["scope_id"]) for r in d.get("roles", [])
        )
        return cls(
            external_keys=keys,
            email=d.get("email"),
            display_name=d.get("display_name"),
            status=d.get("status", "active"),
            teams=teams,
            roles=roles,
            source_provider=d.get("source_provider", "csv"),
        )


@dataclass(frozen=True)
class TeamRecord:
    """A team node, declared in its own record (IDENT §3.2). `parent_team_id` carries the
    hierarchy; a root team's parent is None."""

    team_id: str
    org_id: str
    name: str
    parent_team_id: str | None = None
    source_provider: str = "csv"

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "TeamRecord":
        return cls(
            team_id=d["team_id"],
            org_id=d["org_id"],
            name=d["name"],
            parent_team_id=d.get("parent_team_id"),
            source_provider=d.get("source_provider", "csv"),
        )


@dataclass
class SyncResult:
    """What one `sync()` did (IDENT implementation plan G2).

    - `created`  -- new `users` rows
    - `updated`  -- existing `users` rows whose directory fields were rewritten (precedence-won)
    - `linked`   -- `identity_links` rows added (new provider identities attached)
    - `skipped`  -- records rejected as unresolvable

    `notes` carries one human-readable line per skipped/blocked record for the CLI's dry-run
    diff and the structured log -- never PII values.
    """

    created: int = 0
    updated: int = 0
    linked: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)
