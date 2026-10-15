"""Shared models for the msging library.

The library is being rewritten against docs/design/event-messaging-design.md. The
previous transport models (Envelope, EventIn, AckResponse, ...) were removed with
the batch/spool implementation they belonged to.

ProducerRecord is kept: the producer-record store survives the redesign unchanged
(design doc §5.2), and src/server/storage/producer_store.py depends on it today.

The JSON files in schema/ are canonical; the models below mirror them, and
tests/common/msging/test_schema_parity.py enforces the mirror (D50).

payload and info are deliberately unschema'd — that is what keeps payload churn
out of the transport (design §7)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProducerRecord(BaseModel):
    producer_id: str
    key_prefix: str
    key_hash: str
    org_id: str | None = None
    user_id: str | None = None
    producer_type: str | None = None
    descriptor: dict = Field(default_factory=dict)
    registered_at: str
    last_seen_at: str | None = None
    revoked: bool = False


class Envelope(BaseModel):
    # extra="allow": receivers must ignore unknown keys (design §10.2)
    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0"
    event_id: str | None = None
    dedup: bool = False
    producer_id: str
    msg_id: int
    service_name: str
    created_at: str
    producer_desc: str | None = None
    info: dict = Field(default_factory=dict)
    payload: dict = Field(default_factory=dict)


class NotifyEv(BaseModel):
    model_config = ConfigDict(extra="allow")

    msg_id: int
    producer_id: str
    event_id: str | None = None
    code: str
    reason: str = ""
    info: dict = Field(default_factory=dict)
    ts: str


class MsgRecord(BaseModel):
    """The on-disk record. Stores the WHOLE envelope so a resend after restart
    rebuilds the request verbatim (design §3)."""

    msg_id: int
    envelope: Envelope
    created_at: str
    sent_at: str | None = None
    attempts: int = 0
    nack_reason: str | None = None


class SendResult(BaseModel):
    err_code: str
    reason: str = ""
    msg_id: int | None = None


class CloseResult(BaseModel):
    status: str          # drained | timeout
    unresolved: int
