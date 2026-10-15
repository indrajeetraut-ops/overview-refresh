"""queue_jobs — generic per-session dispatch queue (ingest.md §4.2, arch §15.1)."""
from sqlalchemy import BigInteger, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, ts


class QueueJob(Base):
    __tablename__ = "queue_jobs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    topic: Mapped[str]                                      # 'fold_session' (later: 'cost', 'rollup', ...)
    dedup_key: Mapped[str]                                  # session_id — coalesces arrivals -> 1 job
    payload: Mapped[jsb]                                    # pointer only: {"session_id": ...}
    # server_default (not just the ORM-side default): ring_fold (ingest.yaml) is a raw
    # named-query INSERT that omits these columns, relying on Postgres to fill them in.
    status: Mapped[str] = mapped_column(default="pending", server_default=text("'pending'"))
    run_after: Mapped[ts]                                   # now() default; backoff on retry
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    enqueued_at: Mapped[ts]
    claimed_at: Mapped[ts | None]
    __table_args__ = (
        # Coalesce: at most ONE pending job per session per topic (arch §5, "one pending
        # wake-up per live session").
        Index(
            "uq_queue_jobs_pending", "topic", "dedup_key",
            unique=True, postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_queue_jobs_claim", "topic", "status", "run_after", "id"),
    )
