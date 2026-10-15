"""Producer authentication: issue/verify against a ProducerStore (design
§10.3, gh #19).

Framework-free: callers (e.g. ingest's FastAPI dependency, Task 13) wire
`verify()` into their own HTTP layer -- this module never imports a web
framework.

Recovered from the pre-rewrite `src/common/msging/server.py` (see git show
3c15d72^): the key format (`tk_prod_<token_urlsafe(32)>`), SHA-256 hashing,
the 12-char lookup prefix, and the short-TTL in-process cache all carry
over unchanged. Everything ACK/NACK-related in that file was batch/spool
transport, not auth, and has no home in this redesign.

New in this rewrite: `verify()` raises `AuthError("revoked")` for a revoked
producer (gh #19 -- the old code never checked the flag), and `set_revoked`
purges any cached verify() result for that producer so a revocation is
never masked by a live cache entry. See `Auth.verify`/`Auth.set_revoked`
docstrings for the cache/revocation exposure this does and does not cover.
"""
import asyncio
import hashlib
import hmac
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone

from ..errors import AuthError, CapacityError
from ..log import get_logger, redact_key
from ..models import ProducerRecord
from .producer_store import ProducerStore

log = get_logger("msging.srv.auth")

KEY_PREFIX_LEN = 12
_CACHE_TTL_S = 5.0

