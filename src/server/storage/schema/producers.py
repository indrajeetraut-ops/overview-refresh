"""producers — registration backend for msging.ProducerStore (storage.md §3.2)."""
from sqlalchemy import Computed, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb


class Producer(Base):
    __tablename__ = "producers"
    producer_id: Mapped[str] = mapped_column(primary_key=True)     # registration UUID
    record: Mapped[jsb]                                            # full ProducerRecord dict
    key_hash: Mapped[str] = mapped_column(String, Computed("(record ->> 'key_hash')", persisted=True))
    key_prefix: Mapped[str] = mapped_column(String, Computed("(record ->> 'key_prefix')", persisted=True))
    __table_args__ = (
        Index("uq_producers_key_hash", "key_hash", unique=True),
        Index("ix_producers_key_prefix", "key_prefix"),
    )
