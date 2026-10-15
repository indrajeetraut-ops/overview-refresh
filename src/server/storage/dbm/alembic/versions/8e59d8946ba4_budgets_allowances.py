"""budgets, allowances -- manager API policy tables (F1)

Revision ID: 8e59d8946ba4
Revises: a1f4c7e29b05
"""
from alembic import op
import sqlalchemy as sa

revision = '8e59d8946ba4'
down_revision = 'a1f4c7e29b05'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'budgets',
        sa.Column('scope_type', sa.String(), nullable=False),
        sa.Column('scope_id', sa.String(), nullable=False),
        sa.Column('period', sa.String(), nullable=False),
        sa.Column('amount_usd', sa.Numeric(12, 2), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('scope_type', 'scope_id', 'period'),
    )

    op.create_table(
        'allowances',
        sa.Column('scope_type', sa.String(), nullable=False),
        sa.Column('scope_id', sa.String(), nullable=False),
        sa.Column('limit_tokens', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('scope_type', 'scope_id'),
    )


def downgrade() -> None:
    op.drop_table('allowances')
    op.drop_table('budgets')
