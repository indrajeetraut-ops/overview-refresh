"""users — session and PR owner (storage.md §3.5); also the directory's person row
(docs/design/identity-ingestion.md §3.1)."""
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, ts


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[str] = mapped_column(primary_key=True)
    email: Mapped[str | None]
    name: Mapped[str | None]
    github_user: Mapped[str | None] = mapped_column(index=True)
    # --- directory fields (IDENT §3.1); written only by server.directory.sync, precedence-gated
    # on `source_provider` (IDENT §3, implementation plan G3). `name` above predates this and is
    # left untouched -- telemetry never set it; the directory owns `display_name`.
    display_name: Mapped[str | None]
    # active | disabled. server-default so the existing telemetry INSERTs
    # (ingest.yaml `upsert_user_*`), which name columns explicitly, keep working unchanged.
    status: Mapped[str] = mapped_column(server_default="active")
    primary_team_id: Mapped[str | None]                 # default attribution when on many teams (IDENT §7)
    source_provider: Mapped[str | None]                 # csv | scim | telemetry | ... ; null = telemetry-seeded
    # The vendor account identity carried in OTel resource attributes (`user.account_uuid`,
    # config/otel/claude_code.yaml's identity.user_attrs) -- a different namespace than
    # `user_id`, which is whatever string the JSONL-side collector was told to use at upload
    # time. Declared alongside `user_id` at upload (utils/upload_claude_sessions.sh reads it
    # from the local Claude Code OAuth credentials), never inferred, so an OTel-only stub
    # session can resolve to the same `user_id` instead of surfacing the raw account UUID.
    account_uuid: Mapped[str | None] = mapped_column(index=True)
    # IANA zone name (e.g. "America/Los_Angeles"), browser-detected and self-service-set
    # (single_user's `set_timezone`) -- outside directory sync's own field set (like `name`
    # above), so a re-sync never clobbers it. Null means no preference recorded yet; every
    # bucketing call site that reads this treats null the same as today's UTC default.
    timezone: Mapped[str | None]
    info: Mapped[jsb]
    created_at: Mapped[ts]
