"""sessions — identity, lifecycle, raw normalized signal (storage.md §3.3)."""
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, jsl, ts


class Session(Base):
    __tablename__ = "sessions"
    session_id: Mapped[str] = mapped_column(primary_key=True)
    producer_id: Mapped[str] = mapped_column(index=True)           # plain column, no FK
    org_id: Mapped[str | None]
    user_id: Mapped[str | None] = mapped_column(index=True)        # plain column, no FK
    agent: Mapped[str]                                            # claude_code | codex | cursor | ...
    agent_version: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="live")           # live | ended | idle_closed
    started_at: Mapped[ts]
    last_event_at: Mapped[ts]
    ended_at: Mapped[ts | None]
    signal_blob: Mapped[jsb]                                      # raw normalized data
    info_blob: Mapped[jsb]                                        # debug / harness-dependent
    # subagent linkage — both directions, ingest-set; NOT session_info.subagents (intelligence)
    parent_session_id: Mapped[str | None] = mapped_column(index=True)  # child→parent, if this is a subagent
    subagents: Mapped[jsl]                                        # parent→child session_ids
