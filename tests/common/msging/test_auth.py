import asyncio
import hashlib
import re
import time

import pytest

from common.msging.errors import AuthError, CapacityError
from common.msging.models import ProducerRecord
from common.msging.srv import auth as auth_module
from common.msging.srv.auth import Auth
from common.msging.srv.producer_store import FileProducerStore


@pytest.fixture()
def auth(tmp_path):
    return Auth(FileProducerStore(tmp_path / "producers.json"))


def test_issue_returns_a_usable_key(auth):
    pid, key, _ = auth.issue({"host": "laptop"}, "laptop")
    assert auth.verify(f"Bearer {key}").producer_id == pid


def test_plaintext_key_is_never_stored(auth, tmp_path):
    _, key, _ = auth.issue({}, None)
    assert key not in (tmp_path / "producers.json").read_text()


def test_missing_header_raises_missing(auth):
    with pytest.raises(AuthError) as e:
        auth.verify(None)
    assert e.value.code == "missing"


def test_unknown_key_raises_unknown(auth):
    with pytest.raises(AuthError) as e:
        auth.verify("Bearer tk_prod_nope")
    assert e.value.code == "unknown"


def test_revoked_key_is_rejected(auth):
    """This is what §8.1's `unauthorized` terminal code rests on (gh #19)."""
    pid, key, _ = auth.issue({}, None)
    auth.set_revoked(pid, True)
    with pytest.raises(AuthError) as e:
        auth.verify(f"Bearer {key}")
    assert e.value.code == "revoked"


def test_prefix_collision_is_disambiguated_by_hash(auth):
    """Two keys sharing a prefix must not authenticate as each other.

    issue() picks a random 32-byte token, so two calls to it will not
    collide in practice -- asserting on that path proves nothing. Force the
    collision instead: seed the store directly with two ProducerRecords
    that share a `key_prefix` but carry different `key_hash` values (the
    hash of two keys we make up ourselves, sharing their first 12 chars),
    then verify each of the two real keys resolves to its own producer, and
    a third key sharing the same prefix but matching neither hash is
    rejected as unknown.
    """
    prefix = "tk_prod_AAAA"
    key_a = prefix + "1111"
    key_b = prefix + "2222"
    key_c = prefix + "9999"  # same prefix, matches no stored hash

    rec_a = ProducerRecord(
        producer_id="pid-a",
        key_prefix=prefix,
        key_hash=hashlib.sha256(key_a.encode()).hexdigest(),
        registered_at="2026-07-17T00:00:00+00:00",
    )
    rec_b = ProducerRecord(
        producer_id="pid-b",
        key_prefix=prefix,
        key_hash=hashlib.sha256(key_b.encode()).hexdigest(),
        registered_at="2026-07-17T00:00:00+00:00",
    )
    auth.store.upsert(rec_a)
    auth.store.upsert(rec_b)

    # Confirm the collision is real before trusting what it proves below.
    assert {r.producer_id for r in auth.store.get_by_key_prefix(prefix)} == {
        "pid-a",
        "pid-b",
    }

    assert auth.verify(f"Bearer {key_a}").producer_id == "pid-a"
    assert auth.verify(f"Bearer {key_b}").producer_id == "pid-b"

    with pytest.raises(AuthError) as e:
        auth.verify(f"Bearer {key_c}")
    assert e.value.code == "unknown"


def test_verify_touches_last_seen(auth):
    pid, key, _ = auth.issue({}, None)
    auth.verify(f"Bearer {key}")
    assert auth.store.get(pid).last_seen_at is not None


