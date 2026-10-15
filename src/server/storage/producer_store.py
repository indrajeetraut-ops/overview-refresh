"""Postgres backend for msging.ProducerStore (storage.md §5)."""
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.msging.models import ProducerRecord

from .engine import session_scope
from .schema import Producer


class PgProducerStore:
    """Postgres-backed ProducerStore.

    Requires the storage engine to be initialized once at process startup via
    `server.storage.get_engine(cfg)`; every method uses the shared
    `session_scope()`. Selecting the `postgres` producer-store backend without
    that bootstrap will raise RuntimeError on first use.

    TODO(storage): server bootstrap wiring is a future module.
    """

    def get(self, producer_id: str) -> ProducerRecord | None:
        with session_scope() as s:
            row = s.get(Producer, producer_id)
            return ProducerRecord.model_validate(row.record) if row else None

    def get_by_key_prefix(self, key_prefix: str) -> list[ProducerRecord]:
        with session_scope() as s:
            rows = s.execute(
                select(Producer).where(Producer.key_prefix == key_prefix)
            ).scalars().all()
            return [ProducerRecord.model_validate(r.record) for r in rows]

    def count(self) -> int:
        """Rows in `producers`, for `registration.max_producers` (§10.1).

        A `SELECT count(*)`, not a fetch: the cap is checked on every
        `/register`, and materialising every record to length it would put the
        whole table on the registration path.
        """
        with session_scope() as s:
            return int(s.execute(
                select(func.count()).select_from(Producer)).scalar_one())

    def upsert(self, record: ProducerRecord) -> None:
        data = record.model_dump()
        with session_scope() as s:
            stmt = pg_insert(Producer).values(producer_id=record.producer_id, record=data)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Producer.producer_id], set_={"record": data}
            )
            s.execute(stmt)

    def touch_last_seen(self, producer_id: str, ts: str) -> None:
        self._patch(producer_id, "last_seen_at", ts)

    def set_revoked(self, producer_id: str, revoked: bool) -> None:
        self._patch(producer_id, "revoked", revoked)

    def _patch(self, producer_id: str, key: str, value) -> None:
        with session_scope() as s:
            row = s.get(Producer, producer_id)
            if row is not None:
                record = dict(row.record)
                record[key] = value
                row.record = record
