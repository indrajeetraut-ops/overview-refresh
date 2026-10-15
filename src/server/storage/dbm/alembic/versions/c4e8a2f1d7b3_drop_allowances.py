"""drop allowances -- seat allowances are derived from team budgets (cost-based seats)

Revision ID: c4e8a2f1d7b3
Revises: b7c31f9a4e02

A seat's allowance is now its team's monthly budget / members, scaled to the viewed window
(docs/plan/2026-09-26-cost-based-seat-utilization.md), so the per-scope token allowance table
has no reader. Downgrade recreates it exactly as 8e59d8946ba4 created it; rows are not restored
(neither deployment held any when this shipped).
"""
from alembic import op
import sqlalchemy as sa

revision = 'c4e8a2f1d7b3'
down_revision = 'b7c31f9a4e02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table('allowances')


def downgrade() -> None:
    op.create_table(
        'allowances',
        sa.Column('scope_type', sa.String(), nullable=False),
        sa.Column('scope_id', sa.String(), nullable=False),
        sa.Column('limit_tokens', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('scope_type', 'scope_id'),
    )
