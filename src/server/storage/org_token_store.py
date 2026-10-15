"""Postgres backend for org ingest tokens (otel-intake design §4.5, storage.md §3.7).

The store half of org-token auth: mint, look up by hash, revoke. The HTTP half — bearer
header parsing, the verify cache, the 401 — lives in `server.ingest.otlp.auth`, mirroring how
`msging.srv.auth` sits above `PgProducerStore`. The split matters here: storage is the lowest
layer and must not import a service, and the admin CLI that issues tokens is storage's.

**The plaintext key exists exactly once**, in the return value of `issue()`. Only its sha256
is stored, so a lost token is replaced, never recovered (arch §18.1).
"""
import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select

from .engine import session_scope
from .schema import OrgToken

#: Distinguishes an org ingest token from a producer key (`tk_prod_`) on sight -- one
#: credential kind per intake path (D-OT4).
KEY_PREFIX = "tk_org_"

#: Characters of `token_hash` an admin needs to name a token for revocation. 12 hex chars is
#: 48 bits: far too few to reverse, far more than enough to be unique in one org's key set.
HASH_PREFIX_LEN = 12


def hash_key(api_key: str) -> str:
    """sha256 of a presented key. The only representation that ever reaches the database."""
    return hashlib.sha256(api_key.encode()).hexdigest()


class PgOrgTokenStore:
    """Requires the process-wide engine (`server.storage.get_engine(cfg)`) like every other
    store in this module."""

    def issue(self, org_id: str, descriptor: dict | None = None) -> tuple[str, str]:
        """Mint a token for `org_id`. Returns `(api_key, token_hash)`.

        `api_key` is returned here and only here -- hand it to the operator immediately.
        """
        api_key = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"
        token_hash = hash_key(api_key)
        with session_scope() as s:
            s.add(OrgToken(
                token_hash=token_hash,
                org_id=org_id,
                descriptor=descriptor or {},
                created_at=datetime.now(timezone.utc),
            ))
        return api_key, token_hash

    def get(self, token_hash: str) -> OrgToken | None:
        """The row for a presented key's hash, revoked ones included.

        Revoked rows are returned, not filtered: the caller has to be able to tell a revoked
        key from an unknown one, because they mean different things in the logs (a revoked
        key in use is a machine nobody updated; an unknown one is a probe).
        """
        with session_scope() as s:
            row = s.get(OrgToken, token_hash)
            if row is None:
                return None
            s.expunge(row)      # detached copy: callers outlive the session
            return row

    def find_by_hash_prefix(self, hash_prefix: str) -> list[OrgToken]:
        """Tokens whose hash starts with `hash_prefix` — the admin CLI's handle on a token
        it can no longer see the plaintext of."""
        with session_scope() as s:
            rows = s.execute(
                select(OrgToken).where(OrgToken.token_hash.startswith(hash_prefix))
            ).scalars().all()
            for row in rows:
                s.expunge(row)
            return list(rows)

    def list_for_org(self, org_id: str) -> list[OrgToken]:
        """Every token an org holds, revoked ones included — the admin's inventory."""
        with session_scope() as s:
            rows = s.execute(
                select(OrgToken).where(OrgToken.org_id == org_id)
                .order_by(OrgToken.created_at)
            ).scalars().all()
            for row in rows:
                s.expunge(row)
            return list(rows)

    def set_revoked(self, token_hash: str, revoked: bool = True) -> bool:
        """Flip the revoked flag. Returns False if no such token."""
        with session_scope() as s:
            row = s.get(OrgToken, token_hash)
            if row is None:
                return False
            row.revoked = revoked
            return True
