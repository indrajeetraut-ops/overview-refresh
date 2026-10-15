"""otel_series — one row per metric stream (otel-intake design §4.3).

A "series" is a metric name plus its full label set. The label set is ~1 KB and repeats on
every export interval, so it is stored **once** here and the ~10⁷ datapoints/day in
`otel_metric_points` carry only a 64-char hash of it (D-OT3b).

`series_id` is that hash: sha256 over vendor + metric_name + canonically-sorted resource and
datapoint attributes. It is computed in the receiver, not here — the model is a dumb table
declaration (storage.md §3.1) and the hash is the receiver's idempotency key: the same series
re-exported produces the same id, so the insert is `ON CONFLICT DO NOTHING`.
"""
from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, ts


class OtelSeries(Base):
    __tablename__ = "otel_series"
    series_id: Mapped[str] = mapped_column(primary_key=True)  # sha256 hex, receiver-computed
    vendor: Mapped[str]                    # claude_code | gen_ai | ... | unknown (§4.1 step 4)
    metric_name: Mapped[str]               # vendor-native, verbatim; canonicalized at fold time
    metric_kind: Mapped[str]               # sum | gauge | histogram — from the OTLP payload
    temporality: Mapped[str]               # cumulative | delta — the fold branches on it (§4.4)
    org_id: Mapped[str]                    # from the authenticated org token, never the payload
    session_id: Mapped[str | None]         # via the vendor mapping's session attr
    user_key: Mapped[str | None]           # via the vendor mapping's identity preference list
    attrs: Mapped[jsb]                     # the full label set verbatim (must-ignore on read)
    first_seen_at: Mapped[ts]
    __table_args__ = (
        # Every fold and join is scoped (vendor, session_id) — ids are vendor namespaces
        # (§4.4). A standalone session_id index is deliberately absent: no query looks up a
        # session without knowing its vendor, and this table is written once per series.
        Index("ix_otel_series_vendor_session", "vendor", "session_id"),
    )