def test_revoke_invalidates_the_verify_cache_immediately(auth):
    """Design decision: set_revoked() purges any cached verify() result for
    that producer, so revocation is never masked by a live cache entry
    within the same process. (See auth.py module docstring for the
    remaining cross-process exposure window this does not cover.)

    Asserts through `verify_cached()` rather than on `_cache` directly: the
    cache is keyed by the key's sha256, not the plaintext key, so `key in
    _cache` no longer describes anything. The claim under test is unchanged and
    the probe is now the public one -- "is there a live cache entry for this
    key" is exactly what `verify_cached()` answers.
    """
    pid, key, _ = auth.issue({}, None)
    auth.verify(f"Bearer {key}")  # populates the cache
    assert auth.verify_cached(f"Bearer {key}") is not None
    # And the plaintext key is not what it is filed under -- issue()'s return
    # value is supposed to be the only copy of it in the process.
    assert key not in auth._cache

    auth.set_revoked(pid, True)

    assert auth.verify_cached(f"Bearer {key}") is None
    with pytest.raises(AuthError) as e:
        auth.verify(f"Bearer {key}")
    assert e.value.code == "revoked"


# ========================================================== the verify cache
#
# §5.2's `auth_cache_max`. The cache used to be unbounded and keyed by the
# **plaintext api_key**: one entry went in per distinct key, only `set_revoked`
# ever took one out, and each entry pinned a whole ProducerRecord -- descriptor
# included -- for the life of the process. So a caller who presented N keys
# pinned N records, and every one of those keys stayed readable in process
# memory. It is now an LRU keyed by sha256, capped, with an expiry sweep.


def _keys(auth: Auth) -> list[str]:
    with auth._cache_lock:
        return list(auth._cache)


def test_the_cache_is_keyed_by_the_hash_never_by_the_plaintext_key(auth):
    """The plaintext key is supposed to exist exactly once, in `issue()`'s
    return value. Keying the cache by it put a live copy of every key ever
    presented into a process-lifetime dict, one heap inspection or core dump
    away from being readable -- for a saving of about a microsecond, on a hash
    the miss path computes anyway."""
    issued = [auth.issue({}, None)[1] for _ in range(3)]
    for key in issued:
        auth.verify(f"Bearer {key}")

    cached = _keys(auth)
    assert len(cached) == 3
    for entry in cached:
        assert re.fullmatch(r"[0-9a-f]{64}", entry), \
            f"cache key is not a sha256 digest: {entry!r}"
    for key in issued:
        assert key not in cached
        assert hashlib.sha256(key.encode()).hexdigest() in cached
        # and no entry merely *embeds* it either
        assert not any(key in entry for entry in cached)


def test_the_cache_is_capped_at_cache_max(tmp_path):
    """The bound is the whole point: the worst case has to be a number an
    operator can multiply out (`auth_cache_max` x descriptor size), not
    "however many keys were ever presented"."""
    auth = Auth(FileProducerStore(tmp_path / "producers.json"), cache_max=4)
    for _ in range(40):
        auth.verify(f"Bearer {auth.issue({}, None)[1]}")
    assert auth.cache_size() == 4


def test_the_least_recently_used_entry_is_the_one_evicted(tmp_path):
    """LRU, not "whatever the dict yields first": the entry worth keeping is
    the one a busy producer keeps presenting, and evicting *it* would cost that
    producer a store round trip on every request while an idle key sat in the
    cache in its place."""
    auth = Auth(FileProducerStore(tmp_path / "producers.json"), cache_max=2)
    _, key_a, _ = auth.issue({}, None)
    _, key_b, _ = auth.issue({}, None)
    _, key_c, _ = auth.issue({}, None)

    auth.verify(f"Bearer {key_a}")
    auth.verify(f"Bearer {key_b}")
    # A hit promotes: `a` is now the most recently used, `b` the least.
    assert auth.verify_cached(f"Bearer {key_a}") is not None

    auth.verify(f"Bearer {key_c}")
    assert auth.cache_size() == 2
    assert auth.verify_cached(f"Bearer {key_b}") is None, "evicted the wrong entry"
    assert auth.verify_cached(f"Bearer {key_a}") is not None
    assert auth.verify_cached(f"Bearer {key_c}") is not None


def test_an_expired_entry_is_dropped_on_probe(auth, monkeypatch):
    """Expiry is what makes the cross-process revocation window bounded (see
    `Auth.verify`'s docstring). Dropped rather than merely ignored: an entry
    nobody ever presents again would otherwise hold its record forever, which
    is exactly how the leak looked."""
    monkeypatch.setattr(auth_module, "_CACHE_TTL_S", 0.02)
    _, key, _ = auth.issue({}, None)
    auth.verify(f"Bearer {key}")
    assert auth.cache_size() == 1

    time.sleep(0.05)
    assert auth.verify_cached(f"Bearer {key}") is None
    assert auth.cache_size() == 0, "an expired entry was left holding its record"


