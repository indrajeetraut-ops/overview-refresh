"""session_info — extracted intelligence, worker output (storage.md §3.4)."""
from decimal import Decimal

from sqlalchemy import BigInteger, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, jsb, jsl, ts


class SessionInfo(Base):
    __tablename__ = "session_info"
    session_id: Mapped[str] = mapped_column(primary_key=True)     # 1:1 with sessions; no FK
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    models: Mapped[jsb]
    tools: Mapped[jsb]
    files: Mapped[jsb]
    skills: Mapped[jsl]
    subagents: Mapped[jsb]  # {child_session_id: {...}} — replace-by-key rollup (ingest.md §7)
    pr_list: Mapped[jsl]
    turns: Mapped[jsl]  # per-turn records (worker plan §3-§4) — root-session only
    turn_count: Mapped[int] = mapped_column(default=0)       # real column: dashboard filter/sort
    long_turn_count: Mapped[int] = mapped_column(default=0)  # real column: "sessions with long turns"
    # Dashboard sort/filter scalars (dashboard-fold-implementation plan F1; DASH §7). JSONL-fold
    # outputs -- distinct from the true-up worker's cost_effective_usd_micros/datasource/
    # jsonl_lag_s columns, which land in a later phase and this fold must never write.
    tokens_total: Mapped[int] = mapped_column(default=0, index=True)   # promoted from info["tokens_total"]
    compaction_count: Mapped[int] = mapped_column(default=0, index=True)
    # Spawn count from this session's own events on a plain fold; len(subagents) once
    # rollup_from_family has run for this parent (F1) -- the two agree once every spawn's
    # child has arrived and folded.
    subagent_count: Mapped[int] = mapped_column(default=0, index=True)
    error_count: Mapped[int] = mapped_column(default=0, index=True)
    info: Mapped[jsb]
    # The OTel true-up worker's cost block (otel-intake design §4.4) -- vendor, canonical
    # measures, the per-request ledger and its provenance split.
    #
    # Its own column rather than a key in `info`, because it has its own writer. The JSONL
    # fold replaces `info` wholesale (persist_session_info, and fold() is contractually
    # required to return a COMPLETE SessionInfo), so a second worker's key living inside it
    # is deleted by the next arrival of the session it describes. A column makes the
    # ownership boundary structural instead of a convention two modules have to remember.
    otel: Mapped[jsb]
    # Promoted out of `otel` (dashboard-datasource-modes design §3.2): the true-up worker's own
    # columns, written in the same statement as `otel`, never by the JSONL fold
    # (persist_session_info deliberately omits them -- ground rule 3, phase PM). Nullable: a row
    # created by the JSONL fold before the true-up has ever ticked has no mode yet, and `None`
    # is the honest value for that, not a guessed `jsonl_only`. Real columns (not JSONB keys)
    # because the sessions list sorts/filters on cost and mode, and JSONB sorting is forbidden.
    cost_effective_usd_micros: Mapped[int | None] = mapped_column(BigInteger, index=True)
    datasource: Mapped[str | None] = mapped_column(index=True)   # both | jsonl_only | otel_only
    jsonl_lag_s: Mapped[int | None]
    # Promoted out of `info.repo` (dashboard plan §3), same precedent as tokens_total/
    # compaction_count/etc above -- this is what makes "distinct repos"/"cost by repo" a real
    # GROUP BY instead of a Python-side JSON dedup. The *primary* repo for this session
    # (highest-provenance evidence; see metrics.py's repo_info) -- a convenience column for
    # the common single-repo case, not a claim that a session can only ever touch one repo.
    # `info.repo.{cwd, git_branch, repo_key}` stays as the per-session display fields.
    repo_id: Mapped[str | None] = mapped_column(index=True)
    repo_id_provenance: Mapped[str | None]        # pr_link | remote | path | None
    created_at: Mapped[ts]
    updated_at: Mapped[ts]
    # api-latency plan Phase E: scalars promoted out of `info`/`files`, named exactly as
    # `session_scalars.py`'s Python definitions and the SQL fragments they replace
    # (config/queries/manager.yaml). Fold-owned -- derived from `info`/`files` by
    # `persist_session_info` via `session_scalars.fold_scalars`; never written by the true-up.
    # server_default (not just the ORM-side default), same reasoning as queue_jobs.status: a
    # raw named-query INSERT (synthetic_load's _upsert_table included) that predates these
    # columns omits them, relying on Postgres to fill them in.
    recoverable_resume_usd_micros: Mapped[int] = mapped_column(BigInteger, default=0,
                                                               server_default=text("0"))
    recoverable_cache_bust_usd_micros: Mapped[int] = mapped_column(BigInteger, default=0,
                                                                   server_default=text("0"))
    recoverable_interrupted_usd_micros: Mapped[int] = mapped_column(BigInteger, default=0,
                                                                     server_default=text("0"))
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger)   # None = never estimated
    has_files: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    session_type: Mapped[str | None]
    # True-up's own column, written in the same statement as `cost_effective_usd_micros`
    # (`otel_trueup._write_cost_block`) -- never the fold's.
    otel_vendor_cost_usd_micros: Mapped[int | None] = mapped_column(BigInteger)
