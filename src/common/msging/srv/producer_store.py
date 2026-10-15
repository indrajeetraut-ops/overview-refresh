"""Producer-record storage behind a config-selected adapter (spec §5).

The record — not just the key hash — is the interface: last_seen_at and
revoked are the management surface and must not fork between backends."""
import json
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..config import ProducerStoreConfig
from ..durable import private_dir, write_atomic
from ..models import ProducerRecord


@runtime_checkable
class ProducerStore(Protocol):
    def get(self, producer_id: str) -> ProducerRecord | None: ...
    def get_by_key_prefix(self, key_prefix: str) -> list[ProducerRecord]: ...
    def upsert(self, record: ProducerRecord) -> None: ...
    def touch_last_seen(self, producer_id: str, ts: str) -> None: ...
    def set_revoked(self, producer_id: str, revoked: bool) -> None: ...
    #: How many producers the store holds. Part of the Protocol because
    #: `registration.max_producers` (§10.1) is enforced at the one mint site,
    #: `Auth.issue()`, and must not fork between backends -- a cap that only
    #: the file backend honoured would be a cap nobody could rely on.
    def count(self) -> int: ...


class FileProducerStore:
    """Day-one backend: one JSON file keyed by producer_id, atomic replace.

    **Read-through cached, and that is not an optimisation.** Every read used to
    re-read and re-parse the whole file: `Auth.verify`'s cache miss called
    `get_by_key_prefix`, which was a full `json.loads` and then a linear scan.
    With `mode: open` and 8 KiB descriptors that put a 30 MB parse on the path
    of *every* request, including the 401s a hostile caller sends -- a garbage
    Bearer token cost exactly what a real one did. This process is the only
    writer of its own file, so the parse is cached and invalidated by the file's
    own (mtime_ns, size), and a `key_prefix -> producer_ids` index is built with
    it. `get_by_key_prefix` is then a dict lookup.

    An outside writer is still handled: the stat changes, the cache is dropped
    and the file is re-read. The one case that slips through is a rewrite inside
    the same nanosecond that keeps the byte count identical, which no real
    editor or replace does, and which the next write from this process corrects.

    `upsert`/`touch_last_seen`/`set_revoked` still rewrite the whole file --
    that is inherent to a single-JSON-file backend, and `registration.
    max_producers` is what bounds it. What they no longer do is re-read it.

    TODO(msging): `touch_last_seen` rewrites the whole file for a one-field
    change, and `Auth.verify` calls it on every cache miss -- so N active
    producers cost N/`_CACHE_TTL_S` full-file rewrites per second. Bounded by
    `max_producers` and off the event loop (the routes thread it), but still
    write amplification the postgres backend does not have. Either batch the
    timestamps behind a periodic flush or give the store a "last_seen only"
    sidecar file that does not carry the descriptors.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        # 0700: this directory holds producers.json, i.e. every producer's key
        # hash. Write access to it is enough to replace the file wholesale.
        private_dir(self.path.parent)
        self._lock = threading.RLock()
        # (mtime_ns, size) of the file `_cache` was parsed from; None = nothing
        # cached. The stat is the invalidation key, so another process's write
        # is picked up on the next read rather than served stale.
        self._stamp: tuple[int, int] | None = None
        self._cache: dict[str, dict] = {}
        self._by_prefix: dict[str, list[str]] = {}

    def _stat(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _load(self) -> dict[str, dict]:
        """The parsed file, re-read only when it has changed underneath us."""
        with self._lock:
            stamp = self._stat()
            if stamp is None:
                self._adopt({}, None)
                return self._cache
            if stamp == self._stamp:
                return self._cache
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            # Re-stat after the read: a writer that replaced the file *during*
            # it would otherwise have its stamp cached against the older bytes,
            # pinning stale data until the next write. Mismatched -> cache
            # nothing this pass and re-read next time.
            self._adopt(data, stamp if self._stat() == stamp else None)
            return data

    def _adopt(self, data: dict[str, dict], stamp: tuple[int, int] | None) -> None:
        self._cache = data
        self._stamp = stamp
        index: dict[str, list[str]] = {}
        for producer_id, rec in data.items():
            index.setdefault(rec["key_prefix"], []).append(producer_id)
        self._by_prefix = index

    def _save(self, data: dict[str, dict]) -> None:
        # write tmp -> fsync tmp -> rename -> fsync dir (design §3.1, D30).
        # This file is the server's ONLY durable state and the sole copy of
        # every producer's key hash. write_text + replace is atomic but not
        # durable: the classic rename-without-fsync outcome is a zero-length
        # producers.json, after which every producer's identity.json still
        # holds a valid-looking api_key the server has no record of, so the
        # whole fleet 401s and needs a manual re_register() -- which by design
        # orphans each producer's durable backlog. touch_last_seen lands here
        # on every auth cache miss, so the crash window is not rare.
        write_atomic(self.path, json.dumps(data, indent=2).encode("utf-8"))
        # This process just wrote it, so the bytes on disk are the dict in hand:
        # adopt both rather than making the next read re-parse what we serialised.
        self._adopt(data, self._stat())

    def get(self, producer_id: str) -> ProducerRecord | None:
        rec = self._load().get(producer_id)
        return ProducerRecord.model_validate(rec) if rec else None

    def get_by_key_prefix(self, key_prefix: str) -> list[ProducerRecord]:
        # All matches: prefixes are short and may collide; caller disambiguates
        # by key hash (server.verify).
        with self._lock:
            data = self._load()
            ids = self._by_prefix.get(key_prefix, ())
            return [ProducerRecord.model_validate(data[pid]) for pid in ids]

    def count(self) -> int:
        return len(self._load())

    def upsert(self, record: ProducerRecord) -> None:
        with self._lock:
            data = self._load()
            data[record.producer_id] = record.model_dump()
            self._save(data)

    def touch_last_seen(self, producer_id: str, ts: str) -> None:
        with self._lock:
            data = self._load()
            if producer_id in data:
                data[producer_id]["last_seen_at"] = ts
                self._save(data)

    def set_revoked(self, producer_id: str, revoked: bool) -> None:
        with self._lock:
            data = self._load()
            if producer_id in data:
                data[producer_id]["revoked"] = revoked
                self._save(data)


def make_store(cfg: ProducerStoreConfig) -> ProducerStore:
    if cfg.backend == "file":
        # cfg.file always has a value: ProducerStoreConfig.file is not
        # Optional and carries its own default_factory (config.py), so there
        # is no None case left to guard here the way the pre-rewrite code did.
        return FileProducerStore(cfg.file.path)
    if cfg.backend == "postgres":
        # Lazy import: server.storage carries SQLAlchemy/psycopg, which the
        # collector must never be forced to install (arch §19 packaging goal).
        # PgProducerStore implements this Protocol against the `producers`
        # table and reuses storage's own engine — no DSN lives in this
        # config (docs/modules/storage.md §3.2, §5).
        try:
            from server.storage.producer_store import PgProducerStore
        except ImportError as exc:
            raise NotImplementedError(
                "postgres producer-store backend: server.storage module not "
                "importable in this environment (docs/modules/storage.md §5)"
            ) from exc
        return PgProducerStore()
    raise ValueError(f"unknown producer_store backend: {cfg.backend!r}")
