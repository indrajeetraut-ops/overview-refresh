"""otel_watermarks — the true-up worker's progress marker (otel-intake design §4.4).

Arch §8.3 requires progress state to live in Postgres and to advance **in the same
transaction** as the work it describes; the mechanism is the module's choice. This is that
mechanism: one row per vendor, holding the `received_at` the fold has consumed up to. A crash
aborts the transaction and both the cost block and the advance vanish together, so restart's
normal query re-finds the work.

One row per vendor, not one globally: vendors are separate namespaces (D-OT6), and a new
vendor's first tick must not be told it has already folded everything.
"""
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, ts


class OtelWatermark(Base):
    __tablename__ = "otel_watermarks"
    vendor: Mapped[str] = mapped_column(primary_key=True)
    #: rows with received_at <= this have been folded. Advanced with a small lag behind the
    #: newest row seen, so a write that commits after the tick's read is re-covered next tick
    #: rather than skipped -- the fold is idempotent, so the overlap costs nothing.
    watermark: Mapped[ts]
    updated_at: Mapped[ts]
