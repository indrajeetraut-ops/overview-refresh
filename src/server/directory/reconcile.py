"""reconcile(record) -> user_id (docs/design/identity-ingestion.md §3, implementation plan G2).

Map an incoming `DirectoryRecord` to exactly one `user_id`:

1. resolve each `external_keys` entry against `identity_links` -- first match wins;
2. else fall back to the legacy identity columns a telemetry stub set
   (`users.github_user` / `users.account_uuid`) -- a hit here is **enriched, never
   duplicated**, and back-filled into the link spine;
3. else, if the record carries an email or a display_name, create a `users` row;
4. else reject -- an identity is never guessed (§3.2).

Then append every one of the record's external keys to `identity_links` (append-only) so the
next sync resolves on the spine directly.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from server.storage.queries import QueryCatalog

from .errors import UnresolvableRecordError
from .models import DirectoryRecord

# external-key providers that a pre-directory telemetry/git path may have parked on the legacy
# `users` columns instead of the link spine.
_LEGACY_GITHUB_PROVIDERS = ("github",)
_LEGACY_ACCOUNT_PROVIDERS = ("telemetry", "account_uuid")


@dataclass(frozen=True)
class ReconcileOutcome:
    user_id: str
    created: bool
    linked: int  # identity_links rows genuinely added this call


def mint_user_id(record: DirectoryRecord) -> str:
    """Deterministic id for a newly created person. A `github` key mints `github:<login>` --
    the *same* namespace git-source's auto-create uses (git.yaml `create_user_for_github_login`),
    so a directory-seeded and a PR-seeded identity for one person converge instead of forking.
    Otherwise the first external key: `<provider>:<external_id>`.
    """
    for k in record.external_keys:
        if k.provider == "github":
            return f"github:{k.external_id}"
    first = record.external_keys[0]
    return f"{first.provider}:{first.external_id}"


def _resolve_on_spine(session: Session, catalog: QueryCatalog, record: DirectoryRecord) -> str | None:
    q = catalog.get("directory", "resolve_identity_link")
    for k in record.external_keys:
        row = session.execute(q, {"provider": k.provider, "external_id": k.external_id}).first()
        if row:
            return row[0]
    return None


def _resolve_on_legacy(session: Session, catalog: QueryCatalog, record: DirectoryRecord) -> str | None:
    github_user = next(
        (k.external_id for k in record.external_keys if k.provider in _LEGACY_GITHUB_PROVIDERS),
        None,
    )
    account_uuid = next(
        (k.external_id for k in record.external_keys if k.provider in _LEGACY_ACCOUNT_PROVIDERS),
        None,
    )
    if github_user is None and account_uuid is None:
        return None
    row = session.execute(
        catalog.get("directory", "resolve_user_by_legacy_identity"),
        {"github_user": github_user, "account_uuid": account_uuid},
    ).first()
    return row[0] if row else None


def reconcile(
    session: Session,
    catalog: QueryCatalog,
    record: DirectoryRecord,
) -> ReconcileOutcome:
    user_id = _resolve_on_spine(session, catalog, record)
    created = False

    if user_id is None:
        user_id = _resolve_on_legacy(session, catalog, record)

    if user_id is None:
        if not record.email and not record.display_name:
            keys = ", ".join(f"{k.provider}:{k.external_id}" for k in record.external_keys)
            raise UnresolvableRecordError(
                f"record with keys [{keys}] resolves to no user and carries no email/display_name"
            )
        user_id = mint_user_id(record)
        session.execute(
            catalog.get("directory", "insert_user"),
            {
                "user_id": user_id,
                "email": record.email,
                "display_name": record.display_name,
                "status": record.status,
                "primary_team_id": None,
                "source_provider": record.source_provider,
            },
        )
        created = True

    linked = 0
    link_q = catalog.get("directory", "insert_identity_link")
    for k in record.external_keys:
        res = session.execute(
            link_q, {"provider": k.provider, "external_id": k.external_id, "user_id": user_id}
        )
        linked += res.rowcount if res.rowcount and res.rowcount > 0 else 0

    return ReconcileOutcome(user_id=user_id, created=created, linked=linked)
