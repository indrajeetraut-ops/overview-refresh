"""session_info scalar columns -- promoted per-session values (api-latency plan Phase E)

Revision ID: e1a4c9f83b26
Revises: c4e8a2f1d7b3

Promotes the scalars Phase C extracted in SQL (config/queries/manager.yaml's guarded
fragments) to real columns, named exactly as `session_scalars.py`'s Python definitions and
mirrored one-to-one by the backfill below (D3's row contract, D6's one-Python-definition
rule). Ownership follows the writer (D5): `recoverable_*`/`reasoning_tokens`/`has_files`/
`session_type` are fold-owned (`persist_session_info` via `session_scalars.fold_scalars`);
`otel_vendor_cost_usd_micros` is the true-up's own column (`otel_trueup._write_cost_block`).

The backfill rewrites every existing row once (14.8k rows on `econ_synth`, 153 on `econ`);
autovacuum reclaims the old row versions. Downgrade drops the seven columns -- they are all
derived from `info`/`otel`/`files`, so nothing is lost.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e1a4c9f83b26'
down_revision = 'c4e8a2f1d7b3'
branch_labels = None
depends_on = None

#: Pure SQL, no application imports (every other migration here follows the same rule) --
#: mirrors `session_scalars.py` exactly; `tests/server/storage/test_session_scalars.py` pins
#: the two together. `session_type`'s ORDER BY is a scalar subquery, not a LATERAL join: a
#: LATERAL that returns zero rows (an empty/absent `segments` array) would silently skip that
#: row's UPDATE entirely, leaving every OTHER column un-backfilled for it too.
_BACKFILL_SQL = """
UPDATE session_info i SET
    otel_vendor_cost_usd_micros =
        CASE WHEN jsonb_typeof(i.otel->'cost_basis') = 'object' AND i.otel->'cost_basis' <> '{}'::jsonb
              AND COALESCE(CASE WHEN jsonb_typeof(i.otel->'cost_basis'->'estimated') = 'number'
                                THEN (i.otel->'cost_basis'->>'estimated')::numeric END, 0) = 0
              AND COALESCE(CASE WHEN jsonb_typeof(i.otel->'cost_basis'->'unknown') = 'number'
                                THEN (i.otel->'cost_basis'->>'unknown')::numeric END, 0) = 0
              AND jsonb_typeof(i.otel->'cost_usd_micros') = 'number'
             THEN (i.otel->>'cost_usd_micros')::bigint END,
    recoverable_resume_usd_micros =
        CASE WHEN jsonb_typeof(i.info->'resume_cost'->'total_recoverable_usd_micros') = 'number'
             THEN (i.info->'resume_cost'->>'total_recoverable_usd_micros')::bigint ELSE 0 END,
    recoverable_cache_bust_usd_micros =
        CASE WHEN jsonb_typeof(i.info->'cache_busts'->'total_usd_micros') = 'number'
             THEN (i.info->'cache_busts'->>'total_usd_micros')::bigint ELSE 0 END,
    recoverable_interrupted_usd_micros =
        CASE WHEN jsonb_typeof(i.info->'interrupted'->'total_usd_micros') = 'number'
             THEN (i.info->'interrupted'->>'total_usd_micros')::bigint ELSE 0 END,
    reasoning_tokens =
        CASE WHEN jsonb_typeof(i.info->'reasoning_tokens') = 'number'
             THEN (i.info->>'reasoning_tokens')::bigint END,
    has_files = (i.files IS NOT NULL AND i.files <> '{}'::jsonb),
    session_type = (
        SELECT e.seg->>'label'
        FROM jsonb_array_elements(CASE WHEN jsonb_typeof(i.info->'segments') = 'array'
                                       THEN i.info->'segments' ELSE '[]'::jsonb END)
             WITH ORDINALITY AS e(seg, ord)
        ORDER BY CASE jsonb_typeof(e.seg->'turns')
                   WHEN 'number' THEN (e.seg->>'turns')::numeric
                   WHEN 'array'  THEN jsonb_array_length(e.seg->'turns') ELSE 0 END DESC,
                 e.ord                             -- first max wins, like Python's max()
        LIMIT 1
    );
"""

_FOLD_OWNED_ZERO_DEFAULT = (
    'recoverable_resume_usd_micros', 'recoverable_cache_bust_usd_micros',
    'recoverable_interrupted_usd_micros',
)
_ALL_COLUMNS = (
    ('otel_vendor_cost_usd_micros', sa.BigInteger()),
    ('recoverable_resume_usd_micros', sa.BigInteger()),
    ('recoverable_cache_bust_usd_micros', sa.BigInteger()),
    ('recoverable_interrupted_usd_micros', sa.BigInteger()),
    ('reasoning_tokens', sa.BigInteger()),
    ('has_files', sa.Boolean()),
    ('session_type', sa.String()),
)


def upgrade() -> None:
    op.add_column('session_info',
                  sa.Column('otel_vendor_cost_usd_micros', sa.BigInteger(), nullable=True))
    for c in _FOLD_OWNED_ZERO_DEFAULT:
        op.add_column('session_info', sa.Column(c, sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column('session_info', sa.Column('reasoning_tokens', sa.BigInteger(), nullable=True))
    op.add_column('session_info', sa.Column('has_files', sa.Boolean(), nullable=False,
                                            server_default=sa.false()))
    op.add_column('session_info', sa.Column('session_type', sa.String(), nullable=True))
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    for name, _ in _ALL_COLUMNS:
        op.drop_column('session_info', name)
