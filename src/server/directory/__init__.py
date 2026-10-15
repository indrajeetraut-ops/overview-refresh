"""Org-directory module: a provider-agnostic roster the manager API reads.

Design: docs/design/identity-ingestion.md. Implementation plan:
docs/plan/2026-09-09-identity-ingestion-implementation.md (Track G).

Public surface:

    from server.directory import (
        DirectoryRecord, TeamRecord, ExternalKey, TeamMembership, RoleGrant,
        SyncResult, sync, reconcile, Precedence, DirectoryConfig, load_config,
    )

`sync()` is the only writer of `identity_links` / `teams` / `team_members` /
`role_assignments` and the directory fields on `users`. Providers (CSV now; SCIM/LDAP/OIDC
later) are adapters that emit `DirectoryRecord`s into it -- no schema or API change when one
is added (IDENT §8).
"""
from .config import DirectoryConfig, load_config
from .errors import DirectoryError, DirectoryValidationError, UnresolvableRecordError
from .models import (
    DirectoryRecord,
    ExternalKey,
    RoleGrant,
    SyncResult,
    TeamMembership,
    TeamRecord,
)
from .precedence import DEFAULT_CHAIN, Precedence
from .providers.csv import CsvParseResult, parse_csv_dir, parse_csv_streams
from .reconcile import ReconcileOutcome, mint_user_id, reconcile
from .repo_rollup import compute_user_repos, refresh_user_repos, repo_contributors
from .service import (
    TRUSTED_VIEWER_HEADER,
    descendants,
    members_of,
    resolve_user_id,
    resolve_viewer,
    team_of,
    users_in_scope,
    viewer_id_from_headers,
)
from .sync import sync

__all__ = [
    "DirectoryConfig",
    "load_config",
    "DirectoryError",
    "DirectoryValidationError",
    "UnresolvableRecordError",
    "DirectoryRecord",
    "ExternalKey",
    "RoleGrant",
    "SyncResult",
    "TeamMembership",
    "TeamRecord",
    "DEFAULT_CHAIN",
    "Precedence",
    "CsvParseResult",
    "parse_csv_dir",
    "parse_csv_streams",
    "ReconcileOutcome",
    "mint_user_id",
    "reconcile",
    "compute_user_repos",
    "refresh_user_repos",
    "repo_contributors",
    "resolve_user_id",
    "resolve_viewer",
    "viewer_id_from_headers",
    "TRUSTED_VIEWER_HEADER",
    "team_of",
    "descendants",
    "members_of",
    "users_in_scope",
    "sync",
]
