"""forecast_runs, forecast_scope_series: the precomputed dashboard forecast

Revision ID: 231990ad3837
Revises: e1a4c9f83b26

Two new tables, no data migration: they are filled by the forecast loader
(`server.storage.forecast_load`, `synthetic_load --forecast-export`). The downgrade drops them,
and with them any loaded run (a run is reproducible from its export file).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '231990ad3837'
down_revision = 'e1a4c9f83b26'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('forecast_runs',
    sa.Column('run_id', sa.String(), nullable=False),
    sa.Column('origin', sa.Date(), nullable=False),
    sa.Column('data_through', sa.Date(), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('loaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('run_id'),
    )
    op.create_table('forecast_scope_series',
    sa.Column('run_id', sa.String(), nullable=False),
    sa.Column('scope_type', sa.String(), nullable=False),
    sa.Column('scope_id', sa.String(), nullable=False),
    sa.Column('preset_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('run_id', 'scope_type', 'scope_id', 'preset_id'),
    )


def downgrade() -> None:
    op.drop_table('forecast_scope_series')
    op.drop_table('forecast_runs')
