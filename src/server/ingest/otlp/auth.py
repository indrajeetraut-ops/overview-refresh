"""Org-token authentication for the OTLP routes (otel-intake design §4.5, D-OT4).

One managed-settings block enrols a fleet, so the credential on the wire is the org's, not the
machine's — coarser than the JSONL path's producer keys by design, and deliberately a separate
credential kind (one per intake path).

**Mirrors `common.msging.srv.auth`, does not import it.** The mechanics are the same on
purpose — sha256 at rest, a short-TTL in-process cache, an explicit revoked check — but
msging's `Auth` is producer-typed all the way down (`ProducerRecord`, `touch_last_seen`,
`max_producers`) and its internals are its own. Sharing the class would couple two credential
kinds that are supposed to stay separable.

**What this proves and what it does not (§4.5, stated honestly).** The token establishes the
org. Everything inside the payload — `user.email`, `session.id`, `organization.id` — is
client-asserted and spoofable by a hostile developer *within that org*. Accepted for v1: same
trust zone as the JSONL collector. This is why `org_id` on every stored row comes from the
resolved principal here and never from the payload. The JWT successor (arch §18.1) is the
named fix path.
"""
from __future__ import annotations

import asyncio
import hmac
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from server.storage.org_token_store import PgOrgTokenStore, hash_key

from ..errors import IngestError

#: Same window msging uses. The exposure it buys is bounded and stated on `verify`.
_CACHE_TTL_S = 5.0

#: Cache bound for direct construction; a server passes its configured value.
DEFAULT_CACHE_MAX = 1024


class OrgAuthError(IngestError):
    """A presented Authorization header did not resolve to a live org token.

    `reason` is the log-visible code, never the wire body: the routes answer a bare 401 for
    all three, because telling a caller *which* of missing/unknown/revoked applies is telling
    an attacker whether a guessed key exists.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class OrgPrincipal:
    """What a verified OTLP request is allowed to assert. Deliberately one field: the org is
    the entire scope of an org token."""

    org_id: str


def _bearer(authorization: str | None) -> str:
    """The key out of an `Authorization: Bearer <key>` header.

    Split out so the cache probe and the store path reject a malformed header identically,
    and so neither has to touch the database to do it — an endpoint that hits Postgres to
    reject a garbage header is a free denial-of-service.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise OrgAuthError("missing")
    api_key = authorization.removeprefix("Bearer ").strip()
    if not api_key:
        raise OrgAuthError("missing")
    return api_key


class OrgAuth:
    """Resolve `Authorization: Bearer tk_org_…` to an `OrgPrincipal`."""

    def __init__(self, store: PgOrgTokenStore | None = None,
                 cache_max: int = DEFAULT_CACHE_MAX) -> None:
        self.store = store or PgOrgTokenStore()
        #: 0 disables the cache outright.
        self.cache_max = cache_max
        # Keyed by sha256(key), never by the key: the plaintext is supposed to exist once,
        # and keying by it would keep a live copy of every key ever presented in a
        # process-lifetime dict. The hash is computed on the miss path anyway.
        self._cache: "OrderedDict[str, tuple[OrgPrincipal, float]]" = OrderedDict()
        self._cache_lock = threading.Lock()

    def verify(self, authorization: str | None) -> OrgPrincipal:
        """Raises `OrgAuthError` with reason `missing` | `unknown` | `revoked`.

        A success is cached for `_CACHE_TTL_S`. Revocation therefore takes effect within one
        TTL window per process — bounded, and the honest cost of not hitting Postgres 65
        times a second (§6.2). Unlike msging's, this cache is not purged on revoke: revocation
        happens through the admin CLI in a *different* process, so there is no in-process
        event to purge on. The TTL is the whole mechanism, and it is short.

        **Blocking.** Every cache miss touches the database. `async` callers must use
        `verify_async`.
        """
        api_key = _bearer(authorization)
        hit = self._cache_get(hash_key(api_key))
        if hit is not None:
            return hit
        return self._verify_uncached(api_key)

    async def verify_async(self, authorization: str | None) -> OrgPrincipal:
        """`verify()` for an `async def` route: cache probe on the loop, miss on a thread.

        Split rather than blanket-offloaded — a cache hit is a dict lookup, and paying a
        thread hop for it would make the common path slower than the problem it fixes. Only
        the miss leaves the loop, which is what keeps one caller with a garbage token from
        serialising the whole server behind its database reads.
        """
        api_key = _bearer(authorization)
        hit = self._cache_get(hash_key(api_key))
        if hit is not None:
            return hit
        return await asyncio.to_thread(self._verify_uncached, api_key)

    def _verify_uncached(self, api_key: str) -> OrgPrincipal:
        presented = hash_key(api_key)
        row = self.store.get(presented)
        if row is None:
            raise OrgAuthError("unknown")
        # The lookup was by primary key, so this can only fail on a database that returned
        # the wrong row -- but it is a secret comparison, and those are done in constant time.
        if not hmac.compare_digest(row.token_hash, presented):
            raise OrgAuthError("unknown")
        if row.revoked:
            raise OrgAuthError("revoked")
        principal = OrgPrincipal(org_id=row.org_id)
        self._cache_put(presented, principal)
        return principal

    # -------------------------------------------------------------- cache

    def _cache_get(self, token_hash: str) -> OrgPrincipal | None:
        with self._cache_lock:
            entry = self._cache.get(token_hash)
            if entry is None:
                return None
            if entry[1] <= time.monotonic():
                del self._cache[token_hash]      # expired in place; dropped, not left to rot
                return None
            self._cache.move_to_end(token_hash)
            return entry[0]

    def _cache_put(self, token_hash: str, principal: OrgPrincipal) -> None:
        if self.cache_max <= 0:
            return
        now = time.monotonic()
        with self._cache_lock:
            self._cache[token_hash] = (principal, now + _CACHE_TTL_S)
            self._cache.move_to_end(token_hash)
            # Sweep expired first, evict live second: dropping a dead entry is free, whereas
            # evicting a live one costs its owner a database round trip.
            for stale in [k for k, (_p, exp) in self._cache.items() if exp <= now]:
                del self._cache[stale]
            while len(self._cache) > self.cache_max:
                self._cache.popitem(last=False)

    def cache_size(self) -> int:
        """Entries currently held — the number `cache_max` bounds, and an observability seam."""
        with self._cache_lock:
            return len(self._cache)
