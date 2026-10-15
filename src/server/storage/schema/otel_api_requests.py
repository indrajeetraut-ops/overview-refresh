"""otel_api_requests — the durable per-request spend ledger (otel-intake design §4.3).

One row per completed API request, parsed from the vendor's request event (claude_code:
`api_request`). This is the table that makes the two signal sources joinable: `request_id`
is the key JSONL also carries, so per-request cost truth lands on a session whose structure
the JSONL path described (§3). Kept, unlike `otel_metric_points` — ~45 GB/yr, partitioned
monthly when volume warrants it.

`(vendor, request_id)` is both the PK and the dedup key: request-id formats are vendor
namespaces, so the join key is the pair, and a redelivered event inserts zero rows.

**Nullability.** Only the identity of the row is required — `vendor`, `request_id`, `org_id`,
`received_at`. Everything the vendor mapping fills in is nullable, including fields §4.3
types as plain text/bigint (`session_id`, `model`, `input_tokens`, `output_tokens`). The
receiver runs one transaction per request body: a NOT NULL violation on one malformed record
would abort the whole batch, which contradicts §4.1's contract that a valid body with
unmapped records answers 200 + partialSuccess and never a 5xx. Validation of what a record
must carry lives in the receiver, where it can count and report; the table stays permissive.
§8 already records identity fields as "nullable and vendor-mapped" for vendors without a
session concept.
"""
from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, ts


class OtelApiRequest(Base):
    __tablename__ = "otel_api_requests"
    vendor: Mapped[str] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(primary_key=True)
    session_id: Mapped[str | None]         # vendor-scoped, via the vendor mapping
    org_id: Mapped[str]                    # from the token, never the payload
    user_key: Mapped[str | None]
    model: Mapped[str | None]
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cache_read_tokens: Mapped[int | None] = mapped_column(BigInteger)       # vendors without
    cache_creation_tokens: Mapped[int | None] = mapped_column(BigInteger)   # a cache concept
    # Only claude_code emits cost today; null → priced by the fitted estimator downstream,
    # provenance 'estimated'. Micros, not float: money never rides a binary fraction.
    cost_usd_micros: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    # Canonical concept: which subsystem issued the request; null = interactive. This is the
    # ~5.7% of session spend the JSONL path cannot see at all (§3).
    query_source: Mapped[str | None]
    prompt_id: Mapped[str | None]
    event_ts: Mapped[ts | None]            # the vendor mapping's timestamp attr, client clock
    attrs: Mapped[jsb]                     # vendor residue (claude_code: speed, effort, …)
    received_at: Mapped[ts] = mapped_column(index=True)   # ours — the true-up watermark key
    __table_args__ = (
        Index("ix_otel_api_requests_vendor_session", "vendor", "session_id"),
    )
