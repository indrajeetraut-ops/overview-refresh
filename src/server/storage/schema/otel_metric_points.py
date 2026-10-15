"""otel_metric_points — append-only datapoint staging (otel-intake design §4.3).

The high-volume table: ~10⁷ rows/day at the 1,000-dev reference org. It is a **staging
buffer**, not a durable ledger — once the true-up worker has folded a session it serves only
debugging and recompute, and rows below the fold watermark are pruned past the retention
window (default 30 days, ~45 GB steady).

`(series_id, time_unix_nano)` is the dedup key and the idempotency contract: an OTel SDK
retry re-posts the identical datapoint, and the insert-if-absent produces zero rows.

`start_unix_nano` is the reset-epoch marker. Never sum datapoints of a cumulative series —
each value is a running total, and summing them inflated the reconciliation 150×. The
correct read is last-value per epoch, summed across epochs (§2, §4.4).
"""
from decimal import Decimal

from sqlalchemy import BigInteger, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, ts


class OtelMetricPoint(Base):
    __tablename__ = "otel_metric_points"
    # Plain column, no FK to otel_series (storage.md §3.1) — the receiver writes the series
    # row and its points in one transaction, so the reference is satisfied by construction
    # and an FK would only add a constraint check to the hottest insert path in the system.
    series_id: Mapped[str] = mapped_column(primary_key=True)
    time_unix_nano: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    start_unix_nano: Mapped[int] = mapped_column(BigInteger)   # reset-epoch marker (§2)
    # Unconstrained NUMERIC: token counters are integers in the millions, cost is a small
    # fraction of a dollar carried to ~15 significant digits. One exact type for both, and
    # the fold's sums stay exact.
    value: Mapped[Decimal] = mapped_column(Numeric)
    # Ours, not the client's clock: the prune key and the true-up watermark key (§4.4).
    received_at: Mapped[ts] = mapped_column(index=True)
