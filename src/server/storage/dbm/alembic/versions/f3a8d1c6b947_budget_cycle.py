"""budget_cycle: the org's budget cadence (Overview refresh, Track A1)

Revision ID: f3a8d1c6b947
Revises: 231990ad3837

One new table, no data migration: no row means the default cycle (a recurring calendar month),
which is what every existing deployment already behaves as. The downgrade drops the table and
with it the org's configured cycle; `budgets` rows are untouched.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f3a8d1c6b947'
down_revision = '231990ad3837'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'budget_cycle',
        sa.Column('org_id', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('recurrence', sa.String(), nullable=False),
        sa.Column('sprint_weeks', sa.Integer(), nullable=True),
        sa.Column('anchor_date', sa.Date(), nullable=True),
        sa.Column('anchor_number', sa.Integer(), nullable=True),
        sa.Column('custom_end', sa.Date(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('org_id'),
    )


def downgrade() -> None:
    op.drop_table('budget_cycle')
