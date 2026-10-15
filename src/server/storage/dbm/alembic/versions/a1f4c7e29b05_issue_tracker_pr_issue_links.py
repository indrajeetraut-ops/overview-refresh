"""issue-tracker ingestion: pr_issue_links (plan H3)

Revision ID: a1f4c7e29b05
Revises: c4e1a9f27d3b
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1f4c7e29b05'
down_revision = 'c4e1a9f27d3b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'pr_issue_links',
        sa.Column('pr_artifact_id', sa.String(), nullable=False),
        sa.Column('tracker', sa.String(), nullable=False),
        sa.Column('project_key', sa.String(), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('issue_artifact_id', sa.String(), nullable=True),
        sa.Column('method', sa.String(), nullable=False),
        sa.Column('confidence', sa.Numeric(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('pr_artifact_id', 'tracker', 'project_key', 'number'),
    )
    op.create_index('ix_pr_issue_links_issue', 'pr_issue_links',
                    ['tracker', 'project_key', 'number'], unique=False)
    op.create_index(op.f('ix_pr_issue_links_issue_artifact_id'), 'pr_issue_links',
                    ['issue_artifact_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pr_issue_links_issue_artifact_id'), table_name='pr_issue_links')
    op.drop_index('ix_pr_issue_links_issue', table_name='pr_issue_links')
    op.drop_table('pr_issue_links')