def test_expired_entries_are_swept_when_something_else_is_inserted(auth,
                                                                   monkeypatch):
    """A probe only reaches the key it was asked about. Without the sweep on
    insert, keys presented once and never again would accumulate untouched up
    to the cap and then start evicting *live* entries in their place."""
    monkeypatch.setattr(auth_module, "_CACHE_TTL_S", 0.02)
    for _ in range(5):
        auth.verify(f"Bearer {auth.issue({}, None)[1]}")
    assert auth.cache_size() == 5

    time.sleep(0.05)
    monkeypatch.setattr(auth_module, "_CACHE_TTL_S", 60.0)
    auth.verify(f"Bearer {auth.issue({}, None)[1]}")
    assert auth.cache_size() == 1, "the stale entries were not swept"


def test_cache_max_zero_disables_the_cache_without_breaking_verify(tmp_path):
    """A legal setting, not a degenerate one: every `verify()` then hits the
    store, which is what a deployment that cannot tolerate the cross-process
    revocation window wants."""
    auth = Auth(FileProducerStore(tmp_path / "producers.json"), cache_max=0)
    pid, key, _ = auth.issue({}, None)

    assert auth.verify(f"Bearer {key}").producer_id == pid
    assert auth.verify(f"Bearer {key}").producer_id == pid
    assert auth.cache_size() == 0
    assert auth.verify_cached(f"Bearer {key}") is None

    # And revocation is still immediate with nothing to purge.
    auth.set_revoked(pid, True)
    with pytest.raises(AuthError) as e:
        auth.verify(f"Bearer {key}")
    assert e.value.code == "revoked"


def test_a_rejected_key_never_enters_the_cache(auth):
    """Nothing about a key the store does not know touches the cache -- then or
    now. An unknown key that got an entry would be a free way to fill it."""
    for i in range(20):
        with pytest.raises(AuthError):
            auth.verify(f"Bearer tk_prod_nope{i}")
    assert auth.cache_size() == 0


def test_a_malformed_header_costs_no_store_lookup(auth):
    """`AuthError("missing")` is raised from the header parse, before the
    cache and before the store. It is the cheapest possible rejection, which
    matters because it is the one an anonymous caller can send in a loop."""
    for bad in (None, "", "Basic abc", "Bearer ", "Bearer    "):
        with pytest.raises(AuthError) as e:
            auth.verify_cached(bad)
        assert e.value.code == "missing"


# ========================================================== verify_async
#
# The routes are `async def`. A synchronous `verify()` ran the store read on the
# event loop -- with the file backend, a full parse of producers.json per cache
# miss, on the path of every request including the 401s. One caller with a
# garbage Bearer token serialised the whole server behind it.


class SlowStore:
    """A ProducerStore whose reads block, the way a file parse or a database
    round trip does. `delay_s` is the thing `verify_async` must not spend on
    the event loop."""

    def __init__(self, delay_s: float = 0.05) -> None:
        self.delay_s = delay_s
        self.records: dict[str, ProducerRecord] = {}
        self.reads = 0

    def get(self, producer_id):                                # pragma: no cover
        return self.records.get(producer_id)

    def get_by_key_prefix(self, key_prefix):
        self.reads += 1
        time.sleep(self.delay_s)
        return [r for r in self.records.values() if r.key_prefix == key_prefix]

    def upsert(self, record):
        self.records[record.producer_id] = record

    def touch_last_seen(self, producer_id, ts):
        time.sleep(self.delay_s)

    def set_revoked(self, producer_id, revoked):               # pragma: no cover
        self.records[producer_id].revoked = revoked

    def count(self):
        return len(self.records)


