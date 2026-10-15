"""feedback — the one write the dashboard API makes (A8). Small and additive on purpose
(no-migration stance): a row per `POST /v1/sessions/{id}/feedback` call, nothing normalized
out into a foreign key yet.

`beat_ref` is a free-text reference to whatever the feedback is about within the session --
literally a beat's own `t0` timestamp once beats carry a stable per-instance id, but nothing
ships one yet (`info.segments[].beats` is a `{kind: count}` tally, not individually addressable
rows). Until then this doubles as a looser "what this feedback refers to" slot -- an
`insight_id` for a dismissed catalog card, say -- rather than blocking the whole endpoint on a
beat-addressing feature this pass doesn't build.
"""
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, ts


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(index=True)
    beat_ref: Mapped[str]
    verdict: Mapped[str]
    created_at: Mapped[ts]
