"""causal-graph: session_graph (per-session graphs) + session_tree (materialized family cache)

Revision ID: b7f2a9c14e30
Revises: d5b00c7d2016
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b7f2a9c14e30'
down_revision = 'd5b00c7d2016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'session_graph',
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('parent_session_id', sa.String(), nullable=True),
        sa.Column('graph_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('schema_version', sa.String(), nullable=False),
        sa.Column('built_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('session_id'),
    )
    op.create_index('ix_session_graph_parent_session_id', 'session_graph', ['parent_session_id'])
    op.create_table(
        'session_tree',
        sa.Column('master_session_id', sa.String(), nullable=False),
        sa.Column('tree_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('member_count', sa.Integer(), nullable=False),
        sa.Column('built_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('master_session_id'),
    )


def downgrade() -> None:
    op.drop_table('session_tree')
    op.drop_index('ix_session_graph_parent_session_id', table_name='session_graph')
    op.drop_table('session_graph')