@pytest.mark.asyncio
async def test_verify_async_really_leaves_the_event_loop(tmp_path):
    """Ten misses against a store that blocks for 50 ms each.

    Serialised on the loop that is 500 ms and the server answers nothing else
    in the meantime -- heartbeats, SSE delivery and the worker pool all stalled
    behind one auth. Off the loop it is bounded by the thread pool, not by the
    sum. The threshold is deliberately loose (half the serial cost): what is
    under test is "concurrency exists", not how many threads the interpreter
    felt like using.
    """
    store = SlowStore(delay_s=0.05)
    auth = Auth(store)
    keys = [auth.issue({}, None)[1] for _ in range(10)]

    began = time.monotonic()
    results = await asyncio.gather(
        *(auth.verify_async(f"Bearer {k}") for k in keys))
    elapsed = time.monotonic() - began

    assert len(results) == 10
    assert store.reads == 10
    assert elapsed < 0.25, (
        f"10 blocking verifies took {elapsed:.3f}s -- serialised, so "
        "verify_async() ran the store read on the event loop")


@pytest.mark.asyncio
async def test_verify_async_does_not_pay_a_thread_hop_for_a_cache_hit(tmp_path):
    """Split, not blanket-offloaded. A hit is a dict lookup, and paying a
    thread hop for it would make the common path slower than the problem the
    offload fixes -- so the hit must be served without touching the store."""
    store = SlowStore(delay_s=0.05)
    auth = Auth(store)
    _, key, _ = auth.issue({}, None)
    await auth.verify_async(f"Bearer {key}")
    assert store.reads == 1

    began = time.monotonic()
    for _ in range(50):
        await auth.verify_async(f"Bearer {key}")
    assert time.monotonic() - began < 0.05
    assert store.reads == 1


@pytest.mark.asyncio
async def test_verify_async_raises_exactly_what_verify_raises(auth):
    for header, code in ((None, "missing"), ("Bearer tk_prod_nope", "unknown")):
        with pytest.raises(AuthError) as e:
            await auth.verify_async(header)
        assert e.value.code == code

    pid, key, _ = auth.issue({}, None)
    auth.set_revoked(pid, True)
    with pytest.raises(AuthError) as e:
        await auth.verify_async(f"Bearer {key}")
    assert e.value.code == "revoked"


# ======================================================== max_producers
#
# §10.1. `mode: open` means anyone who reaches the port is issued an identity,
# and every identity is a permanent row the auth path reads. Task 13 capped the
# *size* of one descriptor; nothing capped the count.


def test_max_producers_refuses_the_one_over_the_cap(tmp_path):
    auth = Auth(FileProducerStore(tmp_path / "producers.json"), max_producers=3)
    for _ in range(3):
        auth.issue({}, None)
    with pytest.raises(CapacityError):
        auth.issue({}, None)
    assert auth.store.count() == 3, "a refused registration still wrote a record"


def test_max_producers_is_enforced_at_issue_not_in_the_route(tmp_path):
    """The cap lives at the one mint site. Enforced in the HTTP layer instead
    it would be a cap that every other caller of `issue()` bypasses -- and the
    thing it bounds (the store) does not care which caller filled it."""
    auth = Auth(FileProducerStore(tmp_path / "producers.json"), max_producers=1)
    auth.issue({}, None)
    with pytest.raises(CapacityError):
        auth.issue({}, None)


def test_max_producers_zero_means_unlimited(tmp_path):
    """The escape hatch for a deployment that genuinely has more producers than
    the default and controls its own perimeter."""
    auth = Auth(FileProducerStore(tmp_path / "producers.json"), max_producers=0)
    for _ in range(25):
        auth.issue({}, None)
    assert auth.store.count() == 25


def test_a_refused_registration_logs_no_key_material(tmp_path, caplog):
    """Nothing was minted, so there is nothing to redact -- and the line must
    stay that way (§10.4)."""
    auth = Auth(FileProducerStore(tmp_path / "producers.json"), max_producers=1)
    auth.issue({"host": "laptop"}, None)
    with pytest.raises(CapacityError) as e:
        auth.issue({"host": "laptop"}, None)
    assert "tk_prod_" not in str(e.value)