#: Cache bound used when nothing passes one (tests, direct construction).
#: `ServerConfig.auth_cache_max` is what a server actually runs on.
DEFAULT_CACHE_MAX = 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def _bearer(authorization: str | None) -> str:
    """The key out of an `Authorization: Bearer <key>` header.

    Raises AuthError("missing") for anything else. Split out so the cache probe
    and the store path reject a malformed header identically -- and so neither
    of them has to touch the store to do it.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("missing")
    api_key = authorization.removeprefix("Bearer ").strip()
    if not api_key:
        raise AuthError("missing")
    return api_key


class Auth:
    """Producer-side authentication: issue and verify producer API keys.

    The plaintext key exists exactly once, in the return value of
    `issue()`. It is never stored (the record holds `key_prefix` +
    `key_hash` only) and never logged -- any key-related logging goes
    through `redact_key()`.
    """

    def __init__(self, store: ProducerStore, cache_max: int = DEFAULT_CACHE_MAX,
                 max_producers: int = 0) -> None:
        self.store = store
        #: §5.2's `auth_cache_max`. 0 disables the cache outright.
        self.cache_max = cache_max
        #: §10.1's `registration.max_producers`. 0 = unlimited.
        self.max_producers = max_producers
        # sha256(api_key) -> (ProducerRecord, monotonic expiry). Populated only
        # on a successful, non-revoked verify (see verify()) so a revoked key
        # never enters the cache in the first place.
        #
        # **Keyed by the hash, not the key.** The plaintext key is supposed to
        # exist exactly once, in issue()'s return value; keying by it put a live
        # copy of every key ever presented in a process-lifetime dict, one core
        # dump or heap inspection away from being readable. Hashing costs about
        # a microsecond and the hash is computed on the miss path anyway.
        #
        # **Bounded.** An OrderedDict used as an LRU, capped at `cache_max`,
        # with expired entries swept on insert. Unbounded, one entry went in per
        # distinct key and only `set_revoked` ever took one out -- so a caller
        # who presented N keys pinned N ProducerRecords, whole descriptors
        # included, for the life of the process. Nothing about a *rejected* key
        # touches this dict, then or now: an unknown key never gets an entry.
        self._cache: "OrderedDict[str, tuple[ProducerRecord, float]]" = OrderedDict()
        # verify() runs in a worker thread (see verify_async) while the loop
        # thread probes the cache, so the read-modify-write pairs below
        # (move_to_end after a hit, sweep + popitem on insert) need a lock. It
        # is held for dict operations only -- never across store I/O.
        self._cache_lock = threading.Lock()

    def issue(
        self,
        descriptor: dict | None = None,
        producer_type: str | None = None,
    ) -> tuple[str, str, str]:
        """Mint a new producer identity and API key.

        Returns (producer_id, api_key, registered_at). `api_key` is
        returned here and only here -- callers must hand it to the
        producer immediately; it cannot be recovered later.

        Raises:
            CapacityError: the store already holds `max_producers` records
                (§10.1). Checked here rather than in the HTTP layer because
                this is the one mint site -- a cap enforced in the route would
                be a cap any other caller of `issue()` bypasses.

        TODO(msging): the count-then-upsert below is not atomic, and the route
        runs `issue()` in a thread -- so N concurrent registrations arriving at
        the cap can all read `count() == max_producers - 1` and all write,
        overshooting by up to N-1. Immaterial against what the cap is for (the
        blast radius of a loop, not an exact quota) and bounded by the server's
        concurrency, but it does mean `max_producers` is a soft cap. Closing it
        needs the check and the write under one store-level lock, which the
        `ProducerStore` Protocol does not currently expose.
        """
        if self.max_producers and self.store.count() >= self.max_producers:
            # Loud: either someone is looping /register or a real deployment has
            # outgrown its cap, and both need an operator. No key material here
            # -- nothing was minted.
            log.warning("registration_at_capacity",
                        max_producers=self.max_producers)
            raise CapacityError(
                f"producer store holds {self.max_producers} producers, the "
                "server.registration.max_producers cap (§10.1)"
            )
        producer_id = uuid.uuid4().hex
        api_key = f"tk_prod_{secrets.token_urlsafe(32)}"
        record = ProducerRecord(
            producer_id=producer_id,
            key_prefix=api_key[:KEY_PREFIX_LEN],
            key_hash=_hash_key(api_key),
            producer_type=producer_type,
            descriptor=descriptor or {},
            registered_at=_now_iso(),
        )
        self.store.upsert(record)
        # Not "producer_registered": §10.4 gives that name one field list
        # (producer_id, mode) and router.py owns it, because only the HTTP
        # layer knows the registration mode. Two lines sharing an event name
        # with different fields makes every log query on it wrong. This one is
        # the key mint, so it is named for that.
        log.info(
            "producer_key_issued",
            producer_id=producer_id,
            key_prefix=record.key_prefix,
        )
        return producer_id, api_key, record.registered_at

    def verify(self, authorization: str | None) -> ProducerRecord:
        """Resolve an `Authorization: Bearer <key>` header to its
        ProducerRecord.

        Raises AuthError with code:
          - "missing": no header, or not a Bearer token.
          - "unknown": no stored record's hash matches the presented key.
          - "revoked": a matching record exists but has been revoked.

        A successful result is cached for `_CACHE_TTL_S` seconds so a busy
        producer's repeat requests don't re-hash and re-hit the store (and,
        as a side effect, so `touch_last_seen` is throttled to at most once
        per TTL window rather than written on every request -- a cache hit
        below returns before `touch_last_seen` is ever called).

        Cache vs. revocation: `set_revoked` purges this producer's cache
        entry immediately, so within this process a revocation takes
        effect on the very next call, not after the TTL expires. The
        remaining exposure is cross-process: this cache is in-process only,
        so a *different* process holding a cached verify() result for this
        key keeps accepting it for up to `_CACHE_TTL_S` seconds after
        revocation lands in the store. Worst case is bounded by that TTL
        (currently 5s) per process.

        **Blocking.** Every path that misses the cache touches the store, and
        the store may be a file rewrite or a database round trip. An `async`
        caller must therefore not call this directly -- use `verify_async`,
        which probes the cache on the loop and takes the miss to a thread.
        """
        api_key = _bearer(authorization)
        hit = self._cache_get(_hash_key(api_key))
        if hit is not None:
            return hit
        return self._verify_uncached(api_key)

    def verify_cached(self, authorization: str | None) -> ProducerRecord | None:
        """The cache probe alone: a live entry, or None. Never touches the store.

        Non-blocking, so it is the half of `verify()` that is safe to run on the
        event loop. `AuthError("missing")` still raises here -- a malformed or
        absent header needs no store to reject and must not cost a thread hop.
        """
        return self._cache_get(_hash_key(_bearer(authorization)))

    async def verify_async(self, authorization: str | None) -> ProducerRecord:
        """`verify()` for an `async def` route: cache on the loop, miss on a thread.

        The routes are `async def`, so a synchronous `verify()` ran the store
        read *on the event loop*. With the file backend that was a full parse of
        producers.json per cache miss, on the path of every request including
        the 401s -- one hostile caller with a garbage Bearer token serialised
        the whole server behind it, heartbeats and SSE delivery included.

        Split rather than blanket-offloaded: a cache hit is a dict lookup, and
        paying a thread hop for it would make the common path slower than the
        problem it fixes. Only the miss -- the part that does I/O -- leaves the
        loop.

        Raises: exactly what `verify()` raises.
        """
        api_key = _bearer(authorization)
        hit = self._cache_get(_hash_key(api_key))
        if hit is not None:
            return hit
        return await asyncio.to_thread(self._verify_uncached, api_key)

    def _verify_uncached(self, api_key: str) -> ProducerRecord:
        """The store half of `verify()`. Blocking; never call it from the loop."""
        # Prefixes are only 12 chars and may collide (get_by_key_prefix
        # returns a list for exactly this reason): fetch every candidate
        # and disambiguate by comparing the full key's hash. hmac.compare_digest,
        # not `==` -- this is a secret comparison and timing matters.
        presented_hash = _hash_key(api_key)
        record = next(
            (
                r
                for r in self.store.get_by_key_prefix(api_key[:KEY_PREFIX_LEN])
                if hmac.compare_digest(r.key_hash, presented_hash)
            ),
            None,
        )
        if record is None:
            log.info("producer_auth_unknown", key_prefix=redact_key(api_key))
            raise AuthError("unknown")
        if record.revoked:
            log.info("producer_auth_revoked", producer_id=record.producer_id)
            raise AuthError("revoked")

        now = _now_iso()
        self.store.touch_last_seen(record.producer_id, now)
        record = record.model_copy(update={"last_seen_at": now})
        self._cache_put(presented_hash, record)
        return record

    # -------------------------------------------------------------- cache

    def _cache_get(self, key_hash: str) -> ProducerRecord | None:
        """A live entry for `key_hash`, promoted to most-recently-used."""
        with self._cache_lock:
            entry = self._cache.get(key_hash)
            if entry is None:
                return None
            if entry[1] <= time.monotonic():
                # Expired in place. Dropped rather than left to be overwritten:
                # an entry nobody presents again would otherwise sit in the dict
                # holding a record forever, which is how the leak looked.
                del self._cache[key_hash]
                return None
            self._cache.move_to_end(key_hash)
            return entry[0]

    def _cache_put(self, key_hash: str, record: ProducerRecord) -> None:
        if self.cache_max <= 0:
            return
        now = time.monotonic()
        with self._cache_lock:
            self._cache[key_hash] = (record, now + _CACHE_TTL_S)
            self._cache.move_to_end(key_hash)
            # Sweep first, evict second: an expired entry is dead weight and
            # dropping it is free, whereas evicting a live one costs its owner a
            # store round trip. The sweep is bounded by the cap, so it is O(1)
            # amortised however long the process runs.
            for stale in [k for k, (_r, exp) in self._cache.items() if exp <= now]:
                del self._cache[stale]
            while len(self._cache) > self.cache_max:
                self._cache.popitem(last=False)

    def cache_size(self) -> int:
        """Entries currently held. The number `auth_cache_max` bounds; an
        observability seam, and what the leak regression asserts on."""
        with self._cache_lock:
            return len(self._cache)

    def set_revoked(self, producer_id: str, revoked: bool) -> None:
        """Set (or clear) the revoked flag on a producer record.

        When revoking, also purge any cached verify() result for this
        producer so the revocation is visible on the very next verify()
        call in this process rather than waiting out the cache TTL -- see
        the cache/revocation note on `verify`.
        """
        self.store.set_revoked(producer_id, revoked)
        if revoked:
            with self._cache_lock:
                for key_hash, (record, _expiry) in list(self._cache.items()):
                    if record.producer_id == producer_id:
                        del self._cache[key_hash]
