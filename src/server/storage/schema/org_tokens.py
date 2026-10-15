"""org_tokens — org-level ingest bearer keys for the OTLP routes (otel-intake design §4.5).

One managed-settings block enrolls a whole fleet (D-OT4), so the credential is scoped to the
org, not the machine: coarser than the producer keys of the JSONL path, and deliberately a
separate credential kind — one credential per path.

Only the sha256 of the key is stored. The plaintext exists exactly once, in the output of
`cli_db org-token issue`; it is replaced, never recovered (arch §18.1). Revocation flips the
flag rather than deleting the row, so a revoked key stays distinguishable from an unknown
one in the logs.

`org_id` on every stored OTLP row comes from the token resolved here, never from the
payload's `organization.id` (which is kept in `attrs` for reconciliation).
"""
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, ts


class OrgToken(Base):
    __tablename__ = "org_tokens"
    # The hash is the PK, so verification is one exact lookup — no prefix scan and no
    # disambiguation pass (the producers table needs those only because its key material
    # lives inside a JSONB blob).
    token_hash: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(index=True)   # "which tokens does this org hold"
    descriptor: Mapped[jsb]                           # free-form admin note: who/what it is for
    created_at: Mapped[ts]
    revoked: Mapped[bool] = mapped_column(default=False)
